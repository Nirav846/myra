# Phase 8 — Data Enrichment Pipeline Orchestration, Parallel Execution & Failure Recovery

Directive: make MYRA's data pipeline faster, more reliable, observable and
recoverable **without sacrificing data correctness**. Establish how the pipeline
actually behaves, find which tasks can safely overlap, and implement a small,
maintainable, evidence-based improvement — not a wholesale orchestrator rewrite.

Everything below was traced from the code that actually runs, not from the
existing documentation. Where a pre-existing subsystem was already correct (the
mature `sync_fundamentals`, `PipelineControl`), that is documented as a finding
rather than duplicated.

## 0. Verified starting state

| Item | Value |
|------|-------|
| Branch | `main` |
| Phase 8 **starting** commit | `f22fdbc` (`docs: record the Phase 7 commit hash…`), `== origin/main`, clean tree |
| Live EOD source | `eod2/src/eod2_data/daily/` — 3,769 per-symbol CSVs + `meta.json` (BhavDesk submodule) |
| Active ingest path | `constants.USE_EOD2_DATA = True` → `myra_app.eod2_sync.sync_eod2_data` (the legacy bhavcopy path is dead code today) |
| `DISABLE_FUNDAMENTAL_WRITERS` | `False` (so the fundamentals writers are live) |
| Full suite (before Phase 8) | 1223 passed |

A note on method: while building the reproduction for the WS4 defect I
**contaminated the live `myra_technical.db` with 3 synthetic rows** and
removed them in the same session. The ad-hoc script had patched
`myra_app.constants.DB_DIR`, but `eod2_sync` binds `DB_DIR` *by value* at
import, so the path resolved to the live sidecar. The 3 rows (symbols
`SYM00/01/02`, synthetic OHLC 100/101/99/100) were removed with a precise
predicate and the database re-verified (`quick_check: ok`, 8,009,058 rows —
identical to the Phase 7 baseline, 3,423 symbols, max date 2026-10-12). Every
EOD2 test since runs through `fixture_db_dir`, and
`tests/test_eod2_sync.py::test_sync_writes_only_to_the_redirected_database`
asserts the module binding tracks the redirect so this class of mistake cannot
recur silently. Recorded here because "report errors explicitly" applies to
mistakes I made, not only to pre-existing ones.

---

## 1. The actual pipeline dependency map

### 1.1 Entry points that can start a task

| Entry point | Path | Starts |
|------------|------|--------|
| Web server startup | `myra_web.myra_fastapi_server` lifespan → `background_orchestrator.start()` | the whole scheduled task set + startup catch-up + doctor |
| Headless CLI | `run_pipeline.py` (no args) → `background_orchestrator.start()` | same scheduled set (declines if a scheduler already runs — single-scheduler lock) |
| Manual CLI enrichers | `run_pipeline.py --enrich-screener / --enrich-ca / --sync-* / --backfill-bse` | one task each, then exit |
| Manual pipeline run | `POST /api/pipeline/{run,cancel,pause,resume}` → `PipelineControl` | the `PIPELINE_ORDER` sequence, one worker thread |
| Per-task web triggers | `myra_web/routes/*` calling `myra_app.tasks.*` via `tasks.context.default_context()` | individual tasks |

### 1.2 Scheduled tasks (`myra_app/tasks/registry.py`)

Each enabled `TaskSpec` becomes its own **daemon thread** via
`run_periodic` (`myra_app/tasks/executor.py`), launched with a 30 s stagger
(`background_orchestrator._launch_background_threads`). So the scheduled side is
already a *mixture*: independent tasks run concurrently, each with its own poll
loop.

