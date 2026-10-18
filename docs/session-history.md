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

---

## Phase 5 - ranking & scoring integrity (2026-10-14)

Starting point: `4763f86` (Phase 4 on `origin/main`, suite 1179 passed). Resolves
the Phase 3 "Next up" item 1 (`na_option="bottom"`). Full write-up in
`docs/PHASE5_RANKING_INTEGRITY.md`. Directive: correctness before score
optimisation; no weight retuning, no renormalisation, no favourable defaults for
missing inputs.

- **D1 (HIGH) - the rank layer was inert in production.** `PositionalScorer.rank`
  passed `itertuples()._asdict()` to the scorer; `itertuples` renames every
  leading-underscore column (`_rank_delivery` -> `_0`), so `_rank_*` never
  arrived and delivery / liquidity / ROE / growth **always** used neutral
  fallbacks. 60% of the advertised cross-sectional signal contributed nothing.
  Fixed with `to_dict("records")`. (Same trap already documented at
  `tools/backtest_bulk_deals.py:1080`.)
- **D2 (HIGH) - missing data earned the top percentile.** `precompute_ranks`
  used `rank(pct=True, na_option="bottom")` (comment claimed the opposite; pandas
  assigns NaN the *largest* rank). Fixed: mask `+/-inf`, `na_option="keep"` ->
  NaN -> documented neutral fallbacks; ranks computed over available values
  only; function now returns a copy (no input mutation).
- **D3** `fundamental_score` NaN `F_Score` propagated into `MYRA_Score_v25` ->
  now 0. **D4** `valuation_score` NaN `graham_number` fell to the 0.2
  "Expensive" band (`NaN <= 0` is False) and `close == 0` raised -> now neutral
  0.5 (latent; not called in prod). **D5** removed the dead `if f is not None`
  fundamentals guard. **D6** `myra_web.utils._TIER_RANK` only knew `MID`, but
  scanners emit `MOD`, so `_grade_rank("MOD")` scored 0 - below `LOW` (1.5) in
  the confluence best-grade -> added `MOD: 2.5`. **D7** `rank` now uses
  `kind="mergesort"` (deterministic ties) and tolerates a `None` drawdown.
- **Explainability:** new `PositionalScorer.explain_score(row, regime)` and an
  opt-out `MYRA_Score_v25_breakdown` column from `rank(..., explain=True)`
  (components, weights, regime, drawdown multiplier, `missing_inputs`), produced
  by the same single pass as the score so it cannot drift. Column added to
  `results_manager`'s `std` list so it is not rendered as a CLI "hero" metric.
- **Unchanged by design (locked by tests):** weights 0.25/0.15/0.20/0.10/0.10 +
  0.30, 70/30 split, regime 1.15/0.9, drawdown 0.8/0.95, and every known scalar
  (`_BULL` 71.1, FEAR 81.8, GREED 64.0, dd 67.5/56.9, minimal 20.3).
- **Before/after (deterministic fixtures):** identical-technicals pairs differing
  only in delivery/volume/smart-money. All-present: `47.4/47.4 tie -> 69.5/59.0`.
  Missing-vs-weak: `47.4/47.4 tie -> (with bottom ranks) NO_DATA 69.5 > WEAK 59.0
  bug -> 54.8 vs 69.5 (fixed)`. Proves D1 and D2 had to be fixed together.
- **Tests:** `tests/test_score_components_v2.py` (+8, replaced the Phase 3
  bug-locking test), `tests/test_positional_engine.py` (+9, incl.
  `test_rank_consumes_precomputed_ranks` which fails without D1),
  `tests/test_dcb_defaults.py` (+2 for D6).
- **Verification:** full suite **1197 passed** / 0 failed / 0 skipped (360 s);
  black 26.10.1 + flake8 clean on touched files.
- **Deferred (documented, not changed):** `stability_score` absent-key `0.0` vs
  NaN `0.3`; `universe_loader` zero-defaults conflating absent with 0 across
  ~40 columns; `EXTREME GREED` regime branch unreachable + `"Neutral"` /
  `"NEUTRAL"` mismatch; ranks are within-scan, not universe-wide.
- **Next phase:** populate the rank sources (`avg_delivery_20d`,
  `avg_volume_20d`, `smart_money_score`, `roe`, `ProfitGrowth`) on the real scan
  path so the now-correct rank semantics discriminate candidates, with a
  shadow-mode ordering comparison first.

---

## Phase 6 - live ranking-input integration, shadow mode (2026-10-14)

Starting point: `b2957e6` (Phase 5 on `origin/main`, suite 1197 passed). Resolves
the Phase 5 "Next phase" item. Full write-up in
`docs/PHASE6_LIVE_RANKING_INTEGRATION.md`. Directive: connect only
legitimately-produced sources; integrate nothing that would require inventing a
metric; keep the populated ordering **non-authoritative** behind an opt-in
switch; measure before shipping.

- **Audit - three of the five `precompute_ranks` inputs had no producer.**
  `avg_volume_20d`, `avg_delivery_20d` and `smart_money_score` were only ever
  *read* (with a `0` default) in `universe_loader`. `avg_delivery_20d` did have a
  producer once: `90d2cc1` wrote `SMA20(Delivery_qty)` / `SMA20(Volume)` into the
  Parquet indicator lake, then `aa7016e` (2026-04-17, "Refactor
  precompute_indicators method") dropped both and renamed delivery to
  `avg_deliv_20d` (still quantity). Evidence it once ran:
  `myra_reports/scan_rs_rating.csv` carries `avg_delivery_20d` at delivery
  *quantity* scale (295734.1, 7166426.3) - a legacy latent bug, since delivery
  quantity is a company-size proxy that double-counts volume. `smart_money_score`
  has no producer and no definition (consumers only: `worker_pool.py:285`,
  `strategies/smart_money.py:63,64,91`, `results_manager.py:310`) - left **absent**
  rather than faked from `ias`/`rdv`.
- **Definitions locked (documented with rationale).** `avg_volume_20d` = mean
  `technical_data.volume` over 20 **trading sessions**; `avg_delivery_20d` = mean
  daily **`delivery_pct`** (NSE `DELIV_PER`, 0-100) over 20 sessions - the
  definition `myra_app/ml_trainer.py` already uses (`AVG(delivery_pct) OVER w20`) -
  deriving `delivery*100/volume` only when `delivery_pct` is NULL (mirrors
  `tools/db_doctor.py:604`, `mass_backfill.py:217`); `roe` = `fundamentals.roe`
  percent, passed through with stored `0` preserved and only SQL `NULL` -> `None`.
  A full window is required, otherwise the metric is **absent** and the scorer
  applies its Phase 5 neutral fallback (never a shortened average ranked as a
  20-day one). `as_of_date` bounds the window so no future session is read.
- **Integration is opt-in and defaults off.** New `myra_app/ranking_inputs.py`
  (`compute_ranking_inputs`, `compute_fundamental_inputs`, `attach_ranking_inputs`,
  `ranking_inputs_enabled`, `ENV_FLAG = MYRA_V25_RANKING_INPUTS`).
  `load_universe(..., populate_ranking_inputs=None)` merges the metrics into
  `funda_map`, which `worker_pool.py:273` already spreads into every result row,
  so they reach `precompute_ranks` with no scanner/worker/scorer signature change.
  `screener.py`'s `ROE`/`ProfitGrowth` placeholders became gap-fill-only so a real
  value can never be clobbered by `-`/`0` (with the flag off the row is
  byte-identical).
- **`ProfitGrowth` could not be integrated.** Live `fundamentals` (read-only
  probe): 3948 rows, `roe` non-null 691 (percent scale, SME-heavy), `profit_growth`
  **0**, `earningsGrowth` **0**, `revenueGrowth`/`sales_growth` 0. The column
  mapping is correct (`fundamental_sync` `earningsGrowth -> profit_growth`); the
  values are simply absent. Documented as unavailable, with the
  `COALESCE(profit_growth, earningsGrowth)` bridge noted for when the sync lands.
- **Perf finding + fix.** The naive windowed query (`symbol IN (...)`,
  `ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC)`) made SQLite scan
  the whole ~8 M-row table: **27.4 s** for 938 symbols. Anchoring the window to
  the real trading dates (`date >= lower AND date <= upper`) lets it use
  `idx_tech_symbol_date`: **2.14 s** (as-of) / 1.75 s (latest). Value impact
  measured, not assumed: 793 -> 785 symbols emit inputs and 14 change behaviour -
  in every case the rolling means become **absent** rather than changing value
  (8 drop out of the merged map entirely - `UMANGDAIRY`, `IWEL`, `PEL`,
  `UDAICEMENT`, `ISEC`, `IEL`, `HINDMOTORS`, `MANGCHEFER`; 6 keep a partial
  window, e.g. `SHIVAUM`/`MASKINVEST`/`NKIND`/`PANSARI`/`MODTHREAD` at 19
  sessions, plus `JPASSOCIAT` whose technical rows dropped out while its
  fundamentals row remained). Those are exactly the names whose "last 20
  sessions" would have spanned months.
- **Shadow measurement (historical snapshots + live read-only DBs, not fixtures).**
  `scan_rs_rating.csv` (938 candidates): Spearman **0.6698**, top-20 overlap
  **13/20**, coverage `roe=71 / ProfitGrowth=0 / avg_delivery_20d=654 /
  avg_volume_20d=654`. `scan_multibagger_early.csv` (385): Spearman **0.8855**,
  overlap 14/20. New `tools/compare_v25_orderings.py` (opens every DB with a
  `file:...?mode=ro` URI - cannot write or create WAL sidecars) and
  `myra_app/shadow_ranking.py` (`compare_orderings` builds **both** orderings from
  one result set, so the comparison cannot come from two different snapshots).
  Caveat recorded in the doc: the snapshots are degenerate (713/938 rows share a
  single score; only 225 rows carry any technical input; `pct_above_ma50_60d` and
  `atr_pct` are single-valued and `F_Score` is absent), so `moved 931 / max delta
  739` is largely tie-breaking - the rank correlation and top-20 overlap are the
  load-bearing numbers, and **no improvement claim is made**.
- **Coverage explanations (verified, not guessed):** the `avg_*` coverage tracks
  symbol presence in `technical_data` (3423 symbols; 656 of the 938 snapshot
  symbols present; 654 with a full window), not the window logic; the low `roe`
  coverage is a fundamentals-population gap (0 dotted symbols in the table, so the
  join is not missing rows).
- **Tests:** `+tests/test_ranking_inputs.py` (14: full/short window, derived
  delivery %, all-NULL delivery, `as_of_date` look-ahead guard, case-insensitive
  alignment, empty inputs, fundamental passthrough incl. stored `0` kept,
  attach purity/no-clobber, flag resolution incl. explicit-beats-env,
  `_populate_ranking_inputs` end-to-end) and `+tests/test_shadow_ranking.py` (6:
  empty results, `inputs={}` -> identical ordering + Spearman 1.0, coverage,
  determinism, top-N overlap).
- **Verification:** full suite **1217 passed** / 0 failed / 0 skipped (1197 + 20
  new); `black` 26.10.1 + `flake8` clean on all touched files; performance guard
  **268 violations / 0 CRITICAL** = the pre-existing baseline (new files add none).
- **Deferred (documented):** `smart_money_score` still undefined (so
  `delivery_score`'s 0.6 leg stays neutral); `ProfitGrowth` unpopulated; `roe`
  coverage 7.6% on a large-cap universe; `technical_data` session gaps (282/938
  snapshot symbols absent entirely); no `last_fundamental_update` staleness guard;
  ranks remain within-scan; `universe_loader` still `0`-defaults ~40 cache
  columns.
- **Next phase:** fund the data, not the scoring - populate `profit_growth`,
  widen `roe`, add a freshness guard, and investigate the `technical_data` session
  gaps before promoting the populated ordering to the default (with out-of-sample
  evidence rather than an in-sample correlation).

## Phase 7 - data integrity, technical coverage & fundamental freshness (2026-10-14)

Full write-up: `docs/PHASE7_DATA_INTEGRITY_AND_COVERAGE.md`. Starting commit
`768852e`, final commit `9575853`. Four work-streams; the only *application* fix
was a real reader crash (the metadata-write fix is entirely in the test harness).

- **WS1 (metadata-DB write) - root cause found and fixed.** The suite really did
  write production: **`tests/test_task_offload.py`** calls `_spawn_task` ->
  `task_tracker.register` -> `INSERT INTO task_registry`, and the module never
  requested `fixture_db_dir` (which is opt-in), so `DB_DIR` stayed live and the
  module wrote the live `myra_metadata.db` - **3 rows per full-suite run**
  (`task_registry` 2529 -> 2532). A row-digesting per-test probe pinned the exact
  three tests. It stayed hidden because (a) `fixture_db_dir` only redirects tests
  that ask for it, (b) the write is **WAL-only** so size/mtime were unchanged, (c)
  the mtime moved only at process exit when SQLite checkpointed the WAL - the
  Phase 6 signal - and (d) the first probe compared row *counts* from behind the
  live-access guard, so it returned equal error sentinels ("0 changes").
  **Fixes (no app code changed):** the module now has an autouse
  `_isolated_db(fixture_db_dir)` fixture; `tests/conftest.py` counts committed WAL
  frames (`_wal_committed_frames()` on the WAL header, magics `0x377F0682/3`,
  appended to `_db_signatures()` as `:wal_frames=N` when `N > 0`) so the guard can
  finally see such a write - it caught this one as `wal_frames=12`; and a new
  section-8 test requires every module that calls `_spawn_task` to request
  `fixture_db_dir`. No remaining uncertainty (the 3 residual production rows are
  expired `Done` rows the app purges itself).
- **WS2 (technical coverage) - measured, one real bug fixed.** `technical_data` =
  **8,009,058 rows** (AGENTS.md's "~2.25 M" is wrong), 3,423 symbols, 7,858 dates,
  1995-01-02..2026-10-12, ~3,165-3,231 rows on each of the last 25 dates, 39
  complete sessions since 2026-08-17 with **no missing trading date**. Snapshot
  universe `scan_rs_rating.csv` (938): **639** full 20-session symbols, 458
  partial, 292 absent; histogram `{0:292,5:2,9:1,11:1,17:1,19:2,20:639}`.
  `open/high/low/close/volume/delivery/delivery_pct` are 100% populated;
  `vwap`/`delivery_qty`/`stock_return` are NULL across all 8.0 M rows;
  `delivery_ratio`/`trades` exist only from 2026-08-17. `delivery` =
  `delivery_pct*volume/100` exactly (i.e. `delivery` **is** the quantity, and
  `delivery_qty` is an orphan). 1,191 of 3,610 `symbols_master` symbols have zero
  technical rows (ETFs, `-RE` rights, SME-base tickers). Root causes confirmed
  (IPOs / illiquid-SME / universe inflation / dead + new columns); none
  unexplained; no data back-filled (that would be fabrication). **Bug fixed:**
  `myra_app/portfolio_db.get_delivery_alerts` selected the never-populated
  `delivery_qty` and divided it by volume -> `TypeError` its `except
  sqlite3.Error` missed -> `tools/portfolio.py` crash and web delivery alerts
  silently never firing (`myra_web/routes/portfolio.py` swallows it). Fixed with
  `COALESCE(delivery_qty, delivery)` (+ `close`); `+tests/test_portfolio_delivery_alerts.py`.
- **WS3 (fundamentals) - diagnosis only, nothing integrated.** `fundamentals` =
  3,948 rows / 3,948 symbols. Coverage: `sector` 98.9%, `net_margin` 83.8%,
  `pe` 83.0% (39 zeros), `market_cap` 82.2%, `shares_outstanding` 84.1%,
  `promoter_holding_pct` 81.0% (56 zeros), `free_float_pct` 67.9%,
  `dividend_yield` 38.2% (37 zeros), `inst_holding` 18.4% (267 zeros,
  **untrustworthy** - values ~0.5-4.7 tagged `UPSTOX`), `roe` **17.5%**, `eps`
  11.1%, `book_value` 11.0%, `face_value` 1 row; **0 rows** for
  `profit_growth`/`sales_growth`/`debt_to_equity`/`roe_ttm`/`operating_margin`/
  `pledged_pct`/`beta`. Unit traps: `roe` is a **percent** but `net_margin` is a
  **fraction**; `market_cap` is absolute INR. Freshness is three overlapping
  columns - `date` = source date, `last_updated`/`last_fundamental_update` =
  ingestion - so a staleness guard must key on `date` (none exists today). 992 of
  3,948 fundamental symbols (~25%, incl. 527 `_SME`) are not in `symbols_master`.
  `quarterly_results` (9,095 rows / 745 symbols) is the one legitimate provenance
  for `profit_growth` (`(np_t - np_{t-4})/|np_{t-4}|*100`) but was **deliberately
  not integrated** - it would be a new metric definition feeding the score.
- **WS4 (validate the Phase 6 opt-in) - validated, not promoted.**
  `ranking_inputs_enabled()` = `False` by default; the gate at
  `myra_app/universe_loader.py:321` is inert unless switched on.
  `tools/compare_v25_orderings.py` ran end-to-end and left every live DB's
  main-file size/mtime **unchanged** (sig-hashed before/after). Re-measured:
  Spearman **0.6625**, top-20 overlap **13/20**, coverage `roe=71 /
  ProfitGrowth=0 / avg_delivery_20d=639 / avg_volume_20d=639` - same conclusion as
  Phase 6 (moderate re-ordering, **no improvement claim**). Added
  `+test_diagnostics_run_read_only_and_write_nothing` (SHA-256 unchanged over a
  `mode=ro` connection) and `+test_duplicate_symbols_produce_one_entry_and_no_row_multiplication`.
- **Integrity:** all 15 `.db` files under `myra_app/db/` pass
  `PRAGMA quick_check` (technical 59.8 s, valuation 11.1 s); no corruption. Three
  pre-existing **empty stray** files (`myra_meta.db`, `technical.db`,
  `valuation.db`) are not in `DB_MAP` and are left untouched (deleting live-dir
  files is out of scope) - recommended for owner-confirmed removal.
- **Tests / verification:** new `tests/test_portfolio_delivery_alerts.py` (+1),
  `+2` in `tests/test_ranking_inputs.py`, `+3` in `tests/test_db_isolation.py`
  (2 WAL + 1 task-registry isolation), the `tests/test_task_offload.py` fix, and
  the WAL guard in `tests/conftest.py`. Focused runs: **51 passed** (14.49 s) and
  **48 passed** (18.59 s). Full suite **1223 passed / 0 failed** (265 s) with the
  guard clean. `black` 26.10.1 + `flake8` clean; performance guard **268 / 0
  CRITICAL** (baseline unchanged).
- **Explicit:** default ranking order and scoring weights are **unchanged**
  (0.25/0.15/0.20/0.10/0.10 + 0.30; regime 1.15/0.9; dd 0.8/0.95; `_BULL` 71.1,
  EXTREME FEAR 81.8, EXTREME GREED 64.0, dd0.3 67.5, dd0.5 56.9, `{"close":1}`
  20.3) - the only code fix repaired a crash in a non-scoring alert reader.

## Phase 8 - pipeline orchestration, parallel execution & failure recovery (2026-10-14)

Full write-up: `docs/PHASE8_PIPELINE_ORCHESTRATION.md`. Starting commit `f22fdbc`,
final commit `2b3f42e` (Phase 8 = `4e85e06` + `2b3f42e`). Four changes: two
data-integrity/reliability fixes, one measured concurrency improvement, and one
CI-crash fix — all traced from the running code.

- **WS1 (dependency map).** The scheduled layer is **already concurrent** - each
  enabled `TaskSpec` in `myra_app/tasks/registry.py` runs as its own daemon
  thread (30 s stagger). The manual `PipelineControl` sequence is **sequential**
  by design and the **only** hard `depends_on` edge is `daily_ingest ->
  enrichment` (enrichment reads symbols produced by ingest). Shared-resource
  audit: `myra_technical.db` has one writer (daily-ingest, `_INGEST_LOCK`);
  `myra_valuation.db` has several (`fundamentals_*`, `market_cap_sync`,
  `shares_outstanding_sync`) serialized by WAL + `timeout=30`, all fill-only via
  `safe_write`; metadata (`sync_log`/`task_registry`) is intentionally
  multi-writer with thread-local connections.
- **WS2 (Screener.in, the slow enrichment).** Manual `--enrich-screener`
  (`db/enrichers/screener_enricher.py`) issued **5 requests/symbol** (fresh
  `Session` per call + a "prime" GET per metric) then a flat `sleep(1.5)` per
  symbol, single-threaded, with **one write transaction held for the whole
  multi-hour run**. Fix: **per-symbol commit** (thread-local connection opened /
  used / closed per symbol), **bounded `ThreadPoolExecutor` (4 workers, clamped
  to 16)**, a **global thread-safe `_RequestPacer`** that reproduces the
  original aggregate budget (**0.3 s/request** = 5 req / 1.5 s, so the request
  rate is *unchanged* - only idle time is absorbed), a shared thread-local
  `requests.Session` that removes the prime GET (**5 -> 3 requests/symbol,
  -40 %**), duplicate-run refusal (`_RUN_LOCK`), fill-only `safe_write.upsert_row`
  (inserts new symbols, never NULLs an existing metric), and **bounded retry with
  exp backoff** (transient/429/5xx retried; 404 permanent). Measured (local fake,
  40 symbols, 50 ms latency, real request counts): **70.4 s -> 37.8 s (1.86x),
  200 -> 120 requests**. The mature daily `sync_fundamentals` path already had
  per-symbol commits / cancel / backoff / parking and was left untouched.
- **WS4 (manual EOD CSV - the highest-value fix).** The active ingest
  (`constants.USE_EOD2_DATA = True` -> `eod2_sync.sync_eod2_data`) used a single
  **global `MAX(date)`** cutoff. With the manual copy workflow, a half-copied
  folder advances the global cutoff with the first ready file and every symbol
  still catching up is dropped **forever** - reproduced (temp DB): run 1
  inserted 3/10, run 2 (after the other 7 copied) inserted **0; 7 symbols
  permanently lost that day, silently**. Fix: **per-symbol cutoff** (one grouped
  query; `INSERT OR REPLACE` keeps it idempotent) + a correct "nothing to do"
  bail (least-advanced symbol at/past newest source date), plus
  `assess_source_readiness` that reports a partial / still-being-written /
  delivery-pending source set (per-file mtime stability, `DLV_PENDING_DATES`,
  fraction-at-newest-date) and **skips a file that is still changing** so a torn
  row is never stored. Threshold calibrated from the real source (94.7 % of
  files on a normal day; the 30 % repro fails it). Result now carries a
  `readiness` dict. Manual workflow itself is preserved - nothing auto-downloads.
- **WS3/WS5 (truthful status).** No new status system. The Screener.in backfill
  records `partial` (with `updated`/`failed` counts) or `failed` into the
  existing `sync_log` vocabulary via `_mark_task_run`; `tasks/ingest` keeps its
  `no_new_data` (ran, 0 rows, freshness not established) vs `success` model.
  `PipelineControl` is unchanged. EOD2 logs `INPUT DEGRADED: <reason>` when the
  source set is partial.
- **WS6 (DB isolation).** All new tests use `fixture_db_dir`; the Phase 7
  live-DB guard + committed-WAL-frame detection are untouched and green; no
  pytest-xdist. Added `test_sync_writes_only_to_the_redirected_database` after a
  **self-inflicted incident**: an early ad-hoc repro patched
  `myra_app.constants.DB_DIR` but `eod2_sync` binds `DB_DIR` by value at import,
  so it wrote 3 synthetic rows to the live `myra_technical.db`; they were removed
  with a precise predicate and the DB re-verified (`quick_check: ok`,
  **8,009,058 rows** - identical to the Phase 7 baseline, max date 2026-10-12).
  Every EOD2 test since runs through `fixture_db_dir`.
- **Tests:** `+tests/test_eod2_sync.py` (15), `+tests/test_screener_enricher.py`
  (25), `+tests/test_task_tracker_concurrency.py` (4). No live network, no
  production DB. Both pipeline fixes proven load-bearing by temporarily reverting
  them. Full suite **1267 passed** locally (was 1223, +44), 0 failed; **CI
  1246 passed / 21 skipped**; CodeQL green. `black` 26.10.1 + `flake8` clean;
  performance guard **268 / 0 CRITICAL** (baseline unchanged). Live DBs
  unchanged after the full run (technical 8,009,058 rows / max 2026-10-12).
- **CI segfault (WS6, second commit `2b3f42e`).** The first push failed CI with a
  **segmentation fault (exit 139)** even though all tests passed locally. Cause:
  `task_tracker` caches a `check_same_thread=False` SQLite connection in a module
  global; conftest's `_reset_cached_connections()` closed it from the main thread
  while a `myra_web/background.py` task thread was mid-`execute` on it (SQLite
  forbids use-while-close). A latent race the new Phase 8 threads exposed. Fix
  that **preserves the Phase 7 guarantee**: `_lock` is now an `RLock` and every
  helper resolves `_get_conn()` while holding it; new
  `task_tracker.reset_connection()` swaps+closes under that lock; conftest
  delegates to it. Pinned by the 4 new concurrency tests.
- **Not changed (deliberately):** no scheduler, broker, new service, schema, or
  status contract; no parallelization of `daily_ingest` (prerequisite for
  `enrichment`); no `pytest-xdist`; screening rules / ranking weights / the
  Phase 6 default-off setting untouched.
- **Unverified (documented):** the 1.86x backfill speed-up is synthetic (faked
  latency) + the exact 40 % request reduction; a real `limit=25` Screener.in run
  was not executed (live quota). `DEFAULT_MAX_WORKERS` left at a modest 4.

## Phase 9 - data readiness, crash recovery & change-driven Mission Control (2026-10-14)

Full write-up: `docs/PHASE9_DATA_READINESS_AND_MISSION_CONTROL.md`. Starting
commit `6b9273b`, final commit `7d0f585`. The governing rule: **a maximum date
is not completeness** — latest stored date, expected trading date and coverage
are three separate facts.

- **WS1 (readiness).** New `myra_app/data_readiness.py` reuses the existing
  authorities instead of reimplementing them: `MAX(technical_data.date)`,
  `tasks/watchdog._expected_trading_date()`, `eod2_sync.assess_source_readiness()`
  (Phase 8) and the `sync_log` row — so Mission Control and ingestion can never
  disagree. States: `ready` / `waiting_for_eod` / `partial` / `stale` / `failed` /
  `unknown`, each with a concrete explanation; missing evidence yields `unknown`,
  never a false assurance. Fundamentals report **reporting period**
  (`quarterly_results.period_end`) and **retrieval time**
  (`fundamentals.last_updated`) as separate, separately-labelled fields.
- **Calendar bootstrap defect (real, found by measurement).** `market_calendar`
  marks every Oct-2026 session as `is_trading_day=0 "Likely holiday (zero rows)"`
  — including 2026-10-12 which holds 3,200 rows. `is_trading_day` only lifts that
  heuristic when `technical_data` already has rows, so a session that is real,
  upstream, and *not yet ingested* stayed misclassified: the expected date read
  `2026-10-12` while the source already held `2026-10-14`, and MYRA reported
  itself **ready**. Added `daily_ingestor.has_source_evidence()` as a second
  override arm (bounded ≤200-file sample, heuristic reason only, named NSE
  holidays still authoritative). Expected date `2026-10-12` -> **`2026-10-14`**;
  `_is_db_stale()` `False` -> **`True`**; the live verdict is now **`partial`**:
  "Data is at 2026-10-12 but the EOD source already holds 2026-10-14".
- **Cost.** `assess()` is ~3.4 s uncached (3.2 s of it reading ~3,770 EOD CSV
  tails). The source scan is memoised against a validity token of (folder mtime,
  **newest file mtime**, meta.json mtime+size) + a 60 s TTL backstop — the newest
  file mtime matters because re-copying a session rewrites existing files without
  moving the directory mtime. `sync_eod2_data` calls
  `invalidate_source_cache()` right after a successful insert. Warm cost
  **0.18 s**; unchanged responses are **221 B** with no React re-render.
- **WS3 (live status) — mostly already correct, so documented not rebuilt.**
  Traced the real path: `usePipeline.ts` already uses SSE as primary with a 5 s
  poll *only while SSE is down and the tab is visible*, exponential reconnect
  backoff and a heartbeat watchdog; `MissionControl` and `DataSync` are separate
  routes so there is never a second concurrent connection; `get_status()` is
  in-memory (**16 µs**, 2.5 KB). Measured: an idle system emits **0** events.
  Fixed one real defect — `_publish_stage` re-emitted an identical stage on every
  call (SSE frame + client re-render for no new information); it now returns
  early on an unchanged stage. Readiness deliberately does **not** get a second
  SSE stream: it is a slow-changing verdict, not a transition stream.
- **WS4 (display).** New `DataReadinessPanel` on Mission Control (between the run
  header and the task list), fed by `GET /api/pipeline/readiness?changed_only=1`.
  Shows latest stored date, expected date, coverage, EOD source state, last
  successful ingest, and fundamentals reporting period vs retrieval, with a state
  chip and the backend's explanation. Unknown facts render "Not recorded". Manual
  task controls, layout and navigation unchanged; `tsc --noEmit` and
  `npm run build` clean.
- **WS2 (recovery).** Verified all ten required scenarios with controlled fakes
  (no live Screener.in): interruption keeps committed symbols, partial success
  reports `updated`/`failed` and records `partial` in `sync_log`, 429 is
  bounded-retry and slows the *shared* pacer, restarts skip cached symbols, a
  locked DB is counted as failed (never as an update), a status-write failure
  leaves committed rows intact, late/duplicate/partial EOD files reconcile with
  nothing lost. **New: cross-process duplicate protection**
  (`myra_app/utils/task_lock.py`) — Phase 8's guard was in-process only, so
  `run_pipeline.py --enrich-screener` alongside the API server would have
  double-fetched every symbol.
- **WS5/6 (regression protection) — a pre-existing leak found and fixed.**
  `tests/test_ingest_attempt_cooldown.py` assigned `eod2_sync.sync_eod2_data`,
  `constants.USE_EOD2_DATA` and `constants.ENABLE_DAILY_INGEST` **directly** and
  restored only two other names in its `finally`, so a fake `sync_eod2_data`
  returning `error="boom"` leaked into every later test in the process. Phase 9
  surfaced it as an order-dependent failure; now `monkeypatch`-based, with
  `test_ingest_tests_do_not_leak_a_fake_eod_sync` pinning it. Phase 7/8
  protections otherwise untouched: `test_task_offload.py` still isolated, the
  WAL-aware guard and `DB_DIR` tests green, the task-tracker lock unchanged, no
  pytest-xdist, readiness strictly read-only.
- **Tests / verification:** `+tests/test_data_readiness.py` (21) and
  `+tests/test_recovery_reconciliation.py` (16). Focused run **223 passed**; full
  suite **1304 passed / 0 failed** (was 1267, +37). `black` 26.10.1 + `flake8`
  clean; `tsc --noEmit` + `npm run build` clean; performance guard **268 / 0
  CRITICAL** (baseline unchanged, neither new module flagged). Live DBs
  unchanged after the run: `technical_data` 8,009,058 rows, max 2026-10-12,
  `PRAGMA quick_check: ok`. CI run `38610543418` green (test 3.12: **1283 passed
  / 21 skipped**, lint + frontend-build green, audit skipped as before); CodeQL
  run `38610542589` green.
- **Not changed (deliberately):** no new scheduler/router/service/framework, no
  second event transport, no background "is it ready yet" thread, no readiness
  auto-repair, no invented publication dates or progress percentages, no Phase 6
  promotion and no scoring-weight change.

### Phase 10 - Authoritative NSE Calendar & Automatic EOD Recovery
- **Root cause.** `daily_ingestor` wrote `"Likely holiday (zero rows)"` with
  `is_trading_day=0` the first time a date ingested zero rows, conflating "NSE
  did not trade" with "I do not have the data yet". Self-reinforcing, because a
  date marked closed stops being fetched, so no rows ever contradict it. Live
  damage: 215 heuristic rows, 24 of which actually had `technical_data`; 78
  weekdays were wrongly suppressed, including `2026-10-13`/`2026-10-14`, exactly
  the sessions the watchdog existed to catch up on.
- **WS1 (calendar).** New `myra_app/nse_calendar.py` classifies a date as
  `trading`/`closed`/`special`/`unknown` with provenance, from the official
  publication already vendored in `eod2/src/eod2_data/meta.json` (NSE
  `holidays(type=HOLIDAY_TRADING)`, `CM` segment, plus circular-announced special
  sessions). `technical_data` is never consulted. Precedence: special session >
  official holiday > coverage check > weekend > trading day. The coverage check
  precedes the weekend rule **deliberately** - NSE publishes weekend live
  sessions (2026-02-01 is a Sunday), so an unpublished year yields `unknown`
  rather than a confident "closed", which would reintroduce the original bug for
  every future date. Heuristic rows are never rewritten as holidays;
  `_mark_calendar_unproven()` records a confirmed session as trading, and the
  legacy `holiday_skip` `INSERT OR REPLACE` (which could overwrite a curated
  record) is gone.
- **WS1 (repair).** New `tools/calendar_reconcile.py`, dry-run by default.
  Only rows named exactly `"Likely holiday (zero rows)"` are eligible, the
  `UPDATE` re-checks `holiday_name` in its `WHERE`, nothing is deleted, and the
  run is idempotent. Applied: 4,383 rows before and after (zero deletes), trading
  days 2,891 -> 2,969, heuristics 215 -> 127; 78 restored to trading, 10 confirmed
  as genuine official holidays. **127 pre-2026 records left untouched** because no
  official publication for those years exists in the repo - a guessed verdict is
  worse than a visibly unverified one. Re-running reports `changed: 0`.
- **WS2 (recovery).** New `myra_app/eod_recovery.py` holds the policy with **no
  scheduler, thread or polling of its own**; the existing 60 s watchdog stays the
  only driver. Eligibility requires a confirmed session, a genuinely stale DB,
  the publication window, the shared cooldown and available retry budget. Two
  bounds, answering different questions: the cooldown spaces attempts apart
  (shared with the ingest task so two drivers cannot both fire), while a
  per-input-state budget (`MAX_ATTEMPTS_PER_INPUT=6`, keyed by a fingerprint of
  session + source state) is a hard cap on one unchanged input. A cooldown alone
  would re-import an endlessly incomplete source every 30 minutes forever; the
  budget stops that, and because it is fingerprint-keyed a late-arriving file
  produces a new fingerprint and therefore a fresh budget - a prior failure can
  never permanently wedge recovery. Persisted in the existing `metadata` sidecar
  (cross-process, restart-safe, no schema change); a corrupt row degrades to zero
  attempts rather than raising.
- **Honest outcomes.** `tasks.ingest.run()` now returns whether it actually
  executed. When another driver holds the ingest lock the watchdog reports
  `skipped_already_running` and **refunds** the budget it optimistically spent,
  instead of charging for work it did not do. Success is claimed only when the
  stored date actually advances; `stale_catchup` is marked only on real progress.
- **WS3 (Mission Control).** `readiness.recovery` (`reason`/`detail`/`at`/
  `fingerprint`) is served by the **existing** `/api/pipeline/readiness` and the
  **existing** SSE bus - no new endpoint, no new polling loop, cadence unchanged
  (30 s idle / 8 s active). It is deliberately not folded into the overall state:
  "waiting for EOD" and "recovery failed" are different facts and merging them
  makes a correctly waiting system look broken. Reason codes render as plain
  language and most are not errors. A recovery change is part of the readiness
  fingerprint so the transition actually reaches the UI.
- **Tests / verification.** `+tests/test_nse_calendar.py` (22) and
  `+tests/test_eod_recovery.py` (34); every calendar/recovery test pins the
  official source in-process, so none reads the real `meta.json` and none drifts
  when eod2 refreshes it. Focused run **271 passed**; full suite **1360 passed /
  0 failed** (was 1304, +56). `black` 26.10.1 + `flake8` clean; `tsc --noEmit` +
  `npm run build` clean; performance guard **271 / 0 CRITICAL** (+3 are
  false positives - `summary["unclassified"].append()` builds a plain dict list,
  not a DataFrame, so `pd.concat` does not apply). `db_doctor`: 2 pre-existing
  issues (225 zero-close rows; technical DB 3 days behind), neither introduced
  here - the second is precisely what Phase 10 recovery now handles.
- **Process notes.** The live-DB guard caught three real leaks during this phase,
  each fixed rather than suppressed: a stale `eod2.DB_DIR` assertion in
  `test_eod2_sync.py` (module-level binding replaced by call-time resolution);
  and two missing Phase 10 stubs (`note_attempt`, `refund_attempt`,
  `budget_allows`) in the watchdog suites that wrote budget rows to the live
  metadata sidecar. Leak rows were removed; live metadata is back to its 17
  production rows. Two real bugs were also caught by the new tests and fixed:
  the weekend rule firing for unverified years, and special-session descriptions
  being discarded.
- **Not changed (deliberately):** `eod2/` untouched (read-only reference); no
  schema migration; no row deleted; no heuristic may close a session the official
  calendar confirms is open; nothing guessed - unverifiable dates report
  `unknown`; no new scheduler/thread/endpoint/event transport; no scoring,
  ranking or manual-CSV change.
- **Post-repair outage: published sessions would not ingest (top priority).**
  After the calendar repair the system still sat at `2026-10-12` while the source
  held `2026-10-14` (`ready`, 3552/3770 files). `eod_recovery.evaluate()`
  reported **`eligible=True`** - the policy was right - and the data was blocked
  by **three separate wall-clock guards**, each asking "has *today* bhavcopy
  landed?" and each applied to sessions the source had **already published**:
  `eod_recovery.evaluate`'s `after_publication_window` (deferred to 18:30 IST),
  `tasks/ingest`'s `if ist_now.hour < 18` ("Market data not yet available
  (IST: 09:21). Skipping."), and `if ist_now.weekday() >= 5` (which would
  suppress Friday's catch-up on a Saturday morning). None is wrong about
  today's session; each was being asked about the wrong thing. Fixed with one
  shared rule, `tasks.ingest._has_publishable_backlog()`: if the newest upstream
  session is newer than what is stored, there is real already-published work and
  the guards must not apply. With no backlog, all three behave exactly as
  before. A fourth, non-code cause: no MYRA process was running, so no watchdog
  was ticking.
  **Verified by driving the real watchdog tick** (`_maybe_trigger_ingest`):
  `rows=398611, symbols=3627` -> "Catch-up ingestion advanced DB to 2026-10-14",
  outcome `success` (829.6s); readiness flipped to **`ready`** ("Latest market
  date 2026-10-14 matches the expected session"); recovery recorded
  `recovered - advanced 2026-10-12 -> 2026-10-14`.
- **Ctrl+C / shutdown safety.** The first signal already took the full graceful
  path (join every task so none is killed mid-write, close pooled connections,
  checkpoint WAL, release the single-scheduler lock). The gap closed here: a
  **second Ctrl+C was swallowed** by the `if _shutdown_event.is_set(): return`
  guard, so with a wedged task the operator was pressing Ctrl+C against no
  response at all. A repeat signal now counts down and then exits - safe because
  every database is in WAL mode, so an interrupted write rolls back to the last
  commit and forcing costs a WAL checkpoint, not data. Three details that matter:
  the countdown is **cancellable** (a graceful finish disarms it, so a clean stop
  is never killed by a stale timer); it is **single-shot** (a third Ctrl+C does not
  stack a second countdown); and `run_pipeline.py` now only calls `handle_exit()`
  from its `finally` when **no signal already did**, which otherwise made an
  ordinary clean shutdown look like a second Ctrl+C and arm the countdown against
  itself. A task that will not stop within `TASK_JOIN_SECONDS` is now reported
  rather than silently waited on.
- **A test bug that killed the whole suite.** `test_force_exit_runs_on_a_separate_
  thread` armed a real 5-second countdown that outlived the test; by the time it
  fired, monkeypatch had been undone, so it called the real `os._exit` and killed
  pytest with exit 130. Fixed at the source (cancellable countdown + cancel in
  fixture teardown) rather than by making the test slower - the underlying
  "unarmed timer outlives its test" hazard is real in production too, where a
  clean stop could be killed by a stale forced exit.
- **Tests / verification.** `+tests/test_shutdown_safety.py` (16) and 9 new
  ingest-gate tests in `tests/test_ingest_freshness.py` (now 22).
  `+tests/test_nse_calendar.py` (22), `+tests/test_eod_recovery.py` (35), and 6
  new precedence tests in `test_market_calendar_guard.py` (now 26). Full suite
  **1392 passed / 0 failed**. `black` 26.10.1 + `flake8` clean; `tsc --noEmit`
  clean; performance guard **271 / 0 CRITICAL**. Live state after the fix:
  `technical_data` max date `2026-10-14`, readiness **`ready`**.
- **Known limitation (stated, not hidden).** Automatic ingestion still requires
  the MYRA process to be running - recovery is driven by the 60 s watchdog inside
  it, and no second scheduler was added by design. If MYRA is down, a published
  session waits for the next start.
### Phase 11 - Readiness Transport Reliability & Fundamental Data Integrity
- **State verified, not assumed.** Branch `main`, HEAD and `origin/main` both
  `88f3966`, 0 ahead / 0 behind, clean tree, 3 pre-existing stashes untouched.
  All 7 Phase 10 files present. No discrepancy, no user changes overwritten.
- **P1 - readiness `Failed to fetch`: root cause, reproduced in a real browser.**
  The string is emitted by the *browser*, not the server, for connection refusal,
  DNS, TLS **and** CORS alike. Backend verified healthy (200) throughout. Matrix
  against the real Vite dev server: page origin `localhost:3000` -> absolute URL
  200; `127.0.0.1:3000` -> "Failed to fetch"; LAN `192.168.29.162:3000` ->
  "Failed to fetch". Browser console: *"blocked by CORS policy: No
  `Access-Control-Allow-Origin` header is present"*.
  Two independent causes: `myra_web/src/config.ts` hardcoded an absolute
  cross-origin base `http://localhost:8000/api` (making every panel call a
  cross-origin call into loopback and bypassing the already-configured Vite
  `/api` proxy), and the backend allow-list named only `localhost:3000` /
  `localhost:5173` while Vite runs `--host=0.0.0.0` and therefore advertises
  `127.0.0.1` and LAN origins. An early Playwright route shim produced a
  misleading "loopback address space" denial; the shim was discarded and the
  matrix re-run against the real dev server, which is what made the result
  trustworthy.
- **P1 - fix.** `config.ts` now defaults to the **relative `/api`** so requests
  stay same-origin and the existing proxy forwards them; `VITE_API_BASE` remains
  as the split-deployment override. CORS policy extracted to
  `myra_web/cors_config.py` (own module so it is testable without constructing
  the app, whose routers touch the DB) and widened to `127.0.0.1` / `0.0.0.0`,
  overridable via `MYRA_ALLOWED_ORIGINS`. Failures now classify as
  `unreachable` / `http` / `malformed` with actionable text (e.g. "API not
  responding (HTTP 502 via proxy) - start it with: python run_fastapi.py") rather
  than echoing `Failed to fetch`; a 2xx that breaks the contract is rejected as
  malformed rather than rendered; a displayed verdict during a transport failure
  is disclaimed as "not currently confirmed"; and `setLastUpdated` no longer
  fires on unchanged polls, which had re-rendered the panel every 30s for a value
  that never changed.
  **A bug this introduced and caught:** the first fix used an *empty* base, so
  `${API_BASE}/pipeline/readiness` became `/pipeline/readiness`, missing the
  router's `/api` mount and returning the SPA `index.html` where JSON was
  expected. The new payload validator caught it immediately as `malformed` rather
  than rendering a false verdict. Base is therefore `/api`, not `''`.
  **Verified in the real browser:** both `localhost` and LAN origins render the
  panel with 0 `Failed to fetch` and 0 CORS errors; backend-stopped shows the
  actionable 502 message; restarting recovers to `READY` with no page reload.
  19 regression tests in `+tests/test_readiness_transport.py`, plus
  `tools/verify_readiness_transport.py` as an operator browser smoke check.
- **P2 - fundamental provenance and freshness (read-only).** New
  `tools/fundamentals_audit.py` (every connection `mode=ro`, bounded, CLI only)
  reporting coverage against explicit, non-conflated denominators: 3,948
  `fundamentals` rows vs a 3,424-symbol tracked universe. There is **no**
  `myra_fundamentals.db` - `fundamentals` lives in `myra_valuation.db`.
  Previously reported figures **confirmed**: `roe` 17.50% (691), `market_cap`
  82.22% (3,246), `profit_growth` 0%. **New finding:** `sales_growth` and
  `debt_to_equity` are *also* 0% - the Morningstar camelCase path is effectively
  dead (`revenueGrowth`/`earningsGrowth`/`debtToEquity`/`netMargin`/`dividendYield`
  all 0; `marketCap` 8 rows, `operatingMargin` 7). The percentage-vs-fraction
  defect is quantified: `net_margin` samples 0.1366 (fraction-like) but ranges to
  699.9 and -26,570 (percent-like), so values cannot be compared across rows.
  `inst_holding` (18.44%) is month-end shareholding - a **stock, not a flow**, and
  cannot represent who bought during a quarter.
- **P2 - freshness implemented.** New `myra_app/fundamental_freshness.py`,
  surfaced through the **existing** readiness endpoint as
  `fundamentals.freshness` (no new endpoint). States fresh/aging/stale/unknown;
  cadence per source *kind* (market 7d, ownership 45d, financial 120d) rather
  than a universal TTL, labelled as policy not source fact. Enforced: missing
  timestamps -> `unknown` never `fresh`; **a recent retrieval of an old reporting
  period is still old**; future periods -> `unknown`; valid zero preserved; no
  `COALESCE`/`or 0`; module imports no DB, threading, scheduling or HTTP code.
  Live state: period 2026-09-30, retrieval 2026-10-13 -> `fresh`.
- **P2 - untrustworthy fields labelled, not invented.** `profit_growth`,
  `sales_growth`, `debt_to_equity`, `roe_ttm` and `smart_money_score` are marked
  **unavailable** with the reason. `smart_money_score` is **not a `fundamentals`
  column at all** - it is read from a candidate dict in `universe_loader` /
  `worker_pool` with a `0` default and never written anywhere, confirming and
  extending the Phase 6 finding. No producer was fabricated. Phase 6's opt-in
  (`MYRA_V25_RANKING_INPUTS`) is untouched and the freshness module deliberately
  does not reference it (asserted by test).
- **P2 - found but deliberately NOT changed (documented for a later phase).**
  `myra_web/routes/fund_traction.py:254-255` scores with
  `COALESCE(f.roe, 0) * 0.4 + COALESCE(f.net_margin, 0) * 0.3`;
  `confluence_batch.py:161` and `routes/scanners.py:1836` use
  `COALESCE(f.market_cap, 0)`; `universe_loader.py:215-216` does
  `f.get("profit_growth", 0) or 0` on a field empty for every row. Each is a
  null-as-zero defect in a scoring path and changing them would alter investment
  decisions, which this phase is explicitly not authorised to do.
- **Tests / verification.** `+tests/test_readiness_transport.py` (19) and
  `+tests/test_fundamental_freshness.py` (28). Full suite **1439 passed / 0
  failed**, no live-database writes. `black` + `flake8` clean, `tsc --noEmit`
  clean, `npm run build` success, performance guard **273 / 0 CRITICAL** (+2 are
  the same `.append()`-in-loop false positive - a plain dict list, not a
  DataFrame). One of my own tests wrote rows into the session-shared fixture DB
  and broke an unrelated module's row-count assertions later in the run; fixed by
  restoring the fixture in-test and verified by running both modules together.
- **Operational note.** MYRA's live scheduler writes to the metadata sidecar, so
  the test suite's live-DB guard trips whenever the app is running. The backend
  was stopped for testing and restarted once afterwards; the Vite dev server was
  left running throughout (it touches no databases).
- **P1 follow-up - residual instances of the same bug, closed.** After the
  readiness path was fixed the browser console still showed
  `http://localhost:8000/api/health` blocked from the LAN origin. Three more
  files hardcoded the identical absolute base - `lib/Librarian.ts` (`apiUrl`
  default), `lib/marketCapCache.ts` (`getApiUrl()` fallback) and
  `lib/SettingsContext.tsx` (default `apiEndpoint`). All three now default to
  `API_BASE`; the LAN-origin console is clean with **0 CORS errors and 0
  `Failed to fetch`**, and `tools/verify_readiness_transport.py` reports all three
  origins `HTTP 200`. A `localStorage`-persisted `apiEndpoint` still overrides,
  intentionally. Found only by reading the browser console after the first fix,
  not by inspection.
### Phase 12 - Missing-Data Safety in Investment Decisions
- **State verified, not assumed.** Branch `main`, HEAD and `origin/main` both
  `7f2ac8d`, 0 ahead / 0 behind, clean tree, 3 stashes untouched, both Phase 11
  commits present. Port 8000 was LISTENING, port 3000 down (not needed). No
  discrepancy, no user changes overwritten.
- **All four flagged paths traced before editing. TWO were genuine defects, two
  were already correct and were deliberately NOT changed.**
  - `scanners.py` and `confluence_batch.py` use `COALESCE(market_cap, 0)` only
    inside a *positive-threshold* filter. NULL and 0 both fail `> 0` and both
    fail `BETWEEN min AND max`, so the rows are excluded either way: redundant,
    not wrong. Removing it would be a cosmetic edit to a screening path. A test
    now pins these files unchanged so a later reader does not "fix" them.
  - `screener.py`'s `if r.get("ProfitGrowth") is None: r["ProfitGrowth"] = 0`
    sits under a comment stating it is a *display-compatibility* default that
    fills gaps only and never clobbers a real value. Not a ranking input;
    changing it would alter rendering without improving any evidence.
- **Defect 1 - fund-traction quality score (3 bugs in one expression), which
  feeds a sortable column in FundTractionScanner.tsx.**
  (a) *Absent inputs silently scored zero*: weights were never renormalised over
  inputs that existed. Of 3,246 rows with a market cap, **2,769 have no ROE**
  (85%), 504 no P/E, 339 no net_margin - being unmeasured was a de facto penalty.
  (b) *Negative P/E inverted the metric*: `MIN(pe,100)` capped the cheap end but
  had no floor, so pe=-341.7 scored **+132.51** - the largest score in the data
  - for the worst possible quality signal. 14 rows have pe<0 with a market cap.
  (c) *Mixed-scale net_margin swamped everything*: values run from ~0.13
  (fraction) to -26,570; NECLIFE contributed **-5,070** and RAINBOWPAP -7,971,
  overwhelming every other term combined.
- **Defect 2 - universe_loader fabricated evidence for the entire universe.**
  `PE`/`ROE`/`MCap` correctly preserved `None`, but `ProfitGrowth`, `SalesGrowth`
  and `Debt_Equity` were written as `f.get(col, 0) or 0`. Phase 11 measured all
  three at **0 of 3,948 rows populated**, so the default asserted "0% growth" and
  "zero debt" for every symbol. All three now preserve `None`; a valid stored
  `0.0` still stays `0.0`.
- **Defect 3 - smart_money_score.** No producer exists (re-verified: no writer,
  only readers). `worker_pool.py` did `funda.get("smart_money_score", 0) > 0.7`
  to award a star - behaviourally the star was **never** awarded, so no ranking
  inversion, but the code fabricated a numeric 0 to compare against (and
  `None > 0.7` would raise TypeError). Now compared only when a value exists; the
  field stays explicitly `None`. No proxy signal derived.
- **Scale preservation - a fix I had to discard.** My first correction used a
  plain weighted *average* over present components. The before/after demo showed
  that moved a **complete** row from 34.05 to 48.57, which would have flipped the
  UI's 0-40 / 40-70 / 70+ bands - a policy change disguised as a bug fix. It was
  replaced with a `total_weight / present_weight` renormalising factor, so a row
  with every component scores exactly what it scored before (34.05 -> 34.00,
  REAL_ZERO_ROE 24.00 -> 24.00) while an absent input is redistributed across the
  inputs that exist instead of subtracting points. Weights remain 0.4 / 0.3 and
  the 4:3 emphasis is unchanged.
- **net_margin excluded, not normalised.** Its scale is unresolved, so it no
  longer enters the quality score at all. Guessing a per-row unit is precisely
  what this phase must not do, but ranking on an incomparable column is worse.
  Repair requires a source-level decision and is **deferred to a separately
  scoped phase**.
- **Measured impact (read-only, `tools/phase12_real_impact.py`).** Of 3,246 rows
  with a market cap: **2,942 (90.6%) changed**, **481 now genuinely unavailable
  (NULL)** instead of showing a fabricated number, and top-10 overlap fell to
  **4/10**. Four names left the top 10; NECLIFE moved from -5,070 to unavailable.
  This is a statement that an unavailable input no longer outranks a measured
  one - **not** a claim the new ordering is financially better.
- **Tests / verification.** `+tests/test_missing_data_safety.py` (24): NULL,
  valid zero, negative pe, pe=0, very high pe, negative ROE, corrupt net_margin,
  all-missing, scale preservation, weight preservation, Phase 6 opt-in,
  smart_money_score unavailability, and pins that the three "already correct"
  files stay untouched. Full suite **1463 passed / 0 failed**, no live-database
  writes; `black` + `flake8` clean, `tsc --noEmit` clean, `npm run build` success,
  performance guard **274 / 0 CRITICAL**. Two of my own test expectations were
  wrong and were corrected against the implementation rather than by loosening
  the tests. All inspection was read-only; the only writes were to `tmp_path`
  synthetic fixtures.
- **Operational note.** The MYRA scheduler writes to the metadata sidecar, so the
  test suite's live-DB guard trips whenever the backend is running. It was
  stopped for test runs and restarted once afterwards.

## Phase 13 — Fundamental recovery, margin semantics, scheduler health

- **The question that started it.** Phase 12 ended with the API answering
  `/api/health` 200 and every database "connected", while the scheduler and the
  EOD recovery watchdog were **not running at all**. The watchdog had been
  frozen at `22:39:28` for 20 minutes, and nothing on any status surface said
  so. "Serving" had been silently standing in for "scheduling".
- **Root cause.** A hard kill left the lock file with a dead PID, and
  `_pid_alive` failed **closed** (any exception resolved to "probably alive").
  The restarted API therefore concluded a live scheduler existed and started
  nothing. The refusal was a single line at info level.
- **Fix, and the principle behind it.** Reclamation now requires the previous
  owner to be *provably* gone. "Cannot tell" is not "gone" - misreading a
  healthy lock as dead is exactly how one gets destroyed. The read path returns
  `alive` / `dead` / `unknown` / `absent` instead of a binary; `unknown`
  refuses and `dead` reclaims. Reclamation is a compare-and-swap (`O_EXCL` for
  a free lock, otherwise write-then-`os.replace` then re-read) so two processes
  racing on the same staleness still produce exactly one winner. A per-record
  process creation token catches PID reuse.
- **Release tightened.** It now deletes the lock only when the record names
  *this* process. The previous `owner in (None, os.getpid())` test would delete
  a lock this process never held - that is how a healthy lock gets destroyed.
- **Observable, not inferable.** `/api/health` gains a `scheduler` block
  (`running` / `starting` / `degraded` / `disabled` / `unknown`) driven by the
  watchdog's own 60s heartbeat, so a frozen timestamp means a frozen watchdog.
  `degraded` and `unknown` are deliberately **not** healthy, and the on-screen
  banner is bound to that state - the failure is visible to the operator, not
  only written to a log. A failed lock probe now warns instead of hiding at
  `debug`, and the orchestrator records *why* it declined (`disabled` /
  `lock_held` / `lock_probe_failed`).
- **`DISABLE_SCHEDULER`** was added so "the scheduler is off" is a choice an
  operator made, rather than something inferred from a missing lock file.
  Defaults to unset; behaviour unchanged unless set.
- **net_margin: the recorded defect was wrong.** Provenance said the units were
  `MIXED` - a fraction in some rows and a percent up to 699 in others. They are
  not mixed; they are a pass-through **fraction**. Of 3,309 non-null rows, 95.1%
  are `|v| <= 1` (median 0.0644), every value above 1 is a fraction over a
  near-zero-revenue denominator, the extremes match the live source
  byte-for-byte, and one sample (`ESSARSHPNG 699.92`) is identical across three
  archived snapshots while its `pe` drifts. A live probe returns fractions
  directly (`20MICRONS 0.07137`, `360ONE 0.37408`). The entry now records one
  unit plus the consumer warning. **No stored data was changed** - only the
  recorded interpretation, and the test that asserted `MIXED`.
- **roe_ttm can never populate - two independent causes.** (1) `roeTTM` is
  absent from the requested Morningstar `securityDataPoints`, and the API
  honours that list: probed live, the current field list returns **no roeTTM
  key at all**, while adding it returns values. (2) The merge reads `["roeTTM"]`
  although the fetch stored `"roe_ttm"`. Both recorded; left NULL on purpose
  (no consumer, and the source value is a fraction while sibling `roe` is a
  percent).
- **Deferred, on purpose.** `profit_growth` / `sales_growth` *do* have a source
  (`upstox_income_statement` YoY strings such as `"+9.83%"`, verified against
  `INFY 182972 -> 166590`), but populating them feeds the ranking pipeline -
  that turns on a signal, so it needs an explicit decision rather than a quiet
  backfill. `debt_to_equity` is not derivable (the balance sheet carries only
  `total_asset` / `total_liability`), and `smart_money_score` still has no
  definition.
- **Verification.** Full suite green including the pre-existing lock tests
  unmodified (they monkeypatch `_pid_alive` and write the legacy two-line
  format, so backward compatibility is asserted by their continuing to pass);
  `black` + `flake8` clean; frontend `npm run build` succeeds. No test touches
  the real lock file or the live databases - the lock tests use isolated
  temp directories and injected process state.

## Phase 14 — Annual growth signal validated; shadow ranking is preliminary

- **The question.** Phase 13 deferred `profit_growth` / `sales_growth` because
  populating them "turns on a signal" and needs an explicit decision. Phase 14
  answers the question that must precede it: what do those numbers mean, how many
  symbols can be measured honestly, and what would populating them do to the
  ranking? Verdict: **the definition is validated enough to encode and test; the
  ranking evidence is not enough to authorize live activation.**
- **Definition, verified against the source not the schema comment.** Annual
  year-on-year growth is `(current - prior) / abs(prior) * 100`, in percent.
  `abs(prior)` is the provider's convention (a recovery from a loss reads
  positive). Recomputed every annual pair and compared with the provider's own
  `net_profit_change` string: **2,166 of 2,166 eligible symbols agree** within
  0.02pp - the strongest available independent check on both formula and sign.
- **Quarterly rows cannot make a YoY number.** Within one series, quarterly lag-1
  is QoQ (6,378/6,399 agreement) and lag-4 - the same-quarter-prior-year pair YoY
  needs - yields **0 pairs**: the source does not store them. Mixing the two would
  silently compare different statistics, so only `time_period='yearly'` is
  admitted and quarterly rows can never supply a missing prior period.
- **Fiscal year ends are company-specific** (Mar 26 · Dec 23 · Jun 23 · Sep 22 ·
  Nov 5 across 99 clean labels). Each fiscal-end month is evaluated as its own
  cohort and the newest cohort wins, so a Dec-year-end company is not mis-paired
  against a March calendar.
- **Coverage, corrected from the estimate.** `upstox_income_statement` holds
  17,771 rows / 2,456 symbols, all `consolidated` and all in `crore`. **2,166 of
  2,456 symbols are eligible** (the pre-implementation estimate of 2,170/2,454 is
  wrong and superseded). Exclusions: 162 stale, 63 no prior period, 55 prior base
  below the 1-crore floor, 8 non-consecutive annual filings (the extra rule the
  implementation added), 2 with no annual rows at all. `conflicting_records`,
  `missing_values`, `zero_prior_base` and `missing_freshness_metadata` are all 0.
- **Growth is currently absent from the live ranking, proved read-only.** In
  `fundamentals` (3,948 rows): `profit_growth` **0**, `sales_growth` **0**,
  `debt_to_equity` **0**, `roe_ttm` **0**, against `roe` 691, `net_margin` 3,309,
  `market_cap` 3,246. So the scorer's growth input has only ever contributed its
  neutral 0.4. `SalesGrowth` is not even a scorer input - `precompute_ranks` reads
  `roe`, `ProfitGrowth`, `avg_delivery_20d`, `avg_volume_20d`,
  `smart_money_score` - so revenue growth is derived for completeness and can
  move nothing.
- **Shadow result (preliminary, stale snapshot).** `scan_rs_rating.csv`, 174 days
  old, 938 rows of which only **222** have a populated technical block. Baseline
  (growth absent) vs candidate (validated YoY): coverage **0/222 -> 196/222**,
  Spearman **0.9489**, top-20 overlap **6/20**, **216 moved, max displacement
  56**. Fourteen names entered and fourteen left the top 20. This says the input
  reorders the ranking materially; it does **not** say the new order is better,
  and no return or risk statistic was computed.
- **An earlier 938-row result was discarded, not reported.** It showed Spearman
  0.667 with swings of up to ±724, which was an artefact: the snapshot's
  `ProfitGrowth` is 938 filled / **1 distinct value, all `0.0`** (the pre-Phase-12
  `f.get(col, 0) or 0`), and a **constant** column still ranks at the *maximum*
  percentile - so the baseline handed every symbol the best growth rank. Only 225
  of 938 rows have any real technical block, which is why the file's own
  `MYRA_Score_v25` has just 16 distinct values with a 725-row tie block. The
  tool's default mode on the same file reproduces the degeneracy (Spearman 0.6628,
  max displacement 753), independently confirming the file cannot support a
  fine-grained ranking claim.
- **Baseline honesty.** A saved snapshot carries Phase 6 opt-in inputs and
  pre-Phase-12 placeholders; a live default scan supplies none of them. The
  growth mode therefore strips `roe`, `ProfitGrowth`, `SalesGrowth`,
  `avg_volume_20d`, `avg_delivery_20d`, `smart_money_score` and the precomputed
  rank columns before scoring, so the "baseline" is the production ordering
  rather than whatever the file happens to contain.
- **Limitations, stated rather than buried.** The only snapshots on disk are from
  April 2026 and are not live-representative; the measured effect is an **upper
  bound**, because with the faithful baseline having all Phase 6 inputs absent,
  growth is the only populated input in the candidate; `atr_pct` is a constant
  `0` in the file so `base_score` adds a constant +0.5 and the printed absolute
  scores are not what a live run would show; 26 of 222 frame symbols still fall
  back to neutral. The scorer was deliberately **not** refactored to accommodate
  the comparison - the candidate stays isolated.
- **Deferred, separately scoped finding.** `universe_loader` writes `atr_pct` as
  `0` when absent, and `base_score` reads `atr_pct < 0.03` as a very tight
  (favourable) base and adds +0.5 - a missing ATR **rewards** the row. Same class
  of defect as the `ProfitGrowth = 0` sentinel, on a different field. Recorded
  and explicitly **not** changed in this phase.
- **Deliverables.** `+myra_app/growth_signals.py` (definition, eligibility
  contract, pure derivation, read-only adapter);
  `~tools/compare_v25_orderings.py` gains an opt-in `--growth-source
  upstox-annual` mode with default behaviour unchanged and no implicit fetch;
  `+tests/test_growth_signals.py` (44); `+docs/PHASE14_...md`.
- **Verification.** Focused 66 passed; full suite **1535 passed** (+44 over
  Phase 13's 1491), no live-database writes; `black` + `flake8` clean. No
  frontend file changed, so the TypeScript check and frontend build were not
  applicable. Every database connection in the new code and the growth mode opens
  `mode=ro`, and a test asserts the growth run modifies **zero** rows via
  `Connection.total_changes`.

## Phase 15 — No fresh snapshot can be produced; the indicator lake is empty

- **The question.** Phase 14A left growth validated but unauthorised, on the
  strength of a stale April snapshot that only 222 of 938 rows could even be
  scored on. Phase 15 asks the question that has to come first: can a *fresh,
  technically representative* ranking snapshot be produced at all, and if so,
  what do A (production), B (Phase 6 inputs) and C (B + Phase 14A growth) do to
  one common universe? Verdict: **`INSUFFICIENT DATA` — no snapshot can be
  produced, and the reason is a release-blocking bug.**
- **Ingestion is genuinely fresh, and that is not the problem.** Readiness is
  `ready`; the latest market date `2026-10-15` equals the expected trading day;
  3,194 symbols are present at that date; the EOD source folder holds 3,545 of
  3,770 files with 0 unstable; the last ingest succeeded at
  `2026-10-15T21:58:51`; fundamentals are reporting-period `2026-09-30` and
  `fresh`. The daily pipeline is doing its job.
- **The blocker: the derived indicator lake is empty.** `myra_app/data/indicators/
  precomputed/` holds **0 files**. `precompute_indicators` reads it, finds
  nothing, and hits `assert "symbol" in df_final.columns`
  (`librarian_intelligence.py:76`) — reproduced by execution, not inferred:
  calling it with a real `StockDataLoader` raises `AssertionError`. `engine.py`
  catches that at `:295-298`, prints one `[!] Turbo Load failed:` line and
  returns `([], {})`. **Every production scan currently returns zero candidates,
  and the only symptom is a console line.**
- **"Ready" does not mean "derivable".** The readiness oracle reports `ready`
  while no ranking snapshot can exist. It measures ingestion freshness, not
  whether a derived artifact is present — the same shape of gap as Phase 13's
  "scheduled ≠ serving". This one is "ready ≠ derivable".
- **The raw caches are ~168 days stale.** `data/ohlcv_cache` and `data/lake`
  hold 3,320 files each, written 2026-05-02, carrying OHLCV only to
  **2026-04-30** against a 2026-10-15 market date. `data/lake` is an orphan — no
  `.py` in the tree references it. `librarian.get_ohlcv` (`:307`) refreshes
  stale parquet in place (~`:397`), so even *reading* through it is a write.
- **Rebuilding inside this phase was not permitted.** The only writer,
  `update_indicator_history` (`librarian_intelligence.py:89`), needs
  `LibrarianCore(read_only=False)`, whose constructor runs `_create_tables()`,
  `_migrate_meta_schema()` and `SchemaRegistry.validate_schema()` — production
  schema writes, explicitly forbidden — and would then rewrite ~3,320 OHLCV and
  ~3,320 indicator files off a stale cache.
- **The writer does not produce what the scorer reads.** It writes `sma20`,
  `sma50`, `sma200`, `rsi`, `atr20`, `vol_sma50`, `avg_deliv_20d`, `rdv`,
  `delivery_pct`, `vcp`, `delivery_accumulation_signal`, `ias`, `fvg`, `bos`,
  `choch` — and never `atr_pct`. `schema/technical.sql` confirms `technical_data`
  has no `sma150`, `atr_pct`, `pct_above_ma50_60d`, `keltner_*`, `atr5` or
  `drawdown`. The scorer's technical block — **70% of the score** — therefore has
  no populated source anywhere. Any frame built now would measure fabricated
  fallbacks, which is precisely the result this phase was told not to
  manufacture.
- **Coverage baseline, measured read-only.** Four "universe" numbers disagree:
  `symbols_master` **3,610**, `technical_data` distinct **3,425**, at the latest
  date **3,194**, readiness `coverage.universe` **3,221**. Active ∩ technical =
  **2,419**; active ∩ latest-date = 2,353; 1,006 technical symbols are not active
  (mostly `*_SME`); 1,191 active symbols have no technical data at all (ETFs,
  rights). Over the 20 sessions 2026-09-17→2026-10-15: `avg_volume_20d` **2,332
  (64.6%)**, `avg_delivery_20d` 2,332, full 20-session window 2,332. Fundamentals
  are thinner still: `roe` **234** of the active universe, `roe_ttm` 0,
  `profit_growth` 0, `sales_growth` 0, `debt_to_equity` 0. Growth eligibility is
  the healthiest input at **1,788/3,610 (49.5%)**. `atr_pct` with a readable
  value: **0**.
- **A/B/C was not run, and reusing April was rejected.** The tools are already
  correct and unchanged (`shadow_ranking.compare_orderings` drives the real
  `PositionalScorer`; `--growth-source` still defaults to `none`), so no new
  comparison behaviour was added. Reporting April's numbers again would present
  stale data as fresh; they remain Phase 14A's upper bound (candidate Spearman
  0.9489, top-20 6/20, 216 moved, max displacement 56), not activation-grade.
- **Missing-ATR defect, audited and deliberately not fixed.** `universe_loader`
  emits `"atr_pct": c.get("atr_pct", 0) if c.get("atr_pct") is not None else 0`
  (`universe_loader.py:309`), so "no measurement" becomes a stored `0.0`.
  `base_score` (`score_components_v2.py:104-125`) reads `atr < 0.03` as
  favourable and adds `+0.5`, so **missing data earns the maximum base score**.
  Measured on the real scorer: a fabricated `0.0` and a genuinely tight `0.02`
  both give `base_score = 0.5`; an absent key, `None`, `0.04` and a genuinely
  wide `0.08` all give `0.3`. Two defects in one line — missing is scored as
  best, and wide is indistinguishable from missing. Magnitude: `+1.4` points on
  `MYRA_Score_v25` at `NORMAL`, roughly `+1.1`–`+1.6` once regime and drawdown
  multipliers apply. Relative order is **not** distorted (the uplift is
  uniform), but absolute scores and display bands are: a verified row scores
  `68.8` honestly and `70.2` with the fabricated zero, crossing into `70+`
  "Elite" purely because a value is missing. `_missing_inputs()` also reports
  `[]` for the fabricated zero, so the breakdown presents it as observed.
  Affects every snapshot, including April's (constant `0.0` across all 938
  rows).
- **Pre-existing issues reported, not buried.** `engine.py:267` builds a
  meta-DB path as `os.path.join(os.getcwd(), "db", ...)`, bypassing the
  documented `DB_DIR` / `DB_MAP` invariant (medium; wrong DB if cwd differs, the
  same cwd assumption section 4.2 already shows is fragile). `roe_ttm` and
  `smart_money_score` still have no producer and stay `None`. Dependabot's
  `base_factor` `dependency_file_not_resolvable` failure is pre-existing and
  third-party; no action taken.
- **Deliverables.** `+docs/PHASE15_FRESH_SNAPSHOT_RANKING_VALIDATION.md` (the
  audit, the coverage baseline, the ATR analysis, and the single `INSUFFICIENT
  DATA` decision); `+tests/test_phase15_atr_characterization.py` (10 tests).
  **No production file changed** — `git diff --stat HEAD` is empty.
- **Verification.** New tests 10 passed; full suite **1545 passed** (+10 over
  Phase 14A's 1535); `black` + `flake8` clean. No frontend file changed, so the
  TypeScript check and frontend build were not applicable. Every database
  connection opened `mode=ro`; no schema write, no cache rewrite. Production
  growth stays disabled and `--growth-source` still defaults to `none`.
- **What would unblock it.** (1) populate the indicator lake, or derive the
  scorer's technical fields on demand — release-blocking and independent of
  Phase 15; (2) make the empty-lake failure loud to the user instead of one
  console line; (3) teach readiness about derived artifacts; (4) settle the
  universe definition and the 1,006 / 1,191 join gaps; (5) only then run A/B/C
  and report the full metric set. The ATR fix is on a separate track because it
  cannot change relative order.

## Phase 16 — Restore production screening and make readiness truthful

- **Scope.** Items (1)-(4) of Phase 15's unblock list: the producer, the silent
  failure, readiness, and the universe counts. Item (5), the growth A/B/C run,
  is not attempted. No weight, threshold, formula, band, ordering, or scoring
  input changed; growth stays disabled; the ATR defect stays isolated and
  unfixed; no production DB, cache, or lake was written.
- **The root cause was never the assertion — it is that the producer cannot be
  called.** `sync_market_data` returns at `librarian_sync.py:90-92` under
  `skip_maintenance=True`, *before* `update_indicator_history()` at `:154`, and
  both call sites in the tree pass `skip_maintenance=True`
  (`librarian_sync.py:318`, `screener.py:273`). `if not skip_maintenance:` at
  `:177` is dead for the same reason, so the "background sync" fetches and
  enriches only and has never built the lake.
- **Two proofs it never ran.** (a) commit `aa7016e` removed the indicator
  calculations and `pandas_ta` from the producer, leaving the caller-visible
  `assert "symbol" in df_final.columns` reachable on an empty lake; (b)
  `myra_log` was called in `librarian_intelligence.py` without ever being
  imported — an immediate `NameError` that `.flake8`'s ignored `F821` could not
  flag, because the path never executed. **A clean lint run is not evidence a
  code path runs.**
- **Scope correction: two stacks.** The web/API scanners
  (`myra_web/routes/scanners.py` → `strategies/*_scanner.py` → `fundamentals` +
  `technical_data`) work today and never touch the lake — `TriggerScanner`
  scanned a bounded 200 and returned 2 real candidates in 2.6 s off a
  1,846-symbol universe. Only the CLI/turbo path (`execute_scan` → `run_scan` →
  `load_universe` → `precompute_indicators`) depends on the lake.
- **What was built.** `myra_app/scan_outcome.py` (six closed statuses +
  `ScanOutcome`); `myra_app/indicator_lake.py` (read-only probe: list, sample,
  validate, freshness — call-time `DATA_DIR`, never creates a directory);
  `myra_app/lake_build.py` (explicit CLI: `--status`, `--limit`, `--force`,
  `--no-progress`); `precompute_indicators` returns the empty frame instead of
  raising; `build_indicator_lake` (freshness-gated, bounded, resumable, never
  raises per symbol); atomic `save_indicators` via `os.replace`;
  `Engine.last_scan_outcome` set on every exit path with `_classify_failure`
  mapping exceptions by remedy; `_abort_empty_universe` splitting
  `indicator_lake_empty` from `no_precomputed_rows`; `execute_scan` printing the
  outcome instead of pretending an empty list is a verdict.
- **Readiness: `ready` no longer means "ingested".** New `STATE_BLOCKED`
  ("Screening blocked") plus `screening_facts()` and `_apply_screening()`, which
  escalates `ready`/`waiting`/`stale` and never downgrades
  `failed`/`unknown`/`partial`. The verdict and its reason are in the `assess()`
  payload and the fingerprint, so a lake going from blocked to built is visible;
  the panel styles `blocked` as a failure with an alert icon.
- **Universe reconciliation (reported, membership untouched).** `symbols_master`
  3,610 = 3,610 distinct, 100% `in_active_universe`, all labelled `EQUITY`;
  `technical_data` 3,425 all-time, 3,194 at 2026-10-15, readiness universe 3,221
  (MAX over 3 sessions). Joins: active ∩ tech = 2,419; active − tech = 1,191
  (1,053 other + 108 possible ETF + 30 rights); tech − active = 1,006 (**585
  `_SME` twins** + 420 other + 1 fund). Nothing was added or removed — what "the
  active universe" *should* mean is a product decision.
- **Two existing tests changed, deliberately.** `test_data_readiness.py` gained
  an autouse `screening_facts` stub (its fixture redirects `DB_DIR`, not
  `DATA_DIR`, so the tests would otherwise probe this machine's real lake) plus
  four tests that re-patch it to assert the escalation.
  `test_scoring_components_are_not_modified_by_this_phase` was a whole-file
  `git diff` guard that this phase's legitimate `screener.py` reporting changes
  tripped: it now keeps the three scoring files byte-identical and checks
  `screener.py`'s changed lines for ranking markers instead — the Phase 15
  guarantee preserved, and made durable.
- **Not executed, by instruction.** No lake build. Section 7 of
  `PHASE16_SCREENING_RECOVERY_AND_READINESS.md` is the runbook
  (`python -m myra_app.lake_build --status` / `--limit N`), including the
  warning that `data/ohlcv_cache` is ~168 days behind `technical_data`, so a
  build from it will be correctly reported as `stale`.
- **Limitation stated rather than defaulted away.** The producer still emits
  neither `atr_pct`, `sma150`, `pct_above_ma50_60d`, `keltner_*`, `atr5`, nor
  `drawdown`, so a rebuilt lake restores *screening* but not the scorer's
  70%-of-score technical block. Nothing zero-fills them.
- **Verification.** 79 new tests; full suite **1624 passed** (Phase 15's 1545);
  Phase 15's 10 ATR characterization tests untouched and passing; `black` (the
  repo's pinned 26.x) and `flake8` clean; `tsc --noEmit` clean; production lake
  still 0 files.
- **Handoff.** Before A/B/C: run the section 7 build, confirm readiness reaches
  `ready`, then resolve item (1) of section 8 (the scorer's missing technical
  inputs). The ATR fix stays on its separate track.

## Phase 17 — Technical data provenance: a lake build would not restore the score

Plan and full evidence: `docs/PHASE17_TECHNICAL_DATA_PROVENANCE_AND_SCREENING_PLAN.md`.

- **Traced all seven scorer fields to real sources.** The lake producer
  (`librarian_intelligence._compute_symbol_indicators`) emits
  `sma20 sma50 sma200 rsi atr20 vol_sma50 avg_deliv_20d rdv delivery_pct vcp
  delivery_divergence_score delivery_accumulation_signal ias vwap fvg bos choch
  inst_vol` — and **six of the seven** the scorer reads
  (`sma150, atr_pct, pct_above_ma50_60d, keltner_upper, keltner_lower, atr5,
  drawdown`) are not among them. `universe_loader` turns each missing key into
  a stored `0`. `keltner_*` has **zero producers repo-wide**.
- **The zero-fill is not what costs accuracy.** Across 60 real symbols,
  post-build rows (fabricated zeros) and rows with the keys merely *absent*
  rank **identically** — same Spearman (0.7696) and same 3/10 top-10 overlap
  against provenance-correct values. The loss is the *unmeasured fields
  themselves*: `stability` (weight 0.15) is **identically 0.0 for all 60**
  symbols, `trend` collapses to just `{0.4, 0.6}`, `base` is identically `0.5`,
  and the squeeze bonus fires **0/60** times (vs 6/60 with real inputs).
  "Stop zero-filling" alone would change absolute scores (~1.4 pts, the Phase 15
  ATR effect) but not one rank.
- **Five more fields are dead by key-name mismatch alone** (§3b):
  `universe_loader` reads `avg_volume_20d`/`avg_delivery_20d`/`delivery_percent`
  where the lake emits `vol_sma50`/`avg_deliv_20d`/`delivery_pct`, and reads
  `rel_spread`/`rel_vol` which the lake never emits at all. Consequence:
  `VSA_Intensity` is identically `0`, and `Stage` is identically `"Stage 4"` for
  every symbol because `:184 if sma150 > 0:` guards on the fabricated zero.
  Lowercase `roe` (the key `precompute_ranks` reads) is never populated, so ROE
  contributes nothing to ranking.
- **The correct pattern already exists and is dormant** (§3c):
  `myra_app/ranking_inputs.py` leaves a metric `None` on source failure so the
  scorer applies its neutral fallback, and `_merge_row` never overwrites a real
  value with `None`/`0` — but it is opt-in (`MYRA_V25_RANKING_INPUTS`, default
  off) and `load_universe` is called from exactly one place, `engine.py:342`,
  without the flag. Note the internal inconsistency: `smart_money_score` was
  deliberately left `None` for having no producer, while the seven fields in
  the same dict were left as `0`.
- **The CLI/turbo path has three blockers, none of them the missing indicators.**
  (B1) `load_universe` returned the literal `[]` on an empty lake while its own
  `<10` fast path returned a `DataFrame`, so `run_scan`'s `cache_df.empty` raised
  `AttributeError: 'list' object has no attribute 'empty'` and the actionable
  verdict was **unreachable** — Phase 16's unit test passed only because it
  called `_abort_empty_universe` directly. (B2) `pl.read_database`'s schema
  inference fails at default/1,000/10,000 and succeeds only at 100,000/`None`
  (latent behind B1). (B3) `worker_pool.py:249`/`:310` reference `fvg_mid`,
  which is **assigned nowhere in the repo** — reproduced empirically: an
  always-matching stub returns `FAILED / NameError`; a non-matching stub returns
  `SKIPPED`. **No symbol can be returned as a candidate by the worker pool,
  whatever the data contains.**
- **One bounded fix applied (B1).** `universe_loader.py:69` now returns
  `pd.DataFrame()`, matching every other return. Verified against the live DB:
  the same 12-symbol scan that reported `failed_indicator_computation /
  "'list' object has no attribute 'empty'"` now reports
  `failed_missing_required_artifacts / indicator_lake_empty` with the build
  remedy. 4 new tests, including the **integration** through `run_scan` that
  Phase 16 omitted. Full suite **1628 passed**; `black`/`flake8` clean; all
  production DB mtimes unchanged and the lake still 0 files.
- **Deliberately not fixed:** `fvg_mid` (fixing it first would convert a visible
  failure into an invisible wrong answer scored on fabricated zeros), the Polars
  inference, the ATR scoring defect, the zero-fill, the key mismatches.
- **Both paths characterised, neither recovered.** Web/API scanners are healthy
  and current — 2,010 universe (route default `min_mcap=200`), **37 candidates**,
  24.6 s on `2026-10-16` — but produce a different construct (`trigger_score`),
  not `MYRA_Score_v25`, and record no data date. Recommendation: use it as the
  immediate research path; derive the six unsourced fields from `technical_data`
  (measured 57.9 ms/symbol) only after B1–B3, and **not** from the 168-day-stale
  OHLCV cache. `ranking_history` holds 0 rows.
- **Recovery is not claimed and cannot be** until an operator decision: refresh
  the OHLCV cache, or authorise deriving from `technical_data` — and in both
  cases authorise fixing B3.

## Phase 18 — Stop hiding a working scanner behind a blocked lake

Plan and full evidence: `docs/PHASE18_SCREENING_CONTINUITY_AND_DATA_OPERATIONS.md`.

- **The defect was a reporting defect, and it cut both ways.** Phase 16 modelled
  screening as one verdict — the lake is usable or it is not — so the operator
  was shown **"Screening blocked"** while the web/API `TriggerScanner` was
  healthy and producing candidates, and on a machine with a built lake the same
  single verdict would have let a healthy scanner make the incomplete v2.5
  ranking look operational.
- **Fixed by measuring per mode.** `screening_modes.py` (new) reports
  `operational` / `degraded` / `blocked` **per mode** — `trigger`
  (`trigger_score`, reads `technical_data` directly, no lake) and `v25_turbo`
  (`MYRA_Score_v25`, reads the derived lake) — plus an aggregate that is never
  stronger than the weakest mode and is `blocked` only when **no** mode can run.
  The two scores are declared **not comparable** as a field in the payload, not a
  comment. Missing inputs are reported **by name**; no missing value becomes a
  zero, a score, or a clean bill of health.
- **The runtime blockers are derived, not asserted.**
  `worker_path_blocker()` inspects the live `worker_pool` source for the
  unassigned `fvg_mid`, `polars_schema_blocker()` inspects `engine` for the
  implicit `pl.read_database`. Both clear themselves when the defect is fixed, so
  the report cannot go stale.
- **Live effect: readiness moved `blocked` → `partial`.** `assess()` now
  escalates from the mode aggregate (`degraded` → `partial`, `blocked` →
  `blocked`, escalation only). The explanation reads "TriggerScanner (web/API) is
  operational, but MYRA_Score_v25 turbo ranking (CLI) is unavailable…", with
  `trigger: operational` and `v25_turbo: blocked / lake_empty`.
- **One contract test updated deliberately, with its intent preserved.**
  `test_data_readiness.py`'s "fresh data + unusable screening is blocked" now
  asserts `partial` — because the trigger mode genuinely *is* available — while
  still asserting `!= ready`, and the `blocked` case is kept as its own separate
  test. Phase 16's precedent of updating a contract test alongside the contract.
  The mode axis is stubbed in that module exactly as `screening_facts` was,
  because `fixture_db_dir` redirects `DB_DIR` and not `DATA_DIR`.
- **One front door for data operations.** `data_ops.py` (new):
  `python -m myra_app.data_ops status|recommend|run <op>` plus an interactive
  menu. It *discovers* operations from `tasks.registry.TASKS` (so it cannot
  drift from the single source of truth) and invokes them through the real
  executor contract. It is not a scheduler: no thread, no period, no lock
  ownership, and it never starts anything by itself.
- **The taxonomy is the useful part.** `stale` / `not_ingested` /
  `derived_missing` are data problems with operations behind them;
  `mapping_mismatch` / `unsourced` / `blocked` are **not** data problems, and are
  reported as such with no operation offered. Live output recommends
  `lake-build` for the two inputs the lake really produces *and*, separately,
  states that 7 of 9 scorer inputs have **no producer in the repo** — so a built
  lake still cannot yield a trustworthy score.
- **Restraint by construction.** Every write requires explicit confirmation;
  every operation takes the existing `task_lock` and releases it in a `finally`;
  a task a **live** scheduler owns is refused (`unknown` counts as live, the safe
  side); the self-managed `watchdog` loop and the registry-disabled
  `screener-enrich` are neither offered nor recommended; and all heavy imports
  are function-local so an unset `MORNINGSTAR_TOKEN` (which makes
  `fundamental_sync` raise `KeyError` at import) cannot break `status` — asserted
  by a subprocess test with the variable removed.
- **Mission Control told the operator to fix the wrong thing.** Three defects in
  `App.tsx`: `disabled` was never surfaced at all (`schedulerUnhealthy` covered
  only `degraded`/`unknown`); the advice was hardcoded to "Leave DISABLE_SCHEDULER
  unset" even though it already was, in the common case where the real cause is
  that nothing started the scheduler-owning process; and `schedulerLabel` was
  computed then ignored in favour of a hardcoded title. All three fixed, and
  `DataReadinessPanel` now lists each screening mode with its state, its score
  name, and its unavailable inputs.
- **Verified:** full suite **1722 passed** (1628 + 42 + 52); `black`/`flake8`
  clean on every changed file; frontend `tsc --noEmit` clean and `vite build`
  succeeds; **no production DB writes** (all 13 `myra_*.db` mtimes unchanged, lake
  still 0 files); no `.myra_task_*.pid` left behind.
- **Deliberately not done:** the `fvg_mid` / Polars / key-mismatch fixes
  (fixing `fvg_mid` first would turn a *visible* failure into an *invisible*
  wrong answer scored on fabricated zeros), the lake build, the OHLCV rebuild,
  the fundamental refresh, and starting the scheduler — all operator
  decisions. The report names the exact command and reason instead of guessing.



## Phase 19 — The V2.5 signal contract, and the lake builder that could not write

*Goal: settle which values a published `MYRA_Score_v25` actually consumes and
where each comes from, repair the smallest proven set of execution defects, and
decide — from evidence — whether the indicator lake can be built. No lake build,
no OHLCV rebuild, no fundamental refresh, no scheduler start.*

- **The contract, not just the six names.** Phase 17 named six scorer inputs;
  reading the scorer (`score_components_v2.py`) end to end gives **17**:
  `sma50`/`sma150`/`sma200`/`close`, `pct_above_ma50_60d`, `avg_delivery_20d`,
  `smart_money_score`, `avg_volume_20d`, `atr_pct`/`keltner_upper`/`keltner_lower`/
  `atr5`, `drawdown`, `roe`/`ProfitGrowth`/`F_Score`, `regime`. `graham_number` is
  *not* consumed (`valuation_score` is never wired into the composite).
  `docs/V25_TECHNICAL_SIGNAL_CONTRACT.md` records each field's meaning, units,
  lookback, producer, missing behaviour and status.
- **Four classes of "missing", which used to wear one name.** Not built (a build
  fixes it) / no writer anywhere (a build never will) / producer exists but is
  switched off (`MYRA_V25_RANKING_INPUTS`, off by default — enabling it changes
  published ordering) / absent and zero-filled (already inside the numbers).
  `V25_MANDATORY_INPUTS` (7) vs `V25_OPTIONAL_INPUTS` (5) now drives the mode:
  a mandatory gap is `blocked`/`mandatory_inputs_unsourced`, an optional gap is
  `degraded`/`optional_inputs_unsourced` and never `operational`. `V25_GATED_INPUTS`
  and `V25_UNSOURCED_INPUTS` make the distinction machine-readable, and
  `evaluate()["v25_input_contract"]` exposes all of it.
- **The lake builder could not write a single artifact.** The headline finding of
  this phase, and it was not in the original blocker list. `_compute_symbol_indicators`
  returned a frame with duplicate column names — `delivery_pct`, `vwap`,
  `delivery_divergence_score` — because `get_ohlcv` already returns those
  lower-case, enriched columns, the `capitalize()` pass renames them, and the
  producer's own later assignment of the same names then appends a second column.
  `to_parquet` rejects it (`Duplicate column names found`), `save_indicators`
  re-raises, `build_indicator_lake` counts the symbol as `failed` — so a build
  would have reported *every* symbol as failed and `data/indicators/precomputed`
  could never be populated by anything. Fixed with the dedup idiom that method
  already used after `capitalize()`, `keep="first"`. That choice is what stops a
  fabricated zero (`Delivery_qty` is absent from `technical_data`, ~7% populated
  in the cache, so the dropped duplicate is `fillna(0.0)×100`) from replacing the
  measured NSE delivery percentage.
- **The lake's SMC indicators were fabricated zeros.** `librarian_intelligence`
  imported `SMCManager` from `myra_app.engine`, which has never contained it; the
  `ImportError` was swallowed by a blanket handler, so every artifact carried
  `fvg = 0.0 / bos = False / choch = False` as if measured. Now imported from
  `myra_app.smc_manager`, **outside** the `try` (so a misnamed module fails loudly
  instead of fabricating), with a per-symbol warning on the genuine fallback.
  Verified end-to-end: `fvg` now carries real values (−1/0/1, latest row 1).
- **`fvg_mid` was never assigned.** Both success paths referenced the name; the
  resulting `NameError` failed the whole universe, so no scan could surface a
  result whatever the data held. Bound to `fvg_zone["mid"]` — an established
  definition (`(top + bottom) / 2`, the same `mid` the canonical
  `SMCManager.get_fvg_buy_zone` returns), not an invented indicator. Each call
  site keeps its own written fallback.
- **The Polars read had no declared schema.** `pl.read_database` gets no types
  from a DBAPI cursor, so it inferred from a leading sample; `technical_data`'s
  leading rows are NULL-heavy, the sample resolved to `Null`, and the first real
  value raised `ComputeError: could not append value: 40.0`. New
  `myra_app/utils/sqlite_schema.py` derives an explicit schema from
  `PRAGMA table_info`; wired into both `engine.py` read sites. `infer_schema_length=None`
  was rejected because it leaves 17 all-NULL columns typed `Null`. A full scan of
  all 11 INTEGER-declared columns (8,022,725 rows) found zero non-integral values,
  so `Int64` is exact for the real data.
- **Verdict: the builder works, the lake still cannot produce a trustworthy
  score.** Only 2 of 17 consumed fields are lake-produced; 8 have no producer at
  all. And two data findings sit upstream of every indicator: on 2026-10-13 and
  2026-10-07 `technical_data` stores `sma_50 = close` for ~the whole universe
  (3220/3223) and `sma_200` NULL for every symbol (3221/3224); and the OHLCV cache
  and `technical_data` disagree by **exactly 2×** for `RELIANCE` (736 of 1216
  shared sessions, median ratio 2.0000) up to and including 2026-02-01 — one
  series is corporate-action adjusted and the other is not.
- **Restraint, recorded rather than applied.** The missing-`atr_pct` defect
  (absence → `0` → `base_score` awards **+0.5, the maximum**; while a zero-filled
  `pct_above_ma50_60d` returns **0**, a fabricated worst case) is documented with
  its exact fix but not changed, because it moves published scores. Eight
  definitions (`sma150` source, `atr_pct`, `atr5`, Keltner, `drawdown`,
  `smart_money_score`, `F_Score`) are listed as open investment-strategy
  decisions with options, not guessed at.
- **Scheduler (read-only):** lock absent, `state=degraded`, `DISABLE_SCHEDULER`
  unset, 0 python processes, port 8000 idle. Degraded because nothing started
  `python run_fastapi.py` — not because of a lock. Not started; the command is
  named instead. Manual screening and individual data-ops commands are unaffected.
- **Verified:** new `tests/test_phase19_signal_contract.py` (**27 tests**), full
  suite **1749 passed** (Phase 18: 1722), the targeted nine suites 245 passed,
  `black`/`flake8` clean; no frontend files changed. **No production writes:** lake
  still 0 files, every production read `mode=ro`, `live_db_guard` passed, no
  `.myra_task_*` left behind, working tree contains only the intended seven files.
  `myra_technical.db`'s mtime did not move this phase (still `2026-10-17 11:55:59`),
  so the earlier mid-Phase-18 advance remains undiagnosed and is carried forward.
- **Deliberately not done:** the lake build, the OHLCV rebuild, the fundamental
  refresh, starting the scheduler, enabling `MYRA_V25_RANKING_INPUTS`, and any
  weight/threshold/formula/ordering change.

## Phase 20 - Market-data integrity: two root causes found and fixed forward

*Goal: reproduce the three anomalies carried out of Phase 19 (OHLCV 2x
disagreement; sma_50==close / sma_200 NULL at universe scale; missing atr_pct ->
max base_score), find their root causes, repair only what is conclusively
justified, and return a verdict. No lake build, no OHLCV rebuild, no ingestion,
no scheduler start, no live-DB write.*

- **The 50-day-mean defect was far bigger than two sessions, and had a single
  root cause.** A full-universe re-scan (Phase 19 probed one date) found **12**
  degenerate sessions, not 2: 6 in 2026-08 (17-31), 4 in 2026-09 (8,11,21,28),
  2 in 2026-10 (7,13); table-wide sma_50==close on 40,431 rows and sma_200 NULL
  on 99.3% of 8,022,725. On each, all four columns degenerate together
  (sma_50==close, high_52w==high, low_52w==low, delivery_ma_60==delivery_pct) with
  sma_200 NULL for every symbol. Cause: `eod2_sync.sync_eod2_data` filtered to
  the new rows *before* calling `_compute_derived`, so a normal one-session sync
  handed it a 1-row frame, took its `len(df)>=2` else-branch, and stored the raw
  session values as if they were rolling statistics. `sma_200` was absent from
  `_INSERT_COLS`, and `INSERT OR REPLACE` then nulled it. The enrichment repair
  could not heal it because it writes only the latest date.
- **Repair (forward path only).** The sync now computes the rolling columns over
  the symbol's 252-row history plus the new rows (publishing only the new dates),
  `_compute_derived` no longer substitutes a raw price for a rolling statistic,
  and sma_200 is computed and written. **Fixed, not backfilled:** the 12 sessions
  and the 99.3% historical sma_200 NULLs stay until a backfill is authorised - a
  backfill is a forbidden bulk operation here. They cannot recur.
- **A silent whole-ingest hazard, caught by an existing test.** Adding sma_200 to
  the INSERT revealed that a table missing any written column makes every
  symbol's INSERT raise, which the per-symbol handler downgrades to a skipped
  symbol - a run could report "done" having stored nothing. `sync_eod2_data` now
  resolves the table's real columns and drops absent ones with a *warning*, so
  OHLCV still lands and the gap is visible. The recovery fixture's DDL now derives
  from `_INSERT_COLS` so it cannot drift again.
- **The missing-atr_pct defect is fixed at the fabrication site, not the scorer.**
  `atr_pct` has no producer anywhere, so `universe_loader`'s
  `c.get("atr_pct", 0)...else 0` stored a fabricated 0.0 for the whole universe,
  which `base_score` reads as `atr_pct<0.03` -> +0.5, the maximum - better than a
  genuinely mid base - and reported as an *observed* input. `universe_loader` now
  passes the value through, so an absent ATR lands on the same documented 0.3
  neutral that a real NULL/NaN/absent key already produced. This is the Phase 12
  pattern (ProfitGrowth/SalesGrowth/Debt_Equity); it is a data-integrity fix, not a
  scoring change - the bands, ranking formula and weights are untouched, so the
  Phase 15 characterisation tests still pass by design.
- **The OHLCV 2x disagreement is settled as to mechanism, open as to decision.**
  `technical_data` matches the eod2 source exactly and is the adjusted series; the
  parquet cache is a stale *unadjusted* snapshot (all mtimes <= 2026-05-02,
  window 2021-10-01..2026-04-30) whose per-symbol ratio is a constant
  corporate-action factor (RELIANCE 2.0, ZYDUSWELL 5.0, TCS/ITC/SBIN 1.0). Beyond
  the split (last differing session 2026-02-01) they agree, reproducing Phase 19's
  unexplained sma_200 gap to the decimal. Not a bug: rebuilding the cache or
  pointing the lake at `technical_data` are both forbidden bulk operations, and
  the authoritative series is an operator decision. Documented, not changed.
- **Input ledger re-verified against code:** sma150, pct_above_ma50_60d, atr_pct,
  atr5, keltner_*, drawdown and smart_money_score have no writer for the consumed
  column (a couple of strategy files compute a same-named *local*, but persist
  nothing); avg_volume_20d/avg_delivery_20d/roe/ProfitGrowth are behind the
  opt-in flag. pct_above_ma50_60d absence is *punished* (0.0) not rewarded, and
  Phase 5 deliberately left that alone - unchanged here.
- **Verdict: NOT READY for a controlled validation build.** Two root causes fixed
  forward, but the price basis is still undecided (D3) and six of seven mandatory
  inputs still have no enabled producer, so MYRA_Score_v25 stays MODEBLOCKED.
  TriggerScanner remains the usable mode; nothing published a v25 score.
- **Verified:** full suite **1768 passed** (Phase 19: 1749; +19 new), the whole
  eod2/recovery/ingest set clean under `-W error::FutureWarning`,
  `black`(pinned 26.10.1 via pre_commit)/`flake8`/Performance-Guard/schema-drift
  all pass. **No production writes:** every live read was `mode=ro`,
  `live_db_guard` not weakened, no ingestion/scheduler/bulk refresh run.
- **Deliberately not done:** the lake build, the OHLCV rebuild, the sma backfill,
  the eod2 source's phantom 2026-02-01 session, enabling MYRA_V25_RANKING_INPUTS,
  and any weight/threshold/formula/ordering change.

## Phase 21 - Price basis decided, recovery planned, V2.5 roadmap

*Goal: establish an evidence-backed path to a trustworthy V2.5 validation build -
resolve the price-basis question Phase 20 had to leave open, measure the
historical recovery scope, and roadmap the signal producers. No production
operation, no backfill, no ingestion, no lake build, no scheduler start.*

- **Verdict: READY FOR OWNER-APPROVED RECOVERY.** The Phase 20 blockers are
  resolved as decisions, not code. What remains is approval to execute, not
  missing evidence. Verdict is about readiness for the next approved operation,
  not permission to run it.
- **State verified against running code, not docs.** HEAD 58a89ea, tree clean,
  CI+CodeQL green; `data_readiness.assess()` = **partial**; TriggerScanner
  **operational**; V2.5 **blocked** on `lake_empty`; lake 0 files; latest session
  2026-10-16 == expected; universe 3213; scheduler still down; no V2.5 score
  published. `myra_technical.db` mtime unmoved at 2026-10-17 11:55:59.
- **The price-basis question is now conclusively answered.** `technical_data`
  (eod2) is corporate-action-**adjusted, back-adjusted**; the parquet OHLCV cache
  is a stale **unadjusted** snapshot. Proof is local and needs no external data:
  at each ex-date the cache drops by exactly the factor (RELIANCE 2024-10-28
  -49.76%, WIPRO 2024-12-03 -50.11%, HDFCBANK 2025-08-26 -50.44%, ZYDUSWELL
  2025-09-18 -79.80%, i.e. a 1:4 bonus) while db/eod2 show ordinary moves (+0.49%,
  -0.21%, -0.88%, +1.02%) and are byte-identical to each other. Volume is
  unscaled (ratio 1.0 on 1095/1096). Canonical basis = adjusted.
- **Correction to Phase 20.** The cache/DB factor is **not** a per-symbol constant
  over the window; it is a **single step at that symbol's ex-date** (RELIANCE
  2024-10-28, WIPRO 2024-12-03, HDFCBANK 2025-08-26, ZYDUSWELL 2025-09-18;
  TCS/ITC/SBIN/INFY have no action). The 2026-02-01 boundary Phase 20 reported is
  a calendar artifact - a Sunday row - not a price-basis boundary.
- **One seam mixes bases: `LibrarianCore.get_ohlcv`** (cache history + DB delta).
  The indicator lake inherits it via `_compute_symbol_indicators:125`, and
  `results_manager:449` charts it. Measured damage at a straddling window:
  sma50 RELIANCE 1446 -> 2849, ZYDUSWELL 426 -> 2106. A lake build today would
  roughly double every SMA for any symbol with an action in its trailing 200
  sessions. Recommended fix: have the lake read `technical_data` directly and
  retire the cache as a price source (one source of truth) rather than rebuild it.
- **Recovery is measurable and reproducible.** The 12 degenerate sessions are
  38,320 rows, and correct values are **recomputable from stored history** - the
  same recomputation reproduces a clean session exactly (RELIANCE 2026-10-16:
  sma_50 1260.5080 vs stored 1260.508, sma_200 1345.2610 vs 1345.261). So the
  12 sessions need recomputation only, no re-ingestion. A 5-step plan (R0 backup,
  R1 dry-run on a copy, R2 update, R3 sma_200, R4 re-audit) is specified with
  postconditions and rollback; the ~99.3% sma_200 history backfill is a
  *separate* decision deferred until the basis is fixed.
- **The phantom-session scope is larger than recorded.** 37 weekend-dated
  sessions exist, **9** universe-wide (2014-03-22, 2015-02-28, 2016-10-30,
  2023-11-12, 2024-01-20, 2024-03-02, 2024-05-18, 2025-02-01, 2026-02-01). And
  nothing is missing - 2026-01-31 is a **Saturday**, so the Sunday row is an
  *extra* record, not a shifted one. Origin is the eod2 source itself, so
  recovery needs the eod2 maintainer; MYRA must not delete the rows first, or the
  next ingest re-adds them under the same wrong dates.
- **V2.5 roadmap: 6 of 7 mandatory inputs need no new data.** `sma50`/`sma200`
  are produced; `avg_volume_20d` + `avg_delivery_20d` already have a validated
  producer behind the opt-in flag; `sma150` + `pct_above_ma50_60d` are decidable
  from columns that already exist. Only `atr_pct` genuinely needs a product
  decision. Critically, **the two gated inputs are volume/delivery-derived, so
  they are NOT blocked by the price-basis decision** - they can move first.
- **`MYRA_V25_RANKING_INPUTS` stays disabled.** Enabling it merges roe,
  ProfitGrowth, avg_delivery_20d and avg_volume_20d, which feed the roe/growth/
  delivery/liquidity components; the validation evidence it needs (score
  distribution re-measurement, coverage fraction, non-zero-filled check, one A/B
  ranking run) does not exist yet.
- **Verified:** new `tests/test_phase21_price_basis.py` (5 tests, temporary
  fixtures only, no live data) pins the price-basis contract without pinning the
  current cache state, so the planned repair will not read as a regression;
  `black`(pinned via pre_commit)/`flake8`/Performance-Guard/schema-drift clean.
  **No production writes:** every live read was `mode=ro`, `live_db_guard`
  preserved, no ingestion/backfill/lake build/scheduler operation.
- **Eight operator decisions** are listed in the Phase 21 report (F1-F8), the
  first being: adopt the adjusted basis and authorise the read-path fix.
