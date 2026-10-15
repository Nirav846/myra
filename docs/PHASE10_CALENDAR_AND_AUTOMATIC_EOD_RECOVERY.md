# Phase 10 — Authoritative NSE Trading Calendar & Automatic EOD Recovery

## Why this phase exists

`daily_ingestor` wrote a row into `market_calendar` the first time it ingested a
date and found **zero** `technical_data` rows for it, naming it
`"Likely holiday (zero rows)"` and setting `is_trading_day = 0`.

That conflated two completely different facts:

- *"NSE did not trade on this day"*, and
- *"I have not received this day's data yet."*

A day is not a holiday because we have no rows for it. On a morning-after-EOD
window — the exact moment MYRA is supposed to catch up — the source is routinely
incomplete, so the system was permanently writing real trading sessions into its
own calendar as holidays. Those rows are authoritative to
`is_trading_day()`, so the damage is self-reinforcing: once a date is marked
closed, the ingest path stops fetching it, so no rows ever arrive to contradict
the mistake.

Measured on the live calendar before the repair:

| Metric | Value |
|---|---|
| `market_calendar` rows | 4,383 |
| Rows named `"Likely holiday (zero rows)"` | 215 |
| Of those, dates that **actually had `technical_data`** | 24 |
| Weekdays wrongly suppressed | 78 |

The technical database's latest date was `2026-10-12` while the expected session
was `2026-10-14` — the misclassification was suppressing exactly the catch-up the
watchdog exists to perform.

## The official source

MYRA does not guess and does not maintain its own holiday list. The
authoritative publication is already vendored in the repo and is refreshed
automatically by the eod2 data pipeline:

- `eod2/src/eod2_data/meta.json` — published annually by
  `nse.holidays(type=nse.HOLIDAY_TRADING)`, filtered to the **`CM`** (equity)
  segment. Keys are `DD-MON-YYYY`.
- `special_sessions` — exceptional **live** sessions announced by NSE circulars
  (Muhurat trading, and the like).
- `special_sessions_last_update` — freshness of the special-session list.

At the time of writing the file carries year **2026**, data-version 3.4,
20 official holidays and 1 special session (`2026-02-01`).

The eod2 tree is read-only reference material and was not modified.

## `myra_app/nse_calendar.py`

A single place that answers *"was this date an NSE CM session?"*, with
provenance attached to every verdict.

```python
classify(day) -> {"state", "reason", "provenance", "verified"}
```

| State | Meaning |
|---|---|
| `trading` | Weekday absent from the official holiday list for a verified year |
| `closed` | Official holiday, or weekend within a verified year |
| `special` | Published live session (NSE circular) |
| `unknown` | No official verdict available — **never guessed** |

### Precedence

1. **Special session** — wins over everything, including a same-day holiday entry.
2. **Official holiday** for that date.
3. **Coverage check.** If the year's list is unpublished → `unknown`.
4. **Weekend** — only *after* the coverage check.
5. **Trading day** — weekday absent from the official list.

Steps 3 and 4 are ordered the way they are deliberately: NSE does publish
**weekend** live sessions (2026-02-01 is a Sunday), so for a year we hold no
publication for, a weekend is *unknown*, not confidently "closed". Treating an
unpublished year as "closed" would reintroduce the original bug for every future
date.

### Writing to the calendar

- A heuristic row is **never** rewritten as a holiday. If the official calendar
  says the date is a session, `_mark_calendar_unproven()` records it as trading
  (annotated `(reconciled from heuristic)`) rather than closing it.
- Inserts use `INSERT OR IGNORE`, so a concurrent writer's row wins.
- The legacy `holiday_skip` path no longer uses `INSERT OR REPLACE`, which used
  to silently overwrite a curated record.
- Curated/named records are never touched by reconciliation.

## Reconciling history

`tools/calendar_reconcile.py` — dry run by default, `--apply` to write.

```
python tools/calendar_reconcile.py --date 2026-10-13 --date 2026-10-02
python tools/calendar_reconcile.py                 # dry run
python tools/calendar_reconcile.py --apply
```

Safety properties, enforced in code:

- only rows whose `holiday_name` is exactly the zero-row heuristic are eligible;
- the `UPDATE` re-checks `holiday_name` in its `WHERE`, so a row repaired since
  the scan cannot be clobbered;
- unverifiable dates are reported, never guessed;
- **nothing is ever deleted** and the operation is idempotent.

### Applied result

| Metric | Before | After |
|---|---|---|
| `market_calendar` rows | 4,383 | 4,383 (unchanged — no deletes) |
| Trading days | 2,891 | 2,969 |
| Heuristic records remaining | 215 | 127 |
| Heuristic → confirmed trading | — | 78 |
| Heuristic → confirmed official holiday | — | 10 |

`2026-10-13` and `2026-10-14` are now trading sessions; the expected trading date
resolves to `2026-10-14`.

