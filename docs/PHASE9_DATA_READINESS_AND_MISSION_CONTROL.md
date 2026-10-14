# Phase 9 — Data Readiness, Crash Recovery & Change-Driven Mission Control

Directive: answer three operational questions reliably — **is the data complete
and trustworthy enough to screen on**, **can an interrupted task be identified
and resumed**, and **can Mission Control show meaningful change without burning
CPU, I/O or network** — without adding concurrency for its own sake.

The governing rule, taken from the directive: *a maximum date is not
completeness*. Everything below is built around keeping those two facts apart.

## 0. Initial and final commit hashes

| Item | Value |
|------|-------|
| Branch | `main` |
| Starting commit | `6b9273b` (`docs: record the Phase 8 final commit…`), `== origin/main`, clean tree |
| Final commit | `7d0f585` (`feat(readiness): honest data-readiness on Mission Control; fix calendar bootstrap gap`) — recorded by the follow-up `docs:` commit |
| Phase 9 commits | `7d0f585` (readiness + recovery + UI) + the follow-up `docs:` commit |
| Full suite before | 1267 passed |
| Full suite after | **1304 passed**, 0 failed (+37) |
| CI (ubuntu) | **1283 passed / 21 skipped** (1283 + 21 = 1304) — `test (3.12)`, `lint`, `frontend-build` all green |
| CodeQL | green |

---

## 1. Actual readiness logic and source-of-truth date fields

`myra_app/data_readiness.py` is the single new module. It **reuses** existing
authorities rather than reimplementing them:

| Fact | Source of truth | Why not something else |
|------|-----------------|------------------------|
| `technical.latest_date` | `MAX(technical_data.date)` | A real market date, index-backed (`idx_tech_date_symbol`). Never a file mtime. |
| `technical.expected_date` | `tasks.watchdog._expected_trading_date()` | The battle-tested oracle: weekends, `NSE_HOLIDAYS_BASELINE`, the `market_calendar` DB-truth override, and the 18:30 IST publication cutoff. A second implementation would drift from the one that actually gates ingestion. |
| `coverage.symbols_at_latest` / `universe` | `COUNT(DISTINCT symbol)` per recent session | Completeness needs a denominator, not a max. |
| `source.*` | `eod2_sync.assess_source_readiness()` (Phase 8) | The function ingestion itself uses — Mission Control and the ingest can never disagree. |
| `fundamentals.reporting_date` | `MAX(quarterly_results.period_end)`, else `MAX(fundamentals.date)` | The period the figures *describe*. |
| `fundamentals.retrieval_date` | `MAX(fundamentals.last_updated)` | When *we* fetched. Deliberately labelled separately. |
| `ingestion.*` | `sync_log` row for `daily_ingest` | Existing record; no new table. |

### The calendar defect found and fixed

`market_calendar` marks real sessions as `is_trading_day = 0 "Likely holiday
(zero rows)"` — on the live database **every** trading day of Oct 2026 was so
marked, including 2026-10-12 which holds 3,200 rows. `is_trading_day()` already
lifts that heuristic **only when `technical_data` has rows for the date**.

That creates a bootstrap gap: for a session that is real, has landed upstream,
and has **not been ingested yet**, no database evidence exists, so the calendar
wins and the session is treated as a holiday. Before the fix, the expected date
was `2026-10-12` while the source already held `2026-10-14` — MYRA reported
itself fresh and the watchdog had no reason to fetch the missing sessions.

Fix: `daily_ingestor.has_source_evidence()` — if a real share of the upstream
per-symbol files end on that date, the exchange traded it. Wired into
`is_trading_day` as a second override arm, **only** for the heuristic reason, so
a named NSE holiday stays authoritative. Sampling is bounded (≤200 files).

Measured effect on the live databases: expected date `2026-10-12` → **`2026-10-14`**,
and `_is_db_stale()` flipped `False` → **`True`**.

---

## 2. How completeness is assessed, and what stays unknown

Coverage is `symbols_at_latest / universe`, where `universe` is the **largest**
symbol count across the last 3 sessions (a symbol that traded yesterday but not
today is still in the universe, so the denominator is never understated by a
halting). A session is complete at ≥80 % of universe, which matches the live
shape (3,200 of 3,423 ≈ 93 % on a normal day; the remainder are delisted,
suspended, SME-only and newly listed names that legitimately have no row).

**Deliberately reported as unknown / not recorded, never invented:**

