# Session History Archive

This file contains the detailed session-by-session history that was previously in AGENTS.md.
It is kept for reference but removed from the main AGENTS.md to reduce context bloat.

## Completed Sessions

### Session 1 — Test Suite
- 61-test pytest suite in `tests/` covering: `gate1_score`, `validate_row`, `ih_score`, `DER`, `float_exclusion_guard`.
- GitHub CI workflow `.github/workflows/ci.yml`.

### Session 2 — Data-Health Endpoint + Task Tracker
- `GET /api/data-health` in `myra_fastapi_server.py` returning critical pipeline metrics.
- Persistent task tracker: `task_registry` table in meta.db schema, SQLite-backed `task_tracker.py`.

### Session 3 — Fundamentals Protection
- Protected `promoter_holding_pct`, `free_float_pct`, `free_float_market_cap` from sync overwrite in `fundamental_sync.py`.

### Session 4 — CORS + Token Hardening
- CORS restricted to `["http://localhost:3000", "http://localhost:5173"]`.
- Morningstar token externalised to `MORNINGSTAR_TOKEN` env var.

### Session 5 — HealthStatusBar Component
- React `HealthStatusBar` component (auto-refresh, 3 severity states) in `App.tsx` with `pt-9` offset.

### Session 6 — TypeScript Fix + Backfill Discovery
- Added `grade?: string;` to `Candidate` interface in `SeasonalDeliveryHarvester.tsx` (TS2339 fix).
- Discovered `tools/enrich_history.py` already implemented the backfill correctly.

### Session 7 — Enrichment Backfill Optimisation (~20 h → ~57 min)
- `myra_app/feature_enrichment.py:494` — new `enrich_from_dataframe(full_df, nifty_df, target_date)` function.
- `tools/enrich_history.py` fully rewritten: single `pl.read_database("SELECT * FROM technical_data")`, per-date Polars enrichment, batch writes every 50 dates.
- Old script renamed to `tools/enrich_history_old.py`.

### Session 8 — Column Consolidation + SchemaRegistry
- **Column consolidation** (`fundamental_sync.py`): `MS_CANONICAL_MAP` maps 10 camelCase Morningstar keys to canonical snake_case. Removed camelCase keys from INSERT dict. Backfilled 2,065 rows (`peRatio` → `pe`, `marketCap` → `market_cap`).
- **SchemaRegistry extension**: 30 table schemas across all 8 databases (was 1). Startup validation loops over all tables. Zero schema drift.
- **Scanner/frontend COALESCE simplification**: All `COALESCE(market_cap, marketCap, 0)` → `COALESCE(market_cap, 0)`.
- Commits: `3b593d8` (column consolidation), `0a14957` (SchemaRegistry).

### Session 9 — P3 Cleanup
- **P3-01** (`delivery_ratio`/`delivery_source`): Audited, columns are alive and actively used across ingestion, ML, and frontend. **Skipped.**
- **P3-04** (`_has_active_queries`): Removed dead function, constant, and call site from `background_orchestrator.py`. Commit: `ccfa042`.

### Session 10 — Scanner Bug Fixes (Diagnosis + Fix)
- **Diagnosis**: Wyckoff Automaton and Liquidity Flip Detector returned 0 candidates because `lookback_days` was measured in **calendar days** but minimum row thresholds expected **trading-day** counts. 90 calendar days = 59 trading days, but threshold required `lookback_days+10 = 100`. Per-symbol data-fetch filtered out every symbol at the first check.
- **Fix**: Changed `lookback_days+X` to `max(floor, int(lookback_days*0.6)+5)` in all affected scanners.
- Wyckoff: also fixed hardcoded `n < 60` → `n < 55` in `_detect_events`, and `KeyError: 'symbol'` crash (DataFrame had no symbol column — bug was masked by the `n < 60` guard).
- Operator Fingerprint scanner had the same calendar-vs-trading-day bug; also fixed.
- Results: Wyckoff 527, LFD 2, OFP 241 candidates. All 61 tests pass.
- Commits: `78d1f6f` (Wyckoff + LFD), `b93de25` (OFP).

### Session 11 — Documentation Update
- `README.md`: Full rewrite — accurate feature list, ASCII architecture diagram, 7-scanner table, API overview with example, quick-start, project structure, screenshot placeholders.
- `AGENTS.md`: Updated with all session history.
- `docs/ARCHITECTURE.md`: Created concise reference covering data pipeline, 8-database layout, scanner framework, code conventions.
- `docs/screenshots/`: Created directory with `.gitkeep` and placeholder instructions.
- Commits: (separate commits per file).

### Session 12 — DCB Bargain Scanner
- **New scanner** `DCBBargainScanner` (`myra_app/strategies/dcb_bargain.py`): Computes Delivery Cost Basis (delivery-weighted accumulation price over 120-day window) and flags stocks trading below that institutional level with positive delivery absorption.
- **Backtest results**: TP=10% / SL=8% → 14 trades, 50% win rate, +6.6% net/trade, +₹9,264 P&L, 315 signals.
- **API endpoints**: `GET /api/dcb-bargain/status` + `POST /api/dcb-bargain/scan` in `myra_fastapi_server.py`.
- **Frontend view**: `myra_web/src/views/DCBBargain.tsx` — adjustable parameters (lookback window, market-cap range), result cards with delivery metrics.
- **Test count**: 312 tests, all passing.

### Session 13 — Wyckoff Look-Ahead Bias Fix
- **Eliminated look-ahead bias** in `WyckoffAutomaton._detect_events`: all baselines (`avg_vol`, `avg_del_pct`, `range_low`, `range_high`) switched from whole-df globals to **expanding (rolling-to-signal-day) series** — a signal only sees data up to its own candle. `exp_avg_del` uses pandas `skipna` semantics (old global `np .values.astype(float).mean()` was NaN-poisoned by any NaN `delivery_pct` in the window).
- **`event_date` semantics**: two-candle-confirmed Springs are now dated on the **confirmation candle** (`abs_i+1`) instead of the grab candle; `days_since` flows from `event_date` automatically.
- **`range_low_90`/`range_high_90`** are now signal-local (rolling up to the event candle), not window-global.
- **Equal-low zone** detection no longer scans forward (past + grab candle only).
- **Other fixes**: legacy-schema fallback SELECT now emits `NULL AS swing_low` (13 values, `COLUMNS_13` order); AR/ST dedup matches `event_type` AND `event_date` (different types on same date preserved).
- **Tests**: 8 new (rolling-not-global for SC/SOS/avg_del, equal-low future-look, two-candle date shift, schema fallback, dedup helper). Commit: `fix(wyckoff): eliminate look-ahead bias and fix related bugs`.

### Session 14 — Wyckoff spring_score calibration dress + point-in-time fundamentals history
- **spring_score calibration** (`tools/calibrate_wyckoff_weights.py`, 800-combo random search, leak-free 70/15/15 split): **ABANDONED** — best-on-train VALIDATION Q5-Q1 −2.14% vs shipped defaults +11.21% (gap −13.35%), so weights 30/30/20/10+10/5 kept optimal. Added `weights` override on `WyckoffAutomaton.__init__` + lock tests. Commits: `7e6a74c`, `366da25` (docs/PERFORMANCE.md).
- ~~**Point-in-time fundamentals history** for leak-free mcap calibration (deferred)~~: **SUPERSEDED in Session 15 — removed.** New `fundamentals_history` table + `myra_app/backfill_fundamentals.py` (yfinance, monthly default, idempotent `INSERT OR REPLACE`, `.NS`/`.BO` fallback, `--start/--end/--daily/--limit/--symbols/--dry-run`); `WyckoffAutomaton._resolve_pit_mcap` (as-of `date <= x`, snapshot fallback + warn) + Spring `point_in_time_mcap` field — scoring byte-identical (proven by test). Commits: `6692949`, `d920ce6`, `f13ca3c` (log-spam fix, tri-state flag + 2 regression tests). **All of this infra was deleted in Session 15** (backfill script, table, schema_registry entry, PIT methods, 16 tests).
- ~~**Next (user runs)**~~: the full `fundamentals_history` backfill and mcap-in-`_event_quality` calibration were **cancelled — replaced** by Session 15's price-ratio approach (no backfill needed).
- **Process note (anti-pattern, logged)**: on 2026-08-29 the orchestrator emitted a long loop of near-identical intent lines (`Pushing now.` / `Let me push.` / `<verb> the commit.`) without progressing to the single tool call. Fix recorded in `.agent/rules/03-guardrails.md` §"Act, Don't Narrate": state intent once, then immediately make the tool call; never repeat intent text. (Note: claude-mem worker store was unavailable to write at the time — daemon down, `npx claude-mem` hung on a network install — so this observation was recorded here in AGENTS.md instead. The same loop recurred 2026-08-29 Session 15 during mcap implementation.)
- **Process note 2 (session sprawl)**: Session 15 produced an unusually long intent-repetition cascade across many turns; each "are you done? / finish what you were doing" pause restarted the same narration instead of one tool call per turn. `.agent/rules/03-guardrails.md` §"Act, Don't Narrate" is the standing fix — one intent line + the tool call in the SAME turn, never repeat.

### Session 15 — Price-adjusted market cap in Wyckoff (replaces fundamentals_history backfill)
- **Removed the entire `fundamentals_history` approach**: deleted `myra_app/backfill_fundamentals.py`, the schema_registry `fundamentals_history` entry, `tests/test_fundamentals_history.py` (16 tests), dropped the DB table, and stripped `WyckoffAutomaton` PIT infra (`_load_pit_history`, `_resolve_pit_mcap`, `_pit_warned`/`_pit_history_available` tri-state, Spring `point_in_time_mcap` field).
- **Added `_get_historical_mcap(df, symbol, as_on_date)`**: leak-free approximation `current_mcap * (price_t / current_price)` using the per-symbol `df` already in `_detect_events` (NOT `_bulk_data` — bulk/DB parity-safe). Lazy `fundamentals` fallback memoized into `_current_mcap_map`.
- **Scoring**: Spring `quality` = `del/75*50 + rec/5*50 + mcap_weight * ln(historical_mcap)` (default `mcap_weight=20`), passed via `extra["historical_mcap"]`; missing mcap → plain base. SC/AR/ST/SOS untouched. `_event_quality` became an instance method.
- **Parity bug caught by tests**: an early version read prices from `_bulk_data`, which made bulk-vs-DB paths diverge (parity test failed). Refactored to use the caller's `df` — `test_bulk_loader::test_wyckoff_parity` green again.
- **Saturation finding (open, needs sign-off)**: with raw-₹ mcap, `20*ln(8e9)≈450` → **100% of Spring `event_quality` = 100 (std 0)** on a live scan — zero discrimination. Weight was NOT silently retuned (guardrail); `mcap_weight` default kept at 20 per task brief. Recommendation: normalize the log input (e.g. mcap in ₹ Cr → `2.95`, or ₹1e9 → `2.08`) or drop the weight to ~2; pending user decision.
- **Tests**: new `tests/test_wyckoff_mcap.py` (13 tests: ratio math, missing-price/mcap/df/zero guards, lazy fallback, Spring quality factor on/off, SC/SOS isolation) + `assert mcap_weight == 20` in defaults lock test. Canonical suite: **413 passed, 1 skipped**.
- Commit: `perf(wyckoff): add price-adjusted market cap to quality score (remove backfill)`.

## Current Project State

| Metric | Value |
|--------|-------|
| Python version | 3.12 |
| Total data rows | ~2.25M (technical_data) |
| Symbols tracked | 3,000+ |
| Fundamentals symbols | 2,309 (with promoter data) |
| Enrichment completion | 99.4% |
| Databases | 8 SQLite sidecars (WAL mode) |
| Registered scanners | 7 (Trigger, Float Exhaustion, Invisible Hand, Wyckoff, LFD, OFP, Seasonal Delivery) + 1 endpoint-only (DCB Bargain) |
| Portfolio Tracker | ✅ | CLI tool with auto-refresh, scanner overlap, risk metrics, smart caching |
| Scanner candidates (typical) | 2–623 per run |
| ML models | 2 (forward_return.ubj + launchpad_xgb.joblib) |
| Test suite | 413 passed + 1 skipped, all green |
| CI | GitHub Actions (push/PR to main) |

## Architecture References
- `myra_app/schema_registry.py` — 32 table schemas
- `myra_app/librarian_core.py:52` — `DB_MAP` database filenames
- `myra_app/feature_enrichment.py:140` — daily enrichment pipeline
- `myra_app/feature_enrichment.py:494` — batch `enrich_from_dataframe`
- `myra_app/fundamental_sync.py` — `MS_CANONICAL_MAP` (10 camelCase→snake_case mappings)
- `myra_app/background_orchestrator.py` — daemon thread management
- `tools/enrich_history.py` — optimized batch backfill
- `myra_app/strategies/` — 57 scanner strategy files (7 registered via API)
- `myra_app/strategies/dcb_bargain.py` — DCB Bargain scanner (Delivery Cost Basis, endpoint-only)

## Key Decisions
- camelCase → snake_case mapping happens in `_merge_and_insert()` at record-building time, not in the MS API fetch layer.
- Consolidation backfill is idempotent: `UPDATE WHERE (canonical IS NULL OR canonical = 0) AND alias IS NOT NULL AND alias != 0`.
- SchemaRegistry only ADD COLUMNS (ALTER TABLE), never drops.
- Calendar-vs-trading-day conversion uses factor 0.6 (conservative 5/7 ratio).

---

## Phase 2 — security, dataflow & correctness audit (2026-10-13)

Starting point: `d47ad4c` (Phase 1 on `origin/main`, suite 1111 passed). Full write-up in
`docs/PHASE2_AUDIT.md`.

- **Workstream 1 — security:** 3 open Dependabot alerts fixed; GitHub now reports **0 open / 26 fixed**.
  `black` pinned `23.3.0 → 26.10.1` (24.x still hit PYSEC-2026-2120/2121), npm `source-map-js 1.2.2`
  + `proxy-addr 2.0.8` (lockfile-only). `pip-audit` + `pnpm audit` clean. Commits `7be28e4`, `5a53b79`.
- **Workstream 2 — dataflow:** mapped ingest → store → enrich → scan → rank → surface (daily/EOD only).
  Freshness can't reuse `Librarian.get_expected_trading_day` (circular via `get_max_price_date`).
- **Workstream 3 — defects fixed:**
  - `fix(freshness)` `ec2c40f`: `/api/data-health` reported **calendar** days behind → false red every
    Monday/post-holiday. Added `trading_day_freshness()` (`myra_web/utils.py`), `trading_days_behind`
    + `expected_trading_date` fields, UI prefers the session count. +`tests/test_health_freshness.py` (8).
  - `fix(ranking)` `94ce6a1`: `apply_global_ranking` raised `KeyError` on missing `Stage`/`Stars` and the
    broad `except` swallowed it → silently unsorted output. Guarded access + warning log.
    +`tests/test_results_manager.py` (10) — path previously had no tests.
- **Workstream 4 — perf:** baseline = `pytest tests/test_bulk_loader.py` (~125 s module); documented in
  `docs/PERFORMANCE.md`. Deferred (documented, not implemented): `confluence_batch.py` reloads the whole
  universe once per scanner (~12x) — needs an ingest-invalidated shared cache.
- **CI repaired (was red, never ran a test):** `test` died at `pip install` because `requirements.txt`
  listed bare Windows-only `pywin32` (`9b61e05`). Once running, CI exposed three more real defects:
  a Polars 2.0 hard crash (`min_periods` removed — fixed to `min_samples` in 10 calls, `4dcf517`),
  a live-DB guard bypass where `_candidate_paths` did `lstrip("/")` and broke absolute POSIX URIs
  (`c01d239`), and 11 tests that assumed live data (`ee00aa7`). **CI now green: test + lint +
  frontend-build** (run 38377763169).
- **Verification:** full suite **1129 passed** / 0 errors (418.89 s, 41% cov); black 26.10.1 + flake8 clean;
  `tsc` + `pnpm build` green; `pip-audit` clean; CI green on ubuntu / py3.12 / polars 2.0.0.

---

## Phase 3 — confluence batch sharing, model format, regression tests (2026-10-13)

Starting point: `9cb2a7d` (Phase 2 on `origin/main`, suite 1129 passed).

- **Perf — shared confluence bulk load:** added a batch-scoped, thread-local OHLCV
  snapshot to `myra_app/db/bulk_loader.py` (`_BatchSnapshot`, `_Scope`,
  `batch_snapshot_scope`, `latest_ohlcv_date`) and wrapped the scanner loop in
  `myra_web/confluence_batch.py`. Scanners that ask for an already-covered
  window/symbol set are now served from one in-memory read; uncovered requests
  widen the window (date/symbol union) and reload once, publishing only on
  success. Live batches prewarm `[latest − 545d, as_on] × ALL`.
  - **Measured (live DB, as_on=2026-10-12):** 11 universe loads → **1**; DB read
    time **103.1 s → 48.6 s**. Wall-clock was flat (795.6 s → 810.3 s) because
    slicing materialises per-request copies — the win is deterministic *work
    reduction*, not wall-clock. Slicing switched from boolean-mask to
    `searchsorted`+`iloc` (64.0 s → 14.65 s over the 9 windows).
  - `+tests/test_bulk_loader_scope.py` (18) — sharing/widening, isolation,
    lifecycle/errors, batch wiring, live parity.
- **Model serialization:** canonical artifact is now `models/forward_return.ubj`
  (`MLTrainer.MODEL_PATH`), with `LEGACY_MODEL_PATH` = `models/forward_return.xgb`
  fallback in `_resolve_model_path()`. XGBoost picks the format from the
  extension, so the `.ubj` name removes both the save ("UBJSON as default") and
  load ("Unknown file format: `xgb`") UserWarnings. `/api/ml/status` accepts
  either artifact. `+tests/test_ml_trainer_model_format.py` (5).
- **Deps:** removed unused `express` / `@types/express` from `myra_web/package.json`
  (lockfile −56 packages; `pnpm build` green).
- **Regression tests:** `+tests/test_score_components_v2.py` (15) and
  `+tests/test_positional_engine.py` (7) lock the current scoring numerics.
- **Pre-existing issues found (not changed — would alter rankings):**
  - `score_components_v2.precompute_ranks` uses
    `rank(pct=True, na_option="bottom")` with a comment claiming missing data
    gets the *lowest* rank. In pandas, `na_option="bottom"` assigns NaN the
    *highest* percentile (1.0), so absent `roe`/`ProfitGrowth` are scored as
    top-tier. Documented by `test_precompute_ranks_nan_gets_highest_pct_rank_current_behaviour`.
  - `PositionalScorer.compute_score` guards `if f is not None`, but
    `fundamental_score` never returns `None`, so the fundamentals blend always
    applies (with fallback ranks) — the branch is dead.
  - Environment drift: venv now runs **xgboost 3.4.0** (notes previously said
    3.2.0); `save_model` accepts only `fname` (no `format` kwarg).
  - Order-dependent test isolation: `fixture_db_dir` (`tests/conftest.py:331`)
    is **session-scoped** and redirects every `DB_DIR` binding to a synthetic DB
    for the remainder of the session. Because collection is alphabetical,
    `test_bulk_loader.py`/`test_chart_endpoint.py` run *before*
    `test_db_isolation.py` and see the live DB; running `test_db_isolation.py`
    first starves them (`bulk == {}` → 13 failures). Default order and CI are
    unaffected, but the suite is order-sensitive.