| Task | Module | Writes | Idempotent | Partial-failure safe | Runtime (measured/est.) | Screening prerequisite? |
|------|--------|--------|------------|----------------------|------------------------|------------------------|
| `daily-ingest` | `tasks/ingest` → `eod2_sync` (EOD2) | `technical_data`, `ingestion_rejects`, derived cols | yes (`INSERT OR REPLACE`, per-symbol cutoff) | yes (per-file) | ~2–5 min for 3.7k files (measured: 7.6 s readiness + 6.3 s cutoff query + read) | **yes** (source of all prices) |
| `watchdog` | `tasks/watchdog` | re-triggers due tasks | yes (gates) | — | self-loop | no |
| `etf-sync` / `index-sync` | `tasks/etf_sync` / `index_sync` | metadata (blocklist, constituents) | yes | yes | weekly | no (enrichment) |
| `fundamentals-sync` | `tasks/fundamentals` | `fundamentals` | fill-only | yes | weekly | no (enrichment) |
| `fundamentals-daily` | `tasks/fundamentals` (`run_daily`) | `fundamentals` | fill-only | yes | daily post-close | no (enrichment) |
| `fundamentals-enrich` | `tasks/fundamentals_enrich` → `enrichment_sources` | `fundamentals` (+`symbol_alias`) | yes (fill-only, per-symbol commit) | yes (bounded batch) | daily, bounded `FUNDAMENTALS_ENRICH_BATCH_LIMIT`=200 | no (enrichment) |
| `institutional-sync` | `tasks/institutional` | `myra_institutional.db` | yes | yes | weekly | no |
| `db-backup` | `tasks/db_backup` | backups/ | yes | yes | nightly | no |
| `screener-enrich` | `tasks/screener_enrich` | `screener_fundamentals` | yes (after Phase 8) | yes | **disabled by default** (`enabled=False`); manual `--enrich-screener` | no (enrichment) |
| `fund-traction-sync` | `tasks/fund_traction` | traction tables | yes (monthly gate) | yes | monthly (HEAD-gated) | no |
| `cross-buy-sync` | `tasks/cross_buy` | cross-buy tables | yes | yes | 30d, `mark_on_failure` | no |
| `traction-sma-update` | `tasks/traction_sma` | derived SMA | yes | yes | daily | no |
| `mf-holdings-sync` | `tasks/mf_holdings` | `mf_*` tables | yes (gate) | yes | daily gate, monthly data | no |
| `symbol-identity` | `tasks/symbol_identity` | `symbol_alias` | yes | yes | weekly | no |
| `traction-symbols` | `tasks/traction_symbols` | symbol mapping | yes | yes | hourly poll | no |
| `point-in-time-archive` | `tasks/archive` | `myra_archive.db` | yes (append) | yes | weekly | no |

### 1.3 Manual pipeline (`PipelineControl.PIPELINE_TASKS`) and the one real dependency

Order (the `depends_on` edge is the only hard ordering the code enforces):

```
daily_ingest ──▶ enrichment          (the ONLY depends_on edge)
etf_sync, index_sync, fundamentals_sync, market_cap_sync,
shares_outstanding_sync, institutional_sync, fundamentals_enrich
```

* `enrichment` (`_run_enrichment` → `feature_enrichment.process_enrichment_pipeline`)
  reads symbols produced by `daily_ingest`, so it genuinely depends on it —
  running it first produces features for a stale universe. `PipelineControl`
  encodes exactly this via `depends_on=("daily_ingest",)`.
* Every other task is independent of ingest for correctness (they are
  enrichment; none feeds the price pipeline).

### 1.4 Shared-resource / conflict analysis (from code, not assumption)

| Resource | Concurrent writers? | Why |
|----------|---------------------|-----|
| `myra_metadata.db` (`sync_log`, `metadata`, `task_registry`) | **Yes, intentionally** | WAL mode; one writer at a time, short transactions. Task threads use *thread-local* `LibrarianCore` connections (`background_orchestrator._get_metadata_connection`) so SQLite objects are never shared across threads. `live_db_guard` (tests) proves no test writes it. |
| `myra_technical.db` (`technical_data`) | daily-ingest only | single writer (`_INGEST_LOCK` prevents executor/watchdog double-start). EOD2 sync uses one connection, WAL, `busy_timeout=30s`. |
| `myra_valuation.db` (`fundamentals`) | fundamentals_sync, fundamentals-daily, fundamentals-enrich, market_cap_sync, shares_outstanding_sync | **shared writer, serialized by WAL + `timeout=30`**; every writer is fill-only / zero-guarded via `safe_write`, so interleaving cannot clobber a valid value. |
| `myra_valuation.db` (`screener_fundamentals`) | `screener-enrich` only | **was** the single long-transaction writer (defect, fixed below). Distinct table from `fundamentals`, so it can overlap the `fundamentals` writers. |
| Screener.in upstream | rate-limited | `SOURCE_FETCHERS` is globally pacer-locked per run (see WS2). yfinance/NSE are per-source `RateLimiter`s. |
| Global connection caches | thread-keyed | `LibrarianCore` pools and `task_tracker._conn` are per-thread and reset by `fixture_db_dir` in tests. |