* `expected_session_complete` is `None` when no denominator can be established.
* `expected_date` is `None` when the calendar cannot answer.
* `reporting_date` / `retrieval_date` are `None` when the source stores neither.
* A missing market date at all → state `unknown`, never `ready`.
* A missing *fundamental* source never downgrades technical readiness — an
  optional enrichment failure must not block independent ingestion.

States: `ready`, `waiting_for_eod`, `partial`, `stale`, `failed`, `unknown`,
each with a plain explanation. Ordering of the decision: read failure → no data →
no expected date → at/ahead of expected (then completeness decides) → behind
expected (ingestion failure > source unpublished > source published but
un-ingested > unknown).

The headline case this was built for, measured live on 2026-10-14:

```
latest_date      2026-10-12        (3,200 symbols on that session)
expected_date    2026-10-14
source           3,552 / 3,770 files at 2026-10-14, complete
state            partial
explanation      "Data is at 2026-10-12 but the EOD source already holds
                  2026-10-14; the remaining sessions have not been ingested yet."
```

Before the calendar fix the same call returned **`ready`**. That is precisely the
false assurance the directive warns against.

---

## 3. Enrichment interruption / recovery behaviour

Verified by test, using controlled fakes (no live Screener.in):

| Scenario | Behaviour |
|----------|-----------|
| Process stops halfway | Completed symbols survive — each commits per symbol as it finishes (`test_interrupted_run_keeps_completed_symbols`) |
| Some succeed, some time out | `updated` / `failed` counted; `partial=True`; `sync_log` records `partial` with both numbers, never a bare success |
| Upstream rate-limits (429) | Retried with bounded attempts, `Retry-After` honoured, and the **shared** pacer is pushed out so every worker slows together |
| Restart after commits | Symbols inside the 7-day cache are not re-fetched; stale ones are |
| Launched twice | In-process lock refuses; **new** cross-process lock refuses across OS processes |
| DB locked during a write | The symbol is counted as failed — never reported as an update |
| Status write fails | Committed rows stay committed; the run is not rolled back |
| Optional enrichment fails | Does not affect technical readiness or ingestion |

**New: cross-process duplicate protection.** Phase 8's guard was in-process
only, so `run_pipeline.py --enrich-screener` run while the API server was up
would have double-fetched every symbol. `myra_app/utils/task_lock.py` adds a
per-task pid lock following the same advisory rules as the existing
`scheduler_lock` (filesystem failure degrades to "assume free"; a lock left by a
dead process is reclaimed). A refusal returns
`reason="already_running_other_process"`.

---

## 4. EOD reconciliation behaviour

The Phase 8 per-symbol cutoff is what makes reconciliation work, and Phase 9
pins the end-to-end behaviour:

* A file that arrives after the first run is discovered on the next run
  (`test_late_arriving_csv_is_ingested_on_a_later_run`).
* Re-ingesting the same data inserts nothing and mutates nothing
  (`test_reingesting_the_same_data_changes_nothing`).
* A half-copied set followed by the complete set loses **no** symbol
  (`test_partial_then_complete_file_set_loses_nothing`).
* A file skipped because it was still being written reappears once it settles —
  the skip is not permanent (`test_still_being_written_file_is_skipped_then_ingested`).

The five outcomes the directive asks to distinguish are reported per run:
`rows_inserted` (processed), `skipped` (already processed, or a file still being
written), `rejected_rows` (rejected as invalid — close missing/≤0, mirrored to
`ingestion_rejects`), `readiness.reason` (pending input), and `error` (failed).

---

## 5. Live-status transport, and why it was selected

**No new transport was added.** The audit found the mechanism already correct:

* Backend: `GET /api/pipeline/events` is a working SSE stream
  (`myra_web/routes/pipeline.py`) with an initial snapshot, per-task stage /
  progress / terminal events, and a 15 s heartbeat so a client can tell a dead
  connection from a quiet task.
* Frontend: `usePipeline.ts` uses SSE as primary, a 5 s poll **only while SSE is
  down and the tab is visible**, exponential reconnect backoff (1 s → 30 s), and
  a heartbeat watchdog. `MissionControl` and `DataSync` are separate routes, so
  only one mounts the hook — there is never a second concurrent SSE connection.
* Execution state is in-memory: `control.get_status()` measured at **16 µs**,
  2.5 KB payload. Polling it is effectively free.
* Measured idle behaviour: subscribing and waiting emits **0 events** — an idle
  system does no work.

That is the directive's preferred option 1 (reuse what already works), so the
work here was a targeted defect fix, not a rewrite:

* **Duplicate stage events.** `_publish_stage` re-emitted an identical stage
  every time, driving an SSE frame and a client re-render for no new
  information. It now returns early when the stage text is unchanged; `note()`
  events are exempt because they are discrete operator-facing messages.

Readiness deliberately does **not** get a second SSE stream. It is a slow-changing
verdict, not a transition stream, and the directive warns against adding a
transport merely to look live. It rides the existing `/status` family as one
small, change-aware endpoint.

---

## 6. Polling intervals and backoff

`useDataReadiness` (the only new timer in the app):

| Situation | Interval | Rationale |
|-----------|----------|-----------|
| Idle | **30 s** | The verdict moves at most once per trading session; a faster poll is pure waste. |
| A pipeline run is active | **8 s** | So an operator watching a run sees the date move as it happens. |
| Tab hidden | **no request** | Re-schedules without fetching. |
| Consecutive errors | 5 s → 10 s → 20 s → 40 s → 60 s (cap) | Resets on the first success. |

One interval, one component, no per-page duplicate loops, no full-page reload.
A manual **Re-check** bypasses the fingerprint so it always returns a full
payload.

---

## 7. How unchanged updates avoid backend and frontend work

* **Endpoint:** `GET /api/pipeline/readiness?changed_only=1&since=<fingerprint>`
  returns `{"changed": false, "fingerprint": "..."}` — **221 bytes** — when
  nothing decision-bearing moved.
* **Fingerprint:** a stable hash of only the decision-bearing fields (state,
  latest, expected, coverage, universe, source date, fundamental reporting
  date). `generated_at` is excluded, so an identical verdict always produces an
  identical fingerprint.
* **Frontend:** on `changed: false` the hook does **not** call `setReadiness`, so
  React does not re-render the panel at all. Only the "assessed at HH:MM:SS"
  line updates.
* **Backend cache:** the expensive part is reading the tail of ~3,770 EOD CSVs
  (~3.2 s). That is memoised against a validity token of *(folder mtime, newest
  file mtime, meta.json mtime+size)* plus a 60 s TTL backstop. The newest-file
  mtime matters because **re-copying a session rewrites existing files without
  moving the directory mtime** — a token built only from the folder would have
  pinned a pre-copy verdict.
* **Explicit invalidation:** `sync_eod2_data` calls
  `invalidate_source_cache()` immediately after a successful insert, so the very
  next status read reflects the new folder rather than a value computed before
  the ingest.
* **No background thread** watches for changes; invalidation is explicit and
  the token is a cheap stat, not a poll.

---

## 8. Mission Control date/readiness display semantics

New `DataReadinessPanel`, rendered directly under the run header on the existing
Mission Control page. Four fact groups, no derived verdicts in the UI:

1. **Technical market data** — latest stored date, expected trading date,
   session coverage (`3,200 / 3,214 symbols`).
2. **EOD ingestion** — source files (`3,552 / 3,770 at 2026-10-14`), last
   successful ingest, last attempt with its status.
3. **Fundamentals** — *reporting period* and *last retrieved*, labelled
   separately, each with a tooltip stating explicitly that neither is the other.
4. **Overall** — the state chip and the backend's explanation, plus a footer
   showing when the verdict was last assessed and noting that updates arrive
   only on change.

Display rules honoured: a missing fact renders "Not recorded"/"Unknown", never a
guess; the state comes from the backend, never re-derived client-side; no
progress percentage is invented; existing manual task controls, layout and
navigation are unchanged.

---

## 9. Before/after measurements

All measured on the live workspace (read-only), 2026-10-14.

| Measurement | Before | After |
|-------------|--------|-------|
| Readiness verdict (the feature) | did not exist | `partial` — previously would have read `ready` |
| Expected trading date | `2026-10-12` (wrong) | `2026-10-14` |
| `_is_db_stale()` | `False` (no ingestion needed) | `True` (missing sessions detected) |
| `assess()` cost, no cache | n/a | ~3.4 s (3.2 s of it the EOD file scan) |
| `assess()` cost, warm (token valid) | n/a | **0.18 s** avg (min 0.16 / max 0.20) |
| Full readiness payload | n/a | 1,175 B |
| Unchanged response | n/a | 221 B, no re-render |
| `control.get_status()` | 16 µs | unchanged (16 µs) |
| Idle SSE events | 0 | 0 (unchanged) |
| Stage events for an unchanged stage | 1 per call | **0** |
| Duplicate-run protection | in-process only | in-process **+ cross-process** |

Honest caveat: the 0.18 s warm figure is dominated by a ~3.7 s *cold* first call
to the 2 GB technical database (OS page cache), which is normal for this
workspace and not re-measured per request. The numbers are wall-clock on this
machine, not a CI proxy.

---

## 10. Tests and database-integrity results

New:
* `tests/test_data_readiness.py` (**21**) — dates reported separately; a recent
  max date with a missing universe reads `partial`, not `ready`; complete session
  reads `ready`; waiting vs partial vs failed vs unknown (empty DB, missing
  expected date, unavailable database); source readiness reuse; **no rescan of an
  unchanged folder**; invalidation on file change and on explicit call; unstable
  file blocks readiness; fingerprint stable / changes on date and on coverage;
  the assessment writes nothing.
* `tests/test_recovery_reconciliation.py` (**16**) — the ten required scenarios.

**A pre-existing test-isolation defect was found and fixed.**
`tests/test_ingest_attempt_cooldown.py::test_hard_error_writes_no_success_but_does_throttle`
assigned `eod2_sync.sync_eod2_data`, `constants.USE_EOD2_DATA` and
`constants.ENABLE_DAILY_INGEST` **directly** and its `finally` restored only
`_mark_task_run` / `_mark_task_attempt`. The fake `sync_eod2_data` (returning
`error="boom"`) therefore leaked into **every subsequent test in the process**.
Phase 9 surfaced it as an order-dependent failure; it is now `monkeypatch`-based
and a regression test (`test_ingest_tests_do_not_leak_a_fake_eod_sync`) pins it.

Verification:

| Check | Result |
|-------|--------|
| Focused (readiness, recovery, eod2, screener, isolation, task-tracker, ingest, pipeline) | **223 passed** |
| **Full suite (local)** | **1304 passed**, 0 failed |
| `black` 26.10.1 / `flake8` | clean on all touched files |
| `npm run lint` (tsc --noEmit) | clean |
| `npm run build` | success (pre-existing chunk-size warnings only) |
| Performance guard | **268 violations / 0 CRITICAL** — unchanged baseline; `data_readiness.py` and `task_lock.py` add none |
| Live DBs after the full run | `technical_data` 8,009,058 rows, max date 2026-10-12, `PRAGMA quick_check: ok`; `fundamentals` 3,948; `metadata` 17 — **unchanged** |

Phase 7/8 protections preserved: `test_task_offload.py` still uses
`fixture_db_dir`; the WAL-aware live-DB guard and `DB_DIR` isolation tests are
untouched and green; the shared task-tracker lock is unchanged; no pytest-xdist;
readiness is strictly read-only (`mode=ro`).

CI/CodeQL: run `38610543418` (CI) — `test (3.12)` **1283 passed / 21 skipped**,
`lint` and `frontend-build` green, `audit` skipped (pre-existing); CodeQL run
`38610542589` green.

---

## 11. Known limitations and recommended next phase

1. **Readiness does not auto-repair.** It reports "partial" and leaves recovery
   to the existing task controls, as directed. The operator still has to press
   *Run Daily Ingest*.
2. **Coverage is universe-relative, not absolute.** A symbol that legitimately
   delisted reduces the numerator permanently; the 93 % ceiling is the real-world
   shape, not a defect. A true "expected universe" would need a listing master
   that does not exist today.
3. **`market_calendar` remains a "dates seen" table**, not a real exchange
   calendar. The source-evidence arm compensates for the sessions we have, but a
   day where the exchange traded *and* the upstream folder has not landed would
   still be reported as waiting. Fixing the calendar itself is a data task, not
   a code one.
4. **Fundamental freshness is still coarse.** `fundamentals` has no publication
   date column; the reporting period comes from `quarterly_results.period_end`.
   A per-row publication date would need a schema change and a backfill — out of
   scope here, and not invented.
5. **`stale` is defined but currently unreachable** — a stored date cannot be
   "stale" while it is at or ahead of the expected date, and behind it is
   `waiting`/`partial`/`failed`. It is kept in the vocabulary for clarity and to
   avoid a future state silently mapping to a wrong one.
6. **CPU during idle monitoring was not measured** (no reliable counter in this
   environment). The proxy actually tested: 0 events emitted while idle, 0
   requests while the tab is hidden, and one bounded timer per mounted view.
7. **Next phase recommendation:** fund the data, not the monitor — repair
   `market_calendar` from a real holiday list, add a per-row fundamentals
   publication date, then make the ingest task self-trigger on a `partial`
   readiness verdict (gated and once-per-session) so the common case needs no
   operator action.
