# Phase 31 — Parked Fundamentals Cohort Audit

**Verdict: `MULTIPLE FAILURE CLASSES CONFIRMED — TARGETED FOLLOW-UP NEEDED`**

Read-only audit. **No external request, no refresh, no production write, no
scheduled task, no parser change.**

---

## 1. Baseline and runtime preflight

| Check | Result |
|---|---|
| HEAD | `4d3be88` (`origin/main`, tree clean, synchronized) |
| Phase 30 verdict | `LABEL-ONLY FIRST ROW OBSERVED — PARSER CHANGE NOT JUSTIFIED` |
| CI / CodeQL at baseline | green (verified in Phase 30) |
| Bandit / dead `python` processes | 0 |
| Listeners on 8000 / 3000 | none |
| Sum of `*.db-wal` sizes | 0 bytes |
| Live writer active? | **No** — so DB reads and focused tests were safe |

Reports re-read: Phases 26, 27, 28, 29, 30. Code re-read: `fundamentals_sync.py`
(parking, retry, progress), `fundamental_manager.py` (persist, COALESCE,
undated-row guard, error collapse), `fetcher.py` (source order, status isolation,
`last_source_errors`), `normalizer.py` (per-source field mapping),
`source_priority.py`, `task_utils.py`.

**Inspectable read-only:** `myra_metadata.db` (`metadata`, `task_registry`,
`sync_log`), `myra_valuation.db` (`fundamentals`, `quarterly_results`,
`fundamental_source_freshness`), `myra_technical.db` (`technical_data`), and
`logs/` (files only). Every connection used a `mode=ro` URI. No journal-mode
change, no checkpoint, no truncation, no schema statement.

**A note on the word "parked".** This audit measured the cohort rather than
trusting the label, and the label is wrong — see §3.

---

## 2. Cohort size and authoritative source

The authoritative record is the **`fundamentals_park_state` key in the
`metadata` table of `myra_metadata.db`** (a JSON object; `PARK_STATE_KEY`,
`fundamentals_sync.py:47`). It is not a table.

