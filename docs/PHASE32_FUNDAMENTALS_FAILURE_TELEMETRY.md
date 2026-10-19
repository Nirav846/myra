# Phase 32 — Additive Fundamentals Failure Telemetry

**Verdict: `FAILURE TELEMETRY IMPLEMENTED — TESTED AND DATA PRESERVED`**

Observability only. No refresh, no external request, no parser/fetcher behaviour
change, no production write.

---

## 1. The evidence gap this closes

Phase 31 asked the obvious question — *why did these symbols fail?* — and could
not answer it for a single one of 119 symbols:

- `DataFetcher.last_source_errors` already held a credential-free per-source
  reason (`HTTP 403`, `no data returned`, `<source>: <ExcClass>`, a fixed page
  note), and `FundamentalManager.last_error` already summarised it.
- Both lived **in memory only**. `sync_fundamentals` `print`ed the summary once
  (`fundamentals_sync.py:417`) and nothing captured it. Every file under
  `logs/` stops at 2026-09-23; the refresh ran 2026-10-18.
- `record_failure` persisted `fails`/`last_attempt`/`parked_until` — counts, not
  causes — and `last_attempt` is the run's *start* time for every symbol.

So the diagnosis existed, fleetingly, and was lost. The fix is not to invent new
diagnostics: it is to write down the ones already computed.

## 2. Schema and chosen database

**Database: `myra_metadata.db`** (`LibrarianCore.DB_MAP["meta"]`, resolved through
`constants.DB_DIR` — never a hardcoded path). That sidecar already holds
`fundamentals_park_state`, `fundamentals_sync_status`, `sync_log` and
`task_registry`: operational bookkeeping, not market data. Storing it next to the
park state it explains keeps one operational database rather than inventing a
ninth.

**Table (additive, created lazily with `CREATE TABLE IF NOT EXISTS`):**

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `symbol` | TEXT NOT NULL | validated ticker shape |
| `observed_at` | TEXT NOT NULL | ISO-8601 UTC, **per attempt** |
| `run_id` | TEXT | the run's `start_time` |
| `source` | TEXT | allowlisted source name, NULL when unknown |
| `category` | TEXT NOT NULL | one of nine fixed constants |
| `http_status` | INTEGER | 100–599 only, NULL otherwise |
| `exception_class` | TEXT | validated class name only |
| `detail_code` | TEXT | fixed code, e.g. `no_quarterly_table` |

Index: `idx_fundamentals_failure_log_symbol (symbol, id)`.

Convention followed: lazy `CREATE TABLE IF NOT EXISTS` at the point of use,
wrapped so a failure cannot escape — the same pattern as `source_stats`,
`task_registry` and `traction_symbol_state`. Nothing existing is altered; no
`ALTER`, no rewrite, no migration of `fundamentals` or `quarterly_results`.

**Schema-drift note:** `schema/*.sql` is generated from the live DBs by
`scripts/dump_schema.py`, and the pre-commit guard compares it against them. The
table does not exist in the live DB yet (this phase wrote nothing to production),
so the guard still passes. After the first real run creates it,
`python scripts/dump_schema.py` must be run and committed — that is the documented
follow-up, deliberately not done now, because adding the table to `schema/`
before it exists in the live DB would itself register as drift.

## 3. Event granularity and failure-path coverage

**One row per (symbol, source) per refresh attempt.** A symbol that failed
against two sources writes two rows sharing `observed_at` and `run_id`; a symbol
with no per-source detail writes exactly one row with `source = NULL`.

Recording happens at **exactly one call site** per branch, all inside the sync
loop — never in the fetcher or the manager — so an attempt cannot be counted
twice.

| Path | Category written | Evidence used |
|---|---|---|
| Screener non-200 | `http_error` + status | `last_source_errors[source] = "HTTP <ddd>"` |
| Source returned nothing | `no_data` | `"no data returned"` |
| Page 200, no quarterly table | `page_note` + `no_quarterly_table` | the one fixed `last_page_note` value |
| Source raised | `exception` + class | `"<source>: <ExcClass>"`, prefix must equal the source key |
| Google `Q-1..Q-4` rows rejected | `undated_rows_rejected` | `last_error` + `last_source` (the guard worked; it was not weakened) |
| Persistence failure | `persist_failed` + class | `"persist failed (<ExcClass>: ...)"` — message discarded |
| Generic exhaustion | `fallback_exhausted` | `"multi-source fallback exhausted"` with no per-source detail |
| Throttled (403/429/503) | `rate_limited` + status | the sync loop's own branch |
| Anything unrecognised | `unknown` | no text stored |

Two rules were enforced rather than assumed:

- **Statuses stay with their source.** When per-source detail exists, each row
  carries its own status and the fetcher-level `last_http_status` is *not* used —
  so `screener_in: HTTP 403` and `google_finance: HTTP 404` cannot bleed into
  each other, or into another symbol.
- **No cause is claimed beyond the evidence.** When only generic exhaustion is
  established, the row is `fallback_exhausted` with `source = NULL` and **no HTTP
  status** (the fetcher-level status cannot be attributed to a source), rather
  than inventing a culprit.

## 4. Sanitisation rules

The table stores **no free text**, by construction: there is no code path that
writes a caller-supplied string.

| Field | Rule |
|---|---|
| `category` | must be one of the nine `CAT_*` constants, else `unknown` |
| `http_status` | integer 100–599, else NULL |
| `exception_class` | anchored regex **and** must end in `Error`/`Exception`/`Warning`/`Interrupt`/`Exit`/`Timeout`/`Iteration` (or be exactly `Timeout`) |
| `detail_code` | must be in the fixed `PAGE_NOTE_CODES` allowlist |
| `source` | `^[A-Za-z][A-Za-z0-9_.-]{0,39}$`, else NULL |
| `symbol` | `^[A-Za-z0-9][A-Za-z0-9._&-]{0,39}$`, else the row is **not written** |
| `run_id` | generated by MYRA (the run's start time), truncated to 40 chars |

Hostile or unexpected values fall through to `unknown` with no detail — they are
**dropped, not escaped**. URLs, query strings, cookies, headers, tokens, response
bodies and exception messages therefore cannot be stored.

Two sanitisation holes were found and closed **during implementation**, both by
the tests rather than by inspection:

1. A value shaped like `"FieldTextThatShouldNotBeStored: SECRET123"` matched the
   generic `<name>: <token>` shape and would have stored `SECRET123` as an
   exception class. Fixed by requiring the prefix to equal the symbol's own
   source key **and** by requiring the class name to look like a real exception.
2. The exception path originally recorded telemetry inside the `except` block
   *and* fell through to the hard-failure branch, producing two rows for one
   attempt. Fixed by hoisting a single `escaped_exc` marker and recording once.

## 5. Retention decision

**Keep the newest `KEEP_PER_SYMBOL = 20` rows per symbol**, applied in the same
transaction as the insert (`DELETE ... WHERE symbol = ? AND id NOT IN (SELECT id
... ORDER BY id DESC LIMIT 20)`).

This mirrors the only existing bounded-log precedent in the codebase — `DELETE
FROM source_stats WHERE name = ? AND date NOT IN (... ORDER BY date DESC LIMIT
15)` — instead of inventing a time-based rule with no precedent. Growth is bounded
at `symbols × 20` rows; a 119-symbol failure cohort costs ~2,400 rows at worst.

No global delete, no TTL job, no scheduler, no destructive cleanup of anything
that predates this table (nothing predates it). The policy is documented here and
pinned by `test_retention_keeps_the_newest_rows_per_symbol`.

## 6. When telemetry cannot be written

`record_failure` catches everything (`sqlite3` errors, disk full, locked DB,
unexpected shapes) and returns `0`, logging a warning that names **only the
symbol** — never the evidence, which may be the very thing that failed to
sanitise. The `[FAIL]` line on stdout is untouched, so the original diagnostic is
preserved.

At the sync layer, `_record_failure_telemetry` wraps the call in its own
`try/except` as a second line of defence. `failed`, `fails`, `parked_until`,
backoff, cancellation and the returned summary are all computed independently of
telemetry, so a telemetry outage cannot change the run's outcome.

Pinned by `test_telemetry_write_failure_never_raises` and
`test_telemetry_breakage_does_not_change_the_run_outcome` (which asserts the
parking state still advanced for a failing symbol while the insert exploded).

## 7. Tests and preservation checks

**New tests — 25 in `tests/test_fundamentals_failure_log.py`**, all with temporary
databases and fake managers; no network:

schema creation on a new DB · idempotent init · one event · multiple source
errors preserved · unknown/missing diagnostics · status isolation between
sources *and* symbols · URL/query/cookie/token/exception-message sanitisation ·
non-symbol-shaped input rejected · secret-bearing exception class rejected ·
persist-failure keeps only the class · undated-row rejection recorded · escaped
exception records exactly one event · stale source errors never reused · retention
cap per symbol · `summarize` grouping · `recent_failures` newest-first and
read-only · helpers safe when nothing exists · success records nothing · one
attempt ≠ duplicates · parking/retry unchanged · rate-limit category · fundamentals
values untouched.

**Existing behaviour re-run, not assumed:** `test_fundamental_manager.py`
(including `test_undated_google_rows_are_never_written` and the COALESCE partial-
payload test), `test_fundamentals_diagnostics.py` (`Q-1..Q-4` recognition and
refusal), `test_sync_fundamentals_resume.py`, `test_fundamentals_sync_helpers.py`,
`test_fundamental_sync_guard.py`, `test_source_priority.py`, `test_safe_write.py`.

**Full suite: 2086 passed, 0 failed** (6:51) — 2061 before this phase + 25 new.
The pre-existing `task_tracker`/`DB_DIR` batch-isolation artefact did **not**
recur in the full run and was not touched.

**Formatting/lint:** `black` clean, `flake8` clean on all three changed files.
Guards (performance guard, schema drift guard) examined; the drift guard passes
because the table is not yet in the live DB — see §2.

**Production data preservation, read-only before and after:**

| Check | Before | After |
|---|---|---|
| `quarterly_results` rows | 10,798 | **10,798** |
| `report_date` null/blank | 0 | **0** |
| Placeholder `Q-%` dates | 0 | **0** |
| Distinct reporting periods | 79 | **79** |
| `fundamentals` rows | 3,951 | **3,951** |
| `fundamentals_park_state` | 119 entries, 9,312 bytes | **identical** |
| AAATECH `last_updated` | 2026-10-17T01:46:29.474666 | **unchanged** |
| Telemetry table in live DB | absent | **absent** (no production write) |
| WAL files | 0 bytes | **0 bytes** |

## 8. Failure paths that remain uninstrumented

Stated plainly, because an incomplete map is worse than an honest one:

- **Failures before/after the per-symbol loop** — freshness skip, empty queue,
  disabled source, service-level abort. These are run-level, not symbol-level,
  and already surface in progress/`sync_log`.
- **`_fetch_raw_data` with no fetcher attached** (`self.fetcher is None`): no
  per-source evidence exists, so the row is `fallback_exhausted`/`unknown`. In
  practice the sync always attaches one.
- **Cancellation mid-symbol**: no failure is recorded, because nothing failed.
- **Symbols skipped because already parked**: no attempt, so no row — correct,
  and unchanged.
- **The rate-limit *abort*** is not itself a symbol failure; only the throttled
  symbol gets a row.
- **What the source's HTML actually contained** — deliberately out of scope. The
  log distinguishes *contract* failures (`page_note: no_quarterly_table`,
  `undated_rows_rejected`) from *access* failures, but never stores page content.

## 9. How an operator inspects it safely

Read-only helpers (both open the DB with `mode=ro` and return `[]` if it does not
exist yet):

```python
from myra_app.utils.fundamentals_failure_log import summarize, recent_failures

summarize()                      # counts by symbol x source x category
summarize(symbol="AAATECH")      # one symbol
recent_failures(symbol="AAATECH", limit=20)   # newest first
```

Or directly:

```sql
SELECT symbol, source, category, http_status, exception_class, detail_code,
       observed_at, run_id
FROM fundamentals_failure_log
WHERE symbol = 'AAATECH'
ORDER BY id DESC LIMIT 20;
```

Rows sharing `observed_at` came from **one attempt**; the newest distinct
`observed_at` is the most recent refresh's evidence. No UI, scheduler or new
dependency was added — a reader can call the helpers, which is enough to answer
*"what actually failed for this symbol during the most recent refresh?"*

## 10. What changed, and what did not

**Changed:** `myra_app/utils/fundamentals_failure_log.py` (new),
`tests/test_fundamentals_failure_log.py` (new), and **+43 lines, 0 deletions** in
`myra_app/utils/fundamentals_sync.py` (one helper plus one call per failure
branch).

**Unchanged:** source order, fallback chain, cooldowns, retry thresholds,
`PARK_AFTER_FAILURES`, `is_parked()`, `fails`/`last_attempt`/`parked_until`
semantics, `quarterly_results` and `fundamentals` writes, COALESCE, the undated-
row guard (Google `Q-1..Q-4` still rejected), parser, scoring/ranking, freshness
thresholds, growth defaults, and the live-DB guard.

## 11. Explicit confirmation

- **No fundamentals refresh, retry, backfill or scheduler job was started.**
- **No external HTTP request was made** — every test used fakes and temporary
  databases; the module itself contains no network code.
- **No production write occurred**: the telemetry table is absent from the live
  metadata DB, park state is byte-identical, row counts and reporting dates are
  unchanged, and all WAL files are 0 bytes.
- Commit, CI and CodeQL outcomes are recorded in the accompanying session
  summary; nothing is claimed that was not verified.