- **Verification:** full suite **1172 passed** / 0 warnings (234 s); black 26.10.1 +
  flake8 clean on touched files.

### Next up (Phase 4 candidates)

1. Decide whether the `na_option="bottom"` missing-data ranking is intended; if
   not, fix + retrain/re-rank (scoring semantics change — needs sign-off).
2. Re-measure confluence batch wall-clock on a quiet machine before claiming any
   end-to-end speedup.
3. Make `fixture_db_dir` order-independent (e.g. function-scoped restore, or an
   autouse reset of `DB_DIR` bindings for modules that don't request the
   fixture) so live-DB tests no longer depend on collection order.

---

## Phase 4 - order-independent DB test isolation (2026-10-14)

Starting point: `7eae58d` (Phase 3 on `origin/main`, suite 1172 passed). Addresses
Phase 3 "Next up" item 3. Full write-up in `docs/PHASE4_TEST_ISOLATION.md`.

- **Root cause:** `fixture_db_dir` (`tests/conftest.py`) was **session-scoped**.
  Its `_redirect_db_dir()` rewrites `constants.DB_DIR` and every import-time
  `DB_DIR` binding, and its teardown ran only at session end - so every test
  after the first `fixture_db_dir` request resolved `DB_DIR` to the synthetic
  directory. Modules that resolve DB paths at *runtime*
  (`myra_app.db.bulk_loader._tech_db_path()`, `/api/chart`) then read fixtures.
  Alphabetical collection hid it (`test_db_isolation` sorts after them).
- **Fix:** split the fixture. `_pristine_fixture_dbs` stays session-scoped (one
  stable directory - several callers cache `LibrarianCore` by thread, not path,
  so a per-test directory would strand those pools); `fixture_db_dir` is now
  **function-scoped**: redirect + `_install_live_db_guard()` per requesting test,
  full restore in teardown (`DB_DIR`, import-time bindings, `sqlite3.connect`,
  cached connections).
- **Latent bug fixed:** `test_uri_bypass_cannot_escape_the_guard` restored a bare
  `sqlite3.connect` in its `finally`, leaving the process unguarded for the rest
  of the session. It now re-arms the guard.
- **Pre-existing leak surfaced & fixed:** the function-scoped `_assert_isolated`
  (re-checked every isolated test) exposed `myra_app.ingest_bhavcopy.DB_DIR`
  stuck on a deleted temp dir. Cause: `tests/test_market_calendar_guard.py`'s
  `cal` fixture patched existing modules, but tests import `ingest_bhavcopy`
  *inside* the test body, so it snapshotted the temp dir after the patch loop.
  Added a teardown finalizer that releases any late `DB_DIR` binding.
- **Fixture integrity:** `_validate_fixture_dbs()` fails the run if a fixture DB
  has no tables or lacks a key table (`sync_log` / `technical_data` /
  `fundamentals`), so an "exists but empty" fixture cannot pass vacuously.
- **Tests that only passed because of the leak:** removing it broke 5 tests on
  CI (no live DBs) that pass locally (live DBs present):
  `test_query_endpoint.py::TestEndpointIntegration` (2) and
  `test_super_breakout_scanner.py::TestSuperBreakoutNearTrigger` (3) - both read
  the technical DB that the leak had redirected to fixtures. Both now request
  `fixture_db_dir` explicitly.
- **Regression tests:** `+tests/test_db_isolation_order.py` (7) pins the
  restore/no-leak contract, re-arm-after-removal, directory stability, and
  fixture non-emptiness. Confirmed failing (3) under the old session scope.
- **Verification:** previously-failing order
  (`test_db_isolation` + 8 starved modules) **13 failed/29 passed  132 passed**;
  three-module order **13 failed/29 passed  42 passed**; alternate orders (A, B)
  green; full suite **1179 passed** / 0 errors (255 s); black 26.10.1 + flake8
  clean. CI (ubuntu, no live DBs) **1158 passed, 21 skipped, 0 failed**; CodeQL
  green (commit `76504b5`).