---

## 2. Current sequential/concurrent behaviour and confirmed bottlenecks

* **Scheduled layer: already concurrent** — every enabled task is its own
  daemon thread. Independent enrichments already overlap.
* **Manual pipeline layer: sequential** — `PipelineControl._run_sequence` walks
  `PIPELINE_ORDER` in one worker thread (by design; the ordering is the
  contract).
* **Within a task: sequential per symbol** — the Screener.in work was the
  slowest: `run_pipeline.py --enrich-screener` over ~3.2k symbols, and the daily
  `fundamentals-enrich` batch.

### Confirmed Screener.in bottleneck (WS2)

Traced in `myra_app/db/enrichers/screener_enricher.py` (the manual backfill) and
`myra_app/utils/fundamentals_sync.py` (the mature daily path):

* The original `screener_enricher` issued **5 HTTP requests per symbol**: one
  company page + (2 metrics × a "prime" GET to the company root + a chart GET),
  because `_get_company_id` and `_fetch_metric` each built a **fresh
  `requests.Session`** and `_fetch_metric` re-primed cookies every call.
* It then slept a **flat `time.sleep(1.5)` per symbol**, so the wall-clock was
  dominated by that sleep plus 5 serial round-trips per symbol, all single
  threaded.
* Its real aggregate request budget was therefore `5 requests / 1.5 s ≈ 0.3 s
  per request`.

The daily `sync_fundamentals` path is by contrast already well-built
(per-symbol commit, cancel/pause, exponential backoff, rate-limit-aware abort,
parking) — it was **not** rewritten here.

---

## 3. Per-task input/output and database-write map (for the tasks changed)

### `eod2_sync.sync_eod2_data` (daily ingest)
* **Input:** `eod2/src/eod2_data/daily/*.csv` (per-symbol, header
  `Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY`,
  dates `%Y-%m-%d`) + sibling `meta.json` (`lastUpdate`, `DLV_PENDING_DATES`).
* **Output:** `myra_technical.db` → `technical_data` (16 cols incl. derived
  `delivery_pct`, `delivery_ratio`, `sma_50`, `high_52w`, `low_52w`,
  `delivery_ma_60`) and `ingestion_rejects` (rows with close missing/≤0).
* **Freshness markers** (only on genuine success): `metadata.last_sync_date`,
  `sync_log.daily_ingest`, `stale_catchup`.

### `screener_enricher.enrich_screener_fundamentals` (manual Screener.in backfill)
* **Input:** `fundamentals.symbol WHERE market_cap IS NOT NULL` (the universe).
* **Output:** `myra_valuation.db` → `screener_fundamentals (symbol, pbv, roce, last_updated)`.
* **Idempotent:** yes — 7-day per-symbol cache (`CACHE_DAYS`) and a fill-only upsert.

---

## 4. Changes to concurrency, and why they are safe (WS2)

### 4.1 The blocker that had to be fixed first

Concurrency was initially **unsafe** for two reasons found in code, not assumed:

1. **A single write transaction held for the whole run.** The original
   `enrich_screener_fundamentals` called `conn.commit()` only *after* the loop.
   A multi-hour run therefore held a write lock on `myra_valuation.db` for its
   entire duration (blocking every other valuation writer) and lost all progress
   on a crash.
2. **SQLite connections are not shareable across threads**, and neither are
   `requests.Session` objects.

### 4.2 What was implemented (in `screener_enricher.py`)

* **Per-symbol commit** — a thread-local connection is opened, used and closed
  per symbol, so a worker's write lock is released immediately. Matches the
  mature `sync_fundamentals` pattern. Progress now survives an interruption.
* **Bounded `ThreadPoolExecutor`** — `DEFAULT_MAX_WORKERS = 4`, clamped to
  `[1, 16]`. Never one thread per symbol.
* **Global, thread-safe request pacer (`_RequestPacer`)** — a single lock
  guarantees no two HTTP requests (across all workers) start closer together
  than `REQUEST_INTERVAL_SECONDS = 0.3`. This reproduces the original's
  **aggregate** budget (5 req / 1.5 s = 0.3 s/req), so the request rate seen by
  Screener.in is *unchanged*; only the idle time between requests is absorbed.
  A 429 pushes the shared gate out for everyone (honours `Retry-After`).
