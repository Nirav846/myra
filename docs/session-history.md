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