**127 records remain unclassified.** They are pre-2026 dates for which no official
publication is available in the repo. They are left exactly as they were: a
guessed verdict would be worse than a visibly unverified one.

## Calendar truth vs. data availability

These are separate states and are never collapsed:

| Question | Source of truth |
|---|---|
| *Was this an NSE session?* | The official publication (`nse_calendar`) |
| *Do we have that session's data?* | `technical_data` + source readiness (`data_readiness`) |

A missing `technical_data` row is never consulted by the classifier. That was the
defect.

## Automatic EOD recovery — `myra_app/eod_recovery.py`

The watchdog already existed and already ran every 60 seconds. What was missing
was not another driver but an explicit, bounded statement of *when* an attempt is
legitimate. That policy is now a standalone module with **no scheduler, thread or
polling of its own** — the existing watchdog remains the only driver.

### Eligibility (all required)

1. The expected trading date is a **confirmed** session.
2. The database is actually behind that session.
3. The after-close publication window has opened.
4. The shared attempt cooldown has elapsed.
5. The per-input-state retry budget is not exhausted.

An official holiday or an unverifiable calendar never triggers recovery. A
missing source file is not a reason to stop looking either — it is a reason to
wait, and the cooldown does the waiting.

### Two bounds, because they answer different questions

Attempts are keyed by an **input fingerprint** — the expected session plus a
digest of the source state (newest source date, file counts):

| Bound | Question it answers | Behaviour |
|---|---|---|
| **Cooldown** (`STALE_RETRY_COOLDOWN_MINUTES`) | *How soon again?* | Spaces attempts apart; shared with the ingest task so two drivers cannot both fire |
| **Retry budget** (`MAX_ATTEMPTS_PER_INPUT = 6`) | *How many times, ever, on this?* | Hard cap on one unchanged input state |

A cooldown alone would re-import an endlessly incomplete source every 30 minutes
forever. The budget stops that. Because it is keyed by the fingerprint, a late
arriving file produces a **new** fingerprint and therefore a fresh budget — so a
previous failure can never permanently wedge recovery. That reset-on-meaningful-
change property is pinned by
`test_an_exhausted_budget_is_reset_by_a_late_arriving_file`.

The budget is stored in the existing `metadata` sidecar under
`eod_auto_recovery_budget:<fingerprint>` — cross-process, restart-safe, no schema
change. A corrupt row degrades to "zero attempts used" rather than raising.

### Honest outcomes

`tasks.ingest.run()` now returns whether it actually executed. If another driver
held the ingest lock, the watchdog reports `skipped_already_running` and **refunds**
the budget it optimistically spent, rather than charging for work it did not do.

Success is only claimed when the stored date actually advanced. `stale_catchup`
is marked only on real progress.

## The outage that proved it: sessions sitting in the source, un-ingested

After the calendar repair the system still did not ingest. On the morning of
**2026-10-15 (09:2x IST)**:

```
db_latest            : 2026-10-12
expected             : 2026-10-14
source last_date     : 2026-10-14   (ready, 3552/3770 files)
readiness            : partial
  "Data is at 2026-10-12 but the EOD source already holds 2026-10-14;
   the remaining sessions have not been ingested."
```

`eod_recovery.evaluate()` reported **`eligible=True`** — the policy was correct.
The data was blocked by **three separate wall-clock guards**, each of which
answers "has *today's* bhavcopy landed?" and each of which was applied to
sessions the source had **already published**:

| # | Location | Guard | Effect |
|---|---|---|---|
| 1 | `eod_recovery.evaluate` | `after_publication_window` | Deferred catch-up to 18:30 IST today |
| 2 | `tasks/ingest._run_ingest_locked` | `if ist_now.hour < 18` | `"Market data not yet available (IST: 09:21). Skipping."` |
| 3 | `tasks/ingest._run_ingest_locked` | `if ist_now.weekday() >= 5` | would suppress Friday's catch-up on a Saturday |

All three conflate *"today's session is not published yet"* with *"there is
nothing to fetch"*. None of them is wrong about today's session — they are just
being asked about the wrong thing.

The fix is a single shared rule, `tasks.ingest._has_publishable_backlog()`:
**if the newest session upstream is newer than what we have stored, there is
real, already-published work, and the guards must not apply.** When there is no
such backlog, all three behave exactly as before.

A fourth, non-code cause: no MYRA process was running, so no watchdog was
ticking. Automatic ingestion requires the scheduler to be up.

### Verified

Driving the real watchdog tick (`_maybe_trigger_ingest`) after the fix:

```
[MYRA BG] Database behind confirmed trading date (2026-10-12 < 2026-10-14)
          – triggering catch-up ingestion.
[MYRA BG] EOD2 sync result: rows=398611, symbols=3627, error=None
[MYRA BG] Catch-up ingestion advanced DB to 2026-10-14.
watchdog outcome: success (829.6s)
```

```
readiness state : ready
  "Latest market date 2026-10-14 matches the expected session (2026-10-14)."
recovery outcome: recovered — advanced 2026-10-12 -> 2026-10-14
```