* **Shared thread-local `requests.Session`** — removes the per-metric "prime"
  GET, cutting **5 → 3 requests per symbol (40 % fewer)**.
* **Duplicate-run prevention** — a process-wide `_RUN_LOCK`; a second
  concurrent run refuses (returns `already_running`) instead of double-fetching.
* **Fill-only upsert via `safe_write.upsert_row`** — `INSERT … ON CONFLICT DO
  UPDATE SET col = COALESCE(excluded.col, table.col)`. Inserts new symbols
  (the table is sparse) and never nulls an existing metric on a partial fetch.
* **Bounded retry with backoff** — retries only transient conditions
  (timeout / connection error / 429 / 5xx) with exponential backoff + jitter,
  capped; a 404 is permanent and not retried.

### 4.3 What was *not* parallelized, and why

* **`daily_ingest` was not overlapped with anything.** It is the prerequisite
  for `enrichment` and the sole writer of `technical_data`; correctness of the
  price pipeline outranks shaving minutes off it, and `PipelineControl` already
  models the dependency. The manual scheduler already runs it as its own thread.
* **The manual `PipelineControl` sequence was not parallelized.** Its ordering
  is the contract.
* **Daily `sync_fundamentals` was left alone** — it already does per-symbol
  commits, cancel/pause and rate-limit backoff correctly.

---

## 5. Baseline vs updated measurements (WS2)

Measured with a **local fake** (no live Screener.in quota, no production DB),
40 symbols, 50 ms simulated latency, modeling each implementation's *real*
request count:

| Configuration | Wall-clock | HTTP requests | Notes |
|---------------|-----------|---------------|-------|
| Original (1 thread, 5 req/sym, 1.5 s sleep) | 70.4 s | 200 | ~2.8 req/s |
| **Phase 8 (4 threads, 3 req/sym, 0.3 s global pacer)** | **37.8 s** | **120** | **1.86× faster, 40 % fewer requests** |

* Per-task elapsed: 70.4 s → 37.8 s (synthetic, equal workload).
* Overall wall-clock: same ratio, single task.
* Max observed concurrency: bounded at the configured 4 (asserted in tests).
* API request count: 200 → 120 (−40 %).
* Failure/retry counts: unchanged in the happy path; retries are now bounded and
  rate-limit aware.
* **Result correctness:** the benchmark asserts all 40 symbols are written; the
  real-DB correctness of the EOD2 fix is covered by the WS4 tests.

*Honest caveat:* these are **synthetic** numbers (faked latency, faked
responses). They isolate the *scheduling* behaviour the change targets. The
production speed-up also depends on real Screener.in round-trip times; a real
backfill was not run (it would consume live quota). The **request-count
reduction (5→3) and the rate-neutral pacing are exact**, not synthetic — they
are properties of the code.

---

## 6. Failure-handling and retry policies (WS3)

| Failure | Behaviour | Where |
|---------|-----------|-------|
| Transient network (timeout/conn reset) | bounded retry (×3) with exp backoff + jitter | `screener_enricher._request_with_retry` |
| Rate limit (429) | honour `Retry-After`, push the shared pacer out, retry | `_request_with_retry`, `_RequestPacer.penalise` |
| Server error (5xx) | bounded retry | `_request_with_retry` |
| Permanent (404 / unparseable) | **no** retry; treated as a per-symbol miss | `_request_with_retry` (4xx≠429 returns immediately) |
| Single symbol fails | counted `failed`, run continues | `_run_locked` |
| Source disabled / auto-run off | clean no-op with a reason | `enrich_screener_fundamentals` |
| DB write lock | per-symbol connection with `busy_timeout=60s`; short transactions | `_thread_db` |
| Process torn down mid-run | per-symbol commits keep completed work | per-symbol commit |

The mature daily path's policies (cancel/pause, parking after 5 failures,
rate-limit abort) are unchanged and were verified as the model to follow.

---

## 7. Partial-success semantics (WS3/WS5)

The Screener.in backfill now returns a truth-bearing summary and records it into
the **existing** `sync_log` status vocabulary — no new status system:

| Situation | `summary` | `sync_log.last_status` |
|-----------|-----------|------------------------|
| all succeeded | `partial=False` | `success` |
| some ok, some failed | `partial=True`, `updated`/`failed` counts | `partial` (+counts in `error_message`) |
| all failed | `partial=False`, `updated=0` | `failed` |

`tasks/ingest.py` already distinguishes `no_new_data` (ran, inserted 0 rows,
freshness **not** established) from `success`. That model is preserved.

---

## 8. Manual EOD CSV readiness and ingestion contract (WS4)

**The workflow is preserved exactly as-is**: the operator copies CSVs into
`eod2/…/daily/`; MYRA ingests them. Nothing auto-downloads from an exchange.

### 8.1 Defect found and fixed — a partial file set silently lost the rest of the day

The cutoff was a **single global `MAX(date)`** over the whole table. With the
manual workflow — where files are copied in over time — a half-copied folder
advances the global cutoff with the first ready file, and every symbol still
catching up is filtered out **forever**. Reproduced against a temp DB (10
symbols, 3 copied first): run 1 inserted 3, run 2 (after the other 7 finished
copying) inserted **0 — 7 symbols permanently lost that day, silently**.

**Fix (two parts, both in `eod2_sync.py`):**

1. **Per-symbol cutoff.** Each symbol advances only its own `MAX(date)`, loaded
   in one grouped query. A half-copied set ingests the ready symbols now; the
   stragglers land on a later run. `INSERT OR REPLACE` on `(symbol, date)`
   keeps it idempotent. The old global "no new data" early-bail (which caused
   the loss) is replaced by a *correct* bail: only when the **least-advanced**
   symbol is already at/past the newest source date is there truly nothing to do.
2. **Input readiness (`assess_source_readiness`)** — decides, from facts already
   on disk, whether the set is a complete, settled session: fraction of files at
   the newest date, `meta.json` `DLV_PENDING_DATES`, and per-file mtime
   stability (a file modified within the last 30 s is mid-copy). A partial /
   still-being-written set is **reported** (`INPUT DEGRADED` warning) and a file
   that is still changing is **skipped**, not parsed, so a torn row is never
   stored. A complete set ingests normally. The threshold
   (`MIN_SESSION_COMPLETENESS = 0.5`) is calibrated from the real source shape:
   a normal day has ~3,570/3,769 (94.7 %) of files at the session date; the
   half-copied repro (3/10 = 30 %) fails it.

### 8.2 Result contract

`sync_eod2_data()` now returns `rows_inserted`, `symbols_updated`, `skipped`,
`rejected_rows`, `rejected_symbols`, a **`readiness`** dict, and `error`. It is
idempotent: a rerun inserts nothing and mutates nothing (`test_rerun_inserts_nothing_new`).

### 8.3 Preserved behaviours

Rejects (close missing/≤0) still route to `ingestion_rejects` with the two reason
codes; empty/malformed/duplicate-date files are counted and never stored; a
symbol present only in the DB is never deleted or rewound.

---

## 9. Rerun and recovery instructions

* **Ingest a partial day:** just re-run the ingest (scheduled, or the Control
  Room → Daily Ingest). Ready symbols drop out; the rest ingest on the next run.
  A rerun is safe and idempotent.
* **Screener.in backfill resume:** per-symbol commit + the 7-day cache mean a
  re-run skips what is already fresh; a degraded run shows `partial` with counts
  in Mission Control / `sync_log`. Re-run with `--enrich-screener`.
* **Diagnosing a repeated failure:** the `sync_log` row for `screener_enrich`
  (or `daily_ingest`) carries the status and the error/count message; the
  read-only readiness assessment is logged as `INPUT DEGRADED: <reason>`.

---

## 10. Mission Control / API changes

**None.** The directive was to keep one status contract. The Phase 8 changes
report through the *existing* surfaces:
* Scheduled/manual task status via `sync_log` + `PipelineControl` (unchanged
  vocabulary; `screener_enrich` now also writes `partial`).
* EOD2 readiness as a log line and a `readiness` field in the ingest result that
  the existing `tasks/ingest` already surfaces via its `no_new_data` /
  `error` handling.

No new router, scheduler, or status contract was introduced.

---

## 11. Tests, DB-isolation, and CI

