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
| ML models | 2 (forward_return.xgb + launchpad_xgb.joblib) |
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