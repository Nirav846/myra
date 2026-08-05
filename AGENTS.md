# Myra — Claude‑Mem Session Summary

## Project Scope
AI-powered Indian stock market screener (NSE). FastAPI + React frontend + Python backend (Polars, SQLite). 8 SQLite sidecar databases, 8 scanners (7 API-registered + 1 endpoint-only), XGBoost ML models, SMC enrichment pipeline.

---

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
- **Test count**: 85 tests, all passing.

---

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
| Test suite | 85 tests, all passing |
| CI | GitHub Actions (push/PR to main) |

## Architecture References
- `myra_app/schema_registry.py` — 30 table schemas
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