## Ctrl+C / shutdown safety

The first signal takes the full graceful path: set the shutdown event, **join
every task** so none is killed mid-write, close pooled connections, checkpoint
the WAL, release the single-scheduler lock. A task that will not stop inside
`TASK_JOIN_SECONDS` is reported rather than silently waited on.

The gap this phase closed: a **second Ctrl+C was swallowed** by the
`if _shutdown_event.is_set(): return` guard. With a wedged task the operator was
left pressing Ctrl+C against no response and no feedback. A repeat signal now
counts down `FORCE_EXIT_GRACE_SECONDS` and then exits.

This is safe to force because every database is in WAL mode: an interrupted write
rolls back to the last commit, so a forced exit costs a WAL checkpoint, never
data integrity.

Three correctness details that matter:

- the countdown is **cancellable** — if the graceful path finishes during the
  grace period it is disarmed, so a clean stop is never killed by a stale timer;
- it is **single-shot** — a third Ctrl+C does not stack a second countdown;
- `run_pipeline.py` only calls `handle_exit()` from its `finally` when **no
  signal already did**, otherwise an ordinary clean shutdown would look like a
  second Ctrl+C and arm the countdown against itself.

Pinned by `tests/test_shutdown_safety.py` (16 tests). No test sends a real signal
or exits the process; the force-exit path is exercised through a patched
`os._exit`, and the countdown is cancelled in fixture teardown so it can never
outlive its test.

Recovery status is surfaced through the **existing** `/api/pipeline/readiness`
response and the **existing** SSE bus — no new endpoint, no new polling loop.
Readiness keeps its 30 s idle / 8 s active cadence, and SSE remains primary.

`readiness.recovery` carries `{reason, detail, at, fingerprint}`. It is
deliberately **not** folded into the overall state: "waiting for EOD" and "a
recovery attempt failed" are different facts, and merging them makes a correctly
waiting system look broken.

Reason codes render as plain language, and most are not errors:

| Code | Shown as |
|---|---|
| `recovered` | Recovered — data advanced to the expected session |
| `skipped_official_holiday` | Skipped — exchange holiday |
| `skipped_cooldown` | Waiting — retry cooldown after an earlier attempt |
| `skipped_retry_budget_exhausted` | Paused — retry budget spent on this source state |
| `skipped_calendar_unknown` | Skipped — session could not be confirmed |

A change in recovery reason is part of the readiness fingerprint, so a transition
actually reaches the UI.

## Tests

| Suite | Count | Covers |
|---|---|---|
| `tests/test_nse_calendar.py` | 22 | Classifier, precedence, unknown handling, malformed input, reconciliation safety |
| `tests/test_eod_recovery.py` | 35 | Fingerprints, eligibility, publication-window fix, budget, refund, outcome contract |
| `tests/test_shutdown_safety.py` | 16 | Graceful path, WAL checkpoint, lock release, bounded join, force-exit escalation |
| `tests/test_market_calendar_guard.py` | 26 | Calendar contract under the real ingest path |
| `tests/test_ingest_freshness.py` | 22 | Includes the 9 new ingest-gate tests (backlog probe, cutoff, weekend) |
| `tests/test_watchdog_*` | 42 | Watchdog decisions with the real calendar |

Full suite: **1392 passed**, zero live-database writes.

Every calendar/recovery test pins the official source in-process, so none reads
the real `meta.json` and none can drift when eod2 refreshes it. The tests that
touch the metadata sidecar use `fixture_db_dir` and clear their own rows,
because that fixture directory is shared for the session.

## Known limitations

- Official coverage is **2026 only**. Pre-2026 heuristic records (127) stay
  unverified, and 2027+ returns `unknown` until NSE publishes.
- The fingerprint uses the source's newest date and file counts. A source that
  replaced a file with different content while keeping the same counts would not
  produce a new fingerprint; the 30-minute cooldown bounds the cost of that.
- Special-session descriptions are carried through when the publication supplies
  them, otherwise the generic `(NSE circular)` label is used.
- CPU during idle monitoring is unmeasured. The 60 s tick is bounded by a cheap
  decision function plus the existing source-state cache.
- **Automatic ingestion requires the scheduler to be running.** Recovery is driven
  by the 60-second watchdog inside the MYRA process; if MYRA is not running
  there is nothing to tick, and a published session will sit un-ingested until the
  next start. This is by design (no second scheduler was added), but it means
  "automatic" is conditional on the app being up.

## Invariants preserved

- No schema migration; `market_calendar` is unchanged.
- No row is deleted by reconciliation, and the operation is idempotent.
- No heuristic may close a session the official calendar confirms is open.
- Nothing is guessed: unverifiable dates report `unknown`.
- The eod2 tree is unmodified.
- No new scheduler, thread, endpoint, polling loop, or event transport.
- Scoring, ranking and manual-CSV behaviour are untouched.