| Measure | Value |
|---|---|
| Entries in park state | **119** (matches the expected count) |
| Symbols with `parked_until` set | **0** |
| Attempt-count distribution (`fails`) | 1 → 61 · 2 → 50 · 3 → 2 · 4 → 6 · 5+ → **0** |
| `PARK_AFTER_FAILURES` threshold | 5 |
| Distinct `last_attempt` values | **1** (`2026-10-18T16:01:21.024086+00:00` — the run's *start* time) |
| With stored failure reason | **0** |
| All entries from one run? | Yes — one `start_time`, one `last_attempt` |
| Symbols with ≥1 quarterly row | 1 |
| Symbols with 0 quarterly rows | **118** |
| Symbols with a `fundamentals` row | **119 / 119** |
| Symbols still tradable (price in last 7 sessions) | **119 / 119** |
| SME-suffixed (`*_SME`) | 37 (31.1%) |

**Two metadata problems found immediately, both CONFIRMED:**

1. **Nothing is parked.** `is_parked()` requires `parked_until`
   (`fundamentals_sync.py:138-149`), and **no entry has it** — the maximum
   `fails` is 4, one short of the threshold. All 119 are *failure-tracked*, not
   parked. They will be re-queued on the next run and the run's own
   `Parked: N` counter would print `0`. Phases 26–30 equated "present in park
   state" (119) with "parked" (a threshold state that was never reached). That
   inference was wrong, and it is corrected here.
2. **The progress record is stale and self-contradictory** —
   `status: in_progress`, `start_time` 2026-10-18, `last_sync_date:
   **2026-04-28** (five months old), `last_processed_symbol: CLAYCRAFT_SME`,
   and `total_symbols: 72`, a field **no current code writes** (it is a legacy
   remnant: `_load_progress` does not declare it, and 72 contradicts the 348
   attempts recorded in the same object).

Three records disagree about the last fundamentals run:

| Record | Claims |
|---|---|
| `fundamentals_sync_status` | 2026-10-18, `in_progress`, 229 updated / 119 failed |
| `sync_log.fundamentals_sync` | **2026-10-17**, `cancelled` |
| `pipeline_run_state` | 2026-10-18, `cancelled`, `fundamentals_sync → cancelled` |

`sync_log` is written by `task_utils` (background-orchestrator path) and was not
touched by the 10-18 pipeline run, so it silently reports a run that is not the
latest. CONFIRMED.

---

## 3. Failure categories — what the evidence supports

**The per-symbol cause is unavailable for every symbol.** `record_failure`
persists only `fails`, `last_attempt` and `parked_until`
(`fundamentals_sync.py:152-164`). The reason lives in `mgr.last_error` /
`fetcher.last_source_errors` and is only ever `print`ed
(`fundamentals_sync.py:417`) — never `logger`ed, never stored. **No log captured
that run:** every file under `logs/` stops at 2026-09-23 (the refresh ran
2026-10-18), and zero `[FAIL]` lines exist anywhere in `logs/`.

So the primary per-symbol table is honest and short:

| Primary category | Symbols | Evidence | Confidence |
|---|---|---|---|
| `UNKNOWN — insufficient historical evidence` | **119** | Reason never persisted; no captured stdout; attempt history overwritten | **CONFIRMED** (the *gap*) / **UNKNOWN** (the *cause*) |

Unclassified symbols explicitly accounted for: **119 of 119**. No symbol is given
a fabricated cause.

What the surviving state *does* support is a set of **system-level** classes.
These are not per-symbol diagnoses and are labelled as such:

| # | Class | Evidence | Confidence | Actionable offline? |
|---|---|---|---|---|
| A | **Hard fetch failure, cause unrecorded** — 119 of 348 attempts (34.2%) | `record_failure` ran for all 119 in this run (single `last_attempt`); `park_state.pop()` on success proves none succeeded | **SUPPORTED** (that they failed) / **UNKNOWN** (why) | No |
| B | **Fallback path is structurally unable to persist** | `_fetch_google` initialises every row as `report_date="Q-<n>"` (`fetcher.py:1346`) and never replaces it; the undated-row guard rejects exactly that shape (`_PLACEHOLDER_REPORT_DATE`, `fundamental_manager.py:29,186-196`) | **CONFIRMED** | Yes — code-level |
| C | **Successful-but-incomplete (partial fill)** | 2,910 backlog symbols carry `last_updated=2026-10-17` (only `fundamental_manager` writes that column) yet still miss a required metric; 51 more were written on 10-18 and are still incomplete | **CONFIRMED** (state) / **UNKNOWN** (upstream absence vs parse miss) | Partly |
| D | **Completion criterion may be unsatisfiable** | ROE is missing in 3,075 / 3,322 backlog symbols (92.6%), and Screener.in is the only ROE source | **SUPPORTED** | No |
| E | **Run truncated by cancellation** | `pipeline_run_state`: started 21:31:20 IST, finished 22:01:21, `market_cap_sync` timed out at 1800 s; 348 of ~3,551 symbols attempted (10.5%), 3,007 (90.5%) never reached | **CONFIRMED** | n/a |

**Class B is the most consequential finding and needs no network to prove it.**
Every row Google produces carries a `Q-1..Q-4` placeholder date, which
`fetch_fundamentals` filters out before writing, leaving `dated` empty and
returning `False` with `"source returned only undated rows (google fallback)"`.
There is therefore **no code path by which the Google fallback can persist
anything.** "Multi-source fallback" is effectively single-source: when
Screener.in fails, the symbol cannot be rescued. `normalize()` maps `roe` for
both sources (`normalizer.py:72,105`), so the loss is in Google's page payload,
not in normalization.

### Concentration — tested against a baseline, not assumed

The queue is *defined* by a missing metric, so a naive profile would "discover"
that these symbols lack data. All comparisons below are against controls.

| Signal | Cohort | Control | Verdict |
|---|---|---|---|
| **ROE missing** | 76.5% | backlog 92.6%, window 78.4% | **NOT enriched — hypothesis refuted.** The cohort is *less* ROE-deficient than the pool it came from. |
| **`market_cap`-only missing** | 24% | window 9%, backlog 6% | **Enriched ~2.7×** (CONFIRMED as a pattern, cause UNKNOWN) |
| **SME suffix** | 31.1% | backlog 8.9% (**3.5×**), window ≤ last-processed symbol 13.3% (**2.3×**) | **Enriched under both controls** — direction robust, magnitude uncertain |
| **Tradable** | 100% | — | **Not a cause.** Queue order is tradable-first (`fundamentals_sync.py:333`); this is scheduling, not failure |
| **By source** | n/a | only 4 of 119 have a `fundamental_source_freshness` row (all `yfinance`, 2026-10-11) | Cannot measure — success-side provenance is sparse (665 rows for 3,951 symbols) |
| **By HTTP outcome** | 0 recorded | — | Cannot measure |

Within the alphabetical window, SME symbols failed at ~88% (37 of 42) versus
~30% (82 of 273) for others. This is **corroborating but order-dependent** —
see §4 — so it is reported as indicative, not as a rate.

---

## 4. Reconciliation, and one inconsistency I could not resolve

**Reconciled:** 229 + 119 = 348 attempts. `last_updated = 2026-10-18` exists on
**exactly 229 rows**, matching `updated_count`, of which 178 became complete and
51 remain incomplete. All 119 failures are tradable, so none is a delisting
case. All 51 symbols written on 10-18 are tradable and none is non-tradable
(P ≈ 2×10⁻⁶ under an alphabetical sweep) → **the queue was tradability-ordered
during that run**, so the non-tradable tail was never reached.

**Not reconciled:** the counters say 348 attempts, but `last_processed_symbol =
CLAYCRAFT_SME` sits at index 169 of the 2,543 tradable-backlog symbols, and only
170 tradable symbols sort at or before it. Two candidate explanations —
(a) a second concurrent invocation wrote the same progress key, or (b) the
counter reset was not applied — and **neither is provable from local evidence**.
This is recorded as an open bookkeeping defect, not silently smoothed over.
It also means the window-based denominators above carry an unresolved ambiguity,
which is why the SME finding is anchored on the order-independent comparison.

`last_updated` must not be read as freshness: 3,131 rows share `2026-10-17`,
which is a bulk stamp from a single large run, and 2,910 of those symbols are
*still incomplete*. The column records "we last wrote this row", not "this
metric is current".

---

## 5. Common failure paths and confirmed defects

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Google fallback can never persist (§3 Class B) | **High** — halves the promised source coverage | CONFIRMED, unfixed |
| 2 | Per-symbol failure reason never persisted; only a `print` | **High** — makes this audit's central question unanswerable | CONFIRMED, unfixed |
| 3 | "119 parked" was an inference, not a state; 0 meet the threshold | **Medium** — misreported health across five phases | CONFIRMED, corrected here |
| 4 | `last_attempt` is the run *start* time and overwrites prior attempts, so attempt history is a bare counter | **Medium** | CONFIRMED |
| 5 | Progress record left `in_progress` with a 5-month-old `last_sync_date` and a legacy `total_symbols: 72`; no heartbeat to distinguish "resumable" from "dead" | **Medium** — misleading operator/UI state | CONFIRMED |
| 6 | Three records disagree on the last fundamentals run (`sync_log` vs `fundamentals_sync_status` vs `pipeline_run_state`) | **Medium** | CONFIRMED |
| 7 | Partial fills re-queue indefinitely: success pops the park entry, so a symbol that always returns *some* data never parks and never completes | **Medium** — 2,910 symbols | CONFIRMED (state) |
| 8 | Counter/position inconsistency of §4 | **Low–Medium** | Open, unresolvable read-only |
| 9 | `myra_app/utils/fundamentals_sync.py.OLD` (149 lines) is **tracked** in git since `110af23` | **Low** — hygiene | CONFIRMED |
| 10 | Test-suite fragility: the DB-isolation guard fails in arbitrary multi-file batches (`myra_app.task_tracker` keeps a stale `DB_DIR`); passes in isolation and in the full suite | **Low** — pre-existing, not caused by this audit | CONFIRMED, reported not fixed |

Phase 26's status-reset and Google-status-isolation fixes are **intact and were
not weakened** (`fetcher.py:1167-1175` resets `last_source_errors`,
`last_http_status`, `last_page_note` per call). No current defect was found that
collapses *different* source failures into one message — the message is
source-attributed; it is simply never written down.

---

## 6. Data-preservation findings

Read-only verification, values identical before and after the audit:

| Invariant | Result |
|---|---|
| `quarterly_results` rows / symbols / distinct periods | 10,798 / 884 / 79 |
| `report_date` NULL | **0** |
| `report_date` blank | **0** |
| Placeholder `Q-%` dates | **0** |
| Period range | `Dec 2005` … `Sep 2026` |
| `fundamentals` rows | 3,951 |
| AAATECH quarterly rows | 0 (still parked)/tracked, unchanged |
| AAATECH `last_updated` | `2026-10-17T01:46:29.474666` (unchanged) |
| COALESCE protection | intact — 13 columns, `fundamental_manager.py:290-305` |
| Undated-row guard | intact — `_PLACEHOLDER_REPORT_DATE`, `fundamental_manager.py:29` |
| Period vs retrieval separation | preserved — `report_date`/`period_end` are periods; `last_updated` is retrieval |
| Park state | 119 entries, byte-identical JSON |
| WAL files | 0 bytes after the audit |

"Recent `last_updated`" is explicitly **not** treated as freshness anywhere in
this report (§4).

---

## 7. Smallest justified next action

Ranked by evidence, impact and risk:

1. **Persist per-attempt failure telemetry (recommended, smallest, zero behaviour
   change).** *Design proposal — awaiting authorization, not implemented:*
   - Append one row per failed attempt to a new `fundamentals_failure_log`
     table in `myra_metadata.db`: `symbol, attempted_at, http_status, source,
     reason, fails_at_attempt`. One `INSERT`, no reads, no change to control flow.
   - `reason` reuses the **existing, already credential-free**
     `fetcher.last_source_errors` (`{"screener_in": "HTTP 403", ...}`) that Phase
     26 built. No new parsing, no new requests, no new dependency.
   - Cap growth (e.g. keep the last N rows per symbol) so the metadata DB cannot
     balloon.
   - Effect: the *next* cohort audit answers the question this one could not,
     for the cost of one insert per failure.

2. **Fix progress truthfulness.** Add `finished_at` + a `heartbeat_at` and stamp
   a terminal status; drop the legacy `total_symbols`; write `sync_log` from the
   pipeline path too so the three records agree. Low risk, operator-visible.

3. **Decide the Google fallback** (either make it produce a real period or stop
   spending a request on a path that cannot persist). **Requires authorization** —
   it changes fallback behaviour, which this brief forbids by default.

4. **Do not** run a broad refresh to obtain better messages. It would re-fetch
   ~3,300 symbols against a source that just rate-limited, to learn what one
   insert per failure would have told us.

Ranked *against* each other: (1) is strictly cheaper than a refresh and its only
side effect is disk; (2) is needed for anyone to trust the status panel; (3)
is a real defect but carries behavioural risk and is therefore last.

---

## 8. Tests, Git status and CI

- **Source code changed: none.** The audit produced a report only. Per the
  brief, no tests were added: the confirmed defects (dead fallback, missing
  telemetry) cannot be fixed inside the approved read-only scope, and writing a
  test that asserts the dead-fallback behaviour would lock a bug in.
- **Focused tests run** (mocked, no network):
  `tests/test_fundamentals_sync_helpers.py`, `test_sync_fundamentals_resume.py`,
  `test_fundamental_sync_guard.py`, `test_fundamental_manager.py`,
  `test_fundamentals_source_classification.py`, `test_fundamentals_diagnostics.py`
  → **46 passed, 2 errors**.
- The 2 errors are **pre-existing test isolation artefacts**, not product
  regressions: both files pass in isolation (`2 passed`), and the failure is the
  fail-closed guard `TEST DB ISOLATION BREACH (fixture setup)` reporting
  `myra_app.task_tracker` still resolving `DB_DIR` into a previous test's temp
  directory. Reported per repo policy, not fixed here.
- Commit: this report (and its `session-history.md` entry) only.
- Final HEAD, sync state and CI/CodeQL results are recorded in the session
  summary accompanying this commit; nothing is claimed that was not verified.

---

## 9. Explicit confirmation

- **No external HTTP request was issued** at any point in this audit — not to
  Screener.in, not to Google, not to any source. Every finding came from local
  databases, local logs, local git history, and code reading.
- **No fundamentals refresh, retry, backfill, enrichment, lake build, or
  scheduler job was started.**
- **No production write of any kind** — all DB access used `mode=ro`; no
  journal-mode change, no checkpoint, no WAL manipulation, no schema change, no
  migration. Row counts and park-state JSON are byte-identical before and after.
- **No production parser, fetcher, source priority, scoring, ranking, growth
  default, freshness threshold, date validation, or live-DB guard was modified.**
  `MYRA_V25_RANKING_INPUTS` remains unset; `live_db_guard` is untouched.
- **No service was started or stopped.** Zero MYRA writers and zero `python`
  processes were running throughout.
