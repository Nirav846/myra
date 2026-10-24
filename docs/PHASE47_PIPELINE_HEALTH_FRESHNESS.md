# Phase 47 — Pipeline health incident: freshness and price-quality reporting

Evidence-led diagnosis of the three dashboard warnings (database "8 days stale",
225 invalid closes, failed/missing task statuses), plus the two confirmed
reporting defects it exposed. **Diagnostic + code-level only**: no production
sync, refresh, backfill, scheduler task, DB write, or service restart was
performed. All probes opened the production databases with `mode=ro` and
`PRAGMA query_only=ON`.

---

## A. Root causes

### A1. CONFIRMED — the doctor measured staleness in calendar days

`tools/db_doctor.py` compared `MAX(date)` against the current IST date as
**calendar** days and escalated at `>= 2`:

```python
days_behind = (current_date - db_date).days
if days_behind >= 2:  # CRITICAL
```

On a Monday the newest row is Friday's — three calendar days back but **zero**
trading sessions behind. Every weekend, Monday and post-holiday morning
therefore produced a false CRITICAL.

Evidence from the live `doctor_runs` table:

| id | when (UTC) | local | latest data | reported |
|----|-----------|-------|-------------|----------|
| 90 | 2026-10-24T16:40 | Sat 22:10 IST | 2026-10-16 | `[CRITICAL] STALE - 8 days` |
| 89 | 2026-10-23T18:48 | Sat 00:18 IST | 2026-10-16 | `[CRITICAL] STALE - 8 days` |
| **88** | 2026-10-18T16:00 | **Sun** | **2026-10-16 (Friday)** | `[CRITICAL] STALE - 2 days` |
| 87 | 2026-10-16T19:43 | Fri | 2026-10-14 | `[CRITICAL] STALE - 2 days` |
| 86 | 2026-10-15T19:41 | Thu | 2026-10-14 | `[WARNING] slightly behind - 1 days` |

Row **88** is the defect in one line: on a Sunday, with the newest possible row
already loaded, the doctor reported CRITICAL.

Live reproduction during this phase (Sunday 2026-10-25 00:42 IST,
`MAX(date) = 2026-10-23`):

```
old calendar rule : 2 calendar days behind  -> [CRITICAL] Database is STALE
new session rule  : expected 2026-10-23, 0 sessions behind -> [OK] up to date
```

`myra_web/utils.py:trading_day_freshness` already implemented the correct rule
and `myra_web/routes/health.py:162` already used it for the health bar. The
doctor was the last consumer still on calendar days.

### A2. CONFIRMED — the "8 days stale" row was a stale persisted snapshot, not missing data

`doctor_runs` id 90 was written at **2026-10-24 22:10 IST**. `sync_log` shows
`daily_ingest` succeeded at **2026-10-24 22:28:50 IST** — eighteen minutes
later. The doctor's finding was accurate when written and obsolete almost
immediately; the dashboard keeps showing the latest persisted row.

The data is current: `MAX(date) = 2026-10-23` (Friday), the last completed
session. Per-date coverage for 2026-09-25 → 2026-10-23 is ~3,180–3,240 symbols
on every trading day.

The two apparent gaps are **exchange holidays**, confirmed in
`myra_calendar.db:market_calendar`:

- `2026-10-02` — `Gandhi Jayanti`
- `2026-10-20` — `NSE holiday: Dussehra`

So there are **no missing trading days** in that window.

The UI already surfaces report age honestly (`PipelineStatusPanel.tsx:219`,
`checked {timeAgo(doctor.when_utc)}` → "checked 8d ago"), so no display fix was
required.

### A3. CONFIRMED — the 225 invalid closes are 3 non-equity/defunct series, not live corruption

| symbol | rows | span | close | volume |
|--------|------|------|-------|--------|
| `NIFTY50 DIVIDEND POINTS` | 210 | 2012-03-30 → 2026-04-23 | 0 | 0 |
| `ESSENTIA` | 14 | 2019-08-08 → 2019-11-11 | 0 | 0 |
| `NIFTY 50 FUTURES TR INDEX` | 1 | 2019-04-04 | 0 | 1 |

224 distinct dates, none recent. Two are index pseudo-symbols with **no**
`fundamentals` row, so they are excluded by the market-cap universe join.
`ESSENTIA` does have a `fundamentals` row (`market_cap` ≈ ₹1.56 bn, dated
2026-10-24) but its technical data ends 2019-11-11, so scanners load no recent
bars and skip it.

The canonical ingest already rejects these — `myra_app/eod2_sync.py:419`
`REASON_LE_ZERO = "close<=0 (eod2)"`, asserted by
`tests/test_eod2_sync.py:345/365`. The 225 rows came from a different producer
(index/points sync or a legacy bulk load), and the count has not grown
(id 86 → id 90 all report exactly 225).

The defect was **reporting**, not data: a bare count is indistinguishable from
live price corruption. The warning now names the affected symbol count and date
span.

### A4. UNRESOLVED / not a defect — task statuses

`sync_log` shows genuine, distinct states, and the contract already separates
them (`success` / `no_new_data` / `failed` / `timed_out` / `cancelled` /
`unknown`). Two entries are real outages rather than reporting bugs:

- `cross_buy_sync` — `failed`, `CSV download failed`, last run **2026-09-23**
  (32 days). No later attempt recorded.
- `market_cap_sync` — `timed_out`, `Task timed out after 1800s`, last run
  **2026-10-18**.

`shares_outstanding_sync` shows `cancelled` / `Cancelled by operator` at
2026-10-25 00:04 — an operator action, correctly not reported as success.

Resolving these needs an authorised run, not a code change. No task-status code
was modified.

---

## B. Changes

`tools/db_doctor.py`

1. New module-level `sessions_behind(latest_date, now)` reusing
   `myra_web.utils.trading_day_freshness` (the helper the health bar already
   uses) with `myra_app.daily_ingestor.is_trading_day`, so holidays and the
   18:30 IST bhavcopy cutoff are honoured.
   Returns `(expected_iso, sessions, note)`. When the calendar cannot be
   consulted it returns `sessions=None` **and a note**; the doctor then prints
   the note and falls back to the old calendar-day rule — it never silently
   reports the database fresh.
2. Staleness block now prints `Expected trading date` and
   `Trading sessions behind`, and escalates on sessions (`>= 2` CRITICAL,
   `> 0` WARNING). Severity semantics preserved, unit corrected.
3. `check_technical_data_quality` gains three scope columns **inside the
   existing single table scan** (`COUNT(DISTINCT …)`, `MIN(… date)`,
   `MAX(… date)`) — no extra scan, no new cost. The warning now reads e.g.
   `Rows with zero/negative close price: 225 across 3 symbol(s) (2012-03-30 .. 2026-04-23)`.
   Detection behaviour is unchanged; the doctor still never modifies these rows.

`tests/test_db_doctor_freshness.py` — 12 new tests, temp SQLite fixtures only:

- Friday data on Sunday / Monday morning is **not** stale (the id-88 regression)
- Friday data after Monday's cutoff is exactly 1 session behind
- a named holiday is not counted as a missed session
- genuine staleness still counts every missed session (8)
- unparseable date and an unavailable calendar both yield `None` + a note
  (never a fabricated 0)
- the bad-close warning reports symbol count and date span
- the doctor detects but never deletes or rewrites the invalid rows
- clean data emits no bad-close warning; a missing technical DB skips cleanly

Verification: 12 new + 8 existing freshness + 41 `eod2_sync`/`validate_row` = all
pass. `black` / `flake8` / performance guard / schema drift guard all pass
under the pinned pre-commit config. The pinned black (26.10.1) also reformatted
one pre-existing `conn.execute("""…""")` block in the same file; that is
formatting drift from the earlier `--no-verify` commit, not a behaviour change.

---

## C. Production state

- **No writes.** Every probe used `mode=ro` + `PRAGMA query_only=ON`. No doctor
  run was executed, so no new `doctor_runs` row was created.
- DB main-file mtimes observed at the end of the session: technical
  `24-10 22:41:20`, valuation `18-10 22:01:02`, metadata `24-10 00:26:33`,
  scoring `24-10 22:29:23`, calendar `15-10 00:18:33`. The technical/scoring
  mtimes changed at 2026-10-24 22:28–22:41 because **the operator's own
  `daily_ingest` ran then** — the same event that made the doctor's finding
  obsolete. Nothing in this session wrote to them.
- No service was started, stopped or restarted (backend PID 8824 on :8000,
  Vite PID 9236 on :3000 untouched).

---

## D. Remaining authorised operational actions

Mandatory before the dashboard reads clean:

1. **Re-run the DB doctor** (`POST /api/tools/db-doctor`, or let the startup
   task do it). This writes a new `doctor_runs` row and clears the stale
   "8 days stale" finding. Until then the dashboard keeps showing the
   2026-10-24 snapshot — correctly labelled as ~1 day old.

Optional:

2. **`cross_buy_sync`** — 32 days since the last attempt, failed on
   `CSV download failed`. Needs an authorised run to confirm whether the source
   endpoint still works.
3. **`market_cap_sync`** — timed out at 1800 s on 2026-10-18; consider whether
   the timeout is still appropriate.
4. **Universe hygiene (requires write authorisation)** — `ESSENTIA` carries
   `market_cap > 0` dated 2026-10-24 while its technical data ends 2019-11-11.
   Either its fundamentals row should be retired or the name should be marked
   delisted. Not done here: production DB writes are out of scope.

---

## E. Readiness verdict

**Verified current.** `myra_technical.db` holds every trading session through
2026-10-23 (the last completed session), with ~3,180–3,240 symbols per day. The
two date gaps in the window are confirmed exchange holidays. The 225 invalid
closes are three historical non-equity/defunct series that no scanner consumes
and whose count has not grown.

The warnings on screen were **reporting defects**, not data defects:

- "8 days stale" — a persisted doctor snapshot from 18 minutes before the
  ingest that fixed it (A2), plus a calendar-day rule that fires CRITICAL every
  weekend (A1, now fixed).
- "225 invalid closes" — accurate count, now correctly scoped (A3).

Not addressed, and still open: `cross_buy_sync` has not succeeded in 32 days,
and `market_cap_sync` last timed out. These are optional-task outages that need
an authorised run; they do not affect core screening readiness.