### New tests (no live network, no production DB)
* `tests/test_eod2_sync.py` (15): partial-set not lost; full-set one-pass;
  rerun no-op; older history does not regress a symbol; readiness
  (half-copied / complete / delivery-pending / still-writing / empty /
  not-newer); empty + malformed counted not stored; zero-close rejected not
  stored; duplicate dates collapsed; DB_DIR isolation.
* `tests/test_screener_enricher.py` (25): bounded concurrency observed and
  capped; worker clamp; global pacer serialisation + 429 penalise; duplicate-run
  refusal; new symbol inserted; partial fetch preserves an existing metric;
  empty fetch untouched; per-symbol commit survives abort; transient retry then
  success; retry bounded then raises; 404 not retried; 429 honours Retry-After;
  5xx retried; partial-success summary + `partial` status; total failure not
  success; cache skip; source/auto-disable no-ops; company-id + metric parsing.

### Verification
| Check | Result |
|-------|--------|
| New tests | **40 passed** (15 eod2 + 25 screener) |
| Focused isolation/orchestration tests | **123 passed** (incl. Phase 7 isolation + WAL-guard tests) |
| **Full suite** | **1263 passed**, 0 failed (was 1223; +40 new) |
| `black` 26.10.1 / `flake8` | clean on all touched files |
| Performance guard | **268 violations / 0 CRITICAL** = the pre-existing baseline (new code adds none) |

DB-isolation (WS6): all new tests use `fixture_db_dir`; the Phase 7 live-DB
guard and committed-WAL-frame detection are untouched and green; no
`pytest-xdist`; the `eod2_sync` DB_DIR-isolation regression test guards against
the mistake described in section 0.

CI/CodeQL: see the Phase 8 section of `docs/session-history.md` for the final
run ids (updated in the follow-up docs commit).

---

## 12. Known limitations and next steps

1. **The backfill speed-up is synthetic + request-count, not a timed live run.**
   A real backfill (consuming live Screener.in quota) was deliberately not run.
   Next step: run one `limit=25` `--enrich-screener` in an idle window and
   compare real elapsed time and 429 rate against the model.
2. **Concurrency is deliberately modest (4 workers).** Screener.in is rate-limited
   and the win is mostly from removing the redundant request + absorbing
   latency, not from raw parallelism (at an equal request budget concurrency
   alone is only ~1.1×). Raising `DEFAULT_MAX_WORKERS` is a one-constant change
   but should not be done without a real-quota measurement.
3. **`vwap` / `delivery_qty` / `stock_return`** remain dead columns (carried over
   from Phase 7) and the new enricher reads neither.
4. **Symbol universe drift:** ~992 of 3,948 `fundamentals` symbols are absent
   from `symbols_master` (Phase 7). The Screener.in backfill iterates
   `fundamentals`, so it is unaffected, but the broader join health is open.
5. **`_EOD2_DAILY_CANDIDATES` still hard-codes a Windows path** as the third
   candidate; left as-is (pre-existing, environment-specific).
6. **Duplicate-run prevention is per-process.** Two OS processes could still
   both run the backfill; the existing `scheduler_lock` covers the *scheduler*,
   not the manual CLI. Cross-process exclusion for the manual enricher was not
   added (scope).
7. **Doc drift (pre-existing, not cleaned here per scope):** `AGENTS.md` says
   "~2.25M technical rows" (actually 8.0M) and "8 SQLite sidecars" (12
   populated + 3 stray empty files); `PROJECT_SUMMARY.md` still references the
   old bare DB names.

---

## Definition-of-done status

- Pipeline + task dependencies documented from code — §1.
- Screener.in bottleneck measured, cause established — §2, §5.
- Meaningful, evidence-supported improvement implemented — WS2 (1.86×, 40 %
  fewer requests) and WS4 (fixes silent whole-day data loss).
- Independent tasks overlap only where dependencies/resources permit — §4.3.
- Failures / partial success reported truthfully — §7.
- Failed tasks diagnosable + safely rerunnable — §9.
- Manual EOD CSV ingestion protected against incomplete/malformed input — §8.
- Mission Control / task-status contract kept consistent — §10.
- New behaviour tested without live services — §11.
- Full tests + quality checks pass — §11.
- Committed, pushed, CI verified — recorded in `docs/session-history.md`.
