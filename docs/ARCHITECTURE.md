# MYRA Architecture Reference

## Overview

MYRA is a local-first NSE stock screening platform. Daily OHLCV + delivery data flows in from **EOD2/BhavDesk CSVs**, is enriched with Smart Money Concepts (SMC) indicators, joined with fundamentals and mutual-fund / institutional signals, then served via a FastAPI bridge to a React frontend. All 9 SQLite databases use WAL mode for concurrent read/write.

The codebase is organised as a **monolith refactor**:
- `myra_app/` — backend logic split into focused packages (`tasks/`, `utils/`, `analysis/`, `fetchers/`, `db/`, `strategies/`).
- `myra_web/` — FastAPI app wiring + React frontend. `myra_web/myra_fastapi_server.py` is an **API bridge** that includes **19 routers** and wires the app; it no longer holds procedural endpoint logic.

---

## Data Pipeline

```
EOD2 / BhavDesk CSVs                 Fund Traction (GitHub Pages)        RupeeVest MF holdings
 (daily/ 20microns.csv)                     │                                  │
         │                                 ▼                                  ▼
         ▼                          fund_traction_sync.py             cross_buy_processor.py
  eod2_sync.py (USE_EOD2_DATA)     (fund_traction table)             (fund_cross_buy table)
  incremental per-symbol                  │                                  │
         │                                 └──────────────┬───────────────────┘
         ▼                                                ▼
  myra_technical.db                                myra_valuation.db
  (OHLCV + delivery,                              (fundamentals + traction
   ~2.2M rows)                                    + cross-buy)
         │
         ▼
  feature_enrichment.py (Polars)
  - FVG boundaries & freshness
  - Swing high/low levels
  - Liquidity distance
  - Trend alignment (50/200 SMA)
  - Delivery MA
         │
         ▼
  myra_technical.db (enriched columns)
         │
         ├──────────────────────────────────┐
         ▼                                  ▼
  FastAPI Bridge (:8000)             myra_app/analysis/rrg.py
  19 routers / 33 scanner           (RS-Ratio + RS-Momentum
  endpoints                         from raw EOD2 data)
         │                                  │
         ▼                                  ▼
  React Frontend (:3000)            /api/rrg/*
  (42 routes)
```

### EOD2 data sync

- `USE_EOD2_DATA = True` (`constants.py:30`). When enabled, `myra_app/tasks/ingest.py` calls `sync_eod2_data()` instead of the NSE bhavcopy fetcher.
- `sync_eod2_data()` (`eod2_sync.py:199`) performs **incremental** sync: only rows newer than the DB's `max(date)` are inserted, then the enrichment pipeline runs.
- Source candidates: `eod2/src/eod2_data/daily` (primary, BhavDesk), `eod2_data/daily` (fallback), and a hard-coded absolute path.
- CSV format: `Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY`.
- Column mapping (`_RENAME_MAP`): `Date→date, Open→open, High→high, Low→low, Close→close, Volume→volume, DLV_QTY→delivery, TOTAL_TRADES→trades`. Derived per-symbol columns: `delivery_pct`, `delivery_ratio` (`delivery_source="eod2_adjusted"`), `sma_50`, `high_52w`, `low_52w`, `delivery_ma_60`.

### Fund traction & cross-buy sync

- **Fund Traction** (`fund_traction_sync.py`) — syncs `fund_traction` (PK `(symbol, month)`) into `myra_valuation.db` from GitHub Pages JSON (`TRACTION_BASE_URL = "https://nirav846.github.io/cross-fund-holdings-traction/data/"`), months `2026-04` onward. `update_traction_sma()` recomputes the SMA in a batch temp table.
- **Cross-Buy** (`cross_buy_processor.py`) — builds `fund_cross_buy` (PK `(symbol, month)`) from local RupeeVest holdings CSVs under `cross-fund-holdings-traction/temp_holdings/`. Computes `cross_buy_ratio`, `signal_tag` (`STRONG_CROSS_BUY` ≥5 funds & ratio ≥0.7; `CROSS_BUY` ≥0.5; `MIXED` ≥0.25; else `STYLE_CONCENTRATED`). `DEFAULT_MONTHS = ["2026-04","2026-05","2026-06","2026-07"]`.

### RRG (Relative Rotation Graph)

`myra_app/analysis/rrg.py` reads EOD2 CSV data directly (`DATA_FOLDER`, `META_PATH = meta.json`). It computes RS-Ratio + RS-Momentum vs a benchmark, classifies into quadrants (Leading / Weakening / Lagging / Improving), resamples weekly (W-FRI), z-score normalises, and caches with meta.json invalidation + 6h TTL. Exposed via `/api/rrg/*`.

## Database Layout

All databases reside in `myra_app/db/` and are referenced via `LibrarianCore.DB_MAP` — never hardcoded.

| DB_MAP Key | File | Primary Data |
|-----|------|-------------|
| `technical` | `myra_technical.db` | `technical_data` — OHLCV, delivery, SMC enrichment (~2.2M rows) |
| `valuation` | `myra_valuation.db` | `fundamentals`, `quarterly_results`, `fund_traction`, `fund_cross_buy`, `full_fundamental_cache` |
| `institutional` | `myra_institutional.db` | `large_deals`, `bulk_deals`, `block_deals`, `insider_trades`, `fii_dii_daily` |
| `meta` | `myra_metadata.db` | `symbols_master`, `index_constituents`, `etf_blocklist`, `sync_log`, `task_registry`, `lineage_tracking`, `etf_sync_log` |
| `governance` | `myra_governance.db` | Compliance, SAT disclosures, pledge history |
| `scoring` | `myra_scoring.db` | Pre-materialized fundamental scores |
| `calendar` | `myra_calendar.db` | Market trading days, muhurat sessions |
| `network_cache` | `myra_cache_network.db` | HTTP response cache for external API calls |
| `options` | `myra_options.db` | `option_chain` + `pcr_snapshot` — live PCR snapshots for market regime |

Schema definitions are maintained in `myra_app/schema_registry.py` (**32 tables** across all databases). On startup `librarian_schema.py` validates every table exists with correct columns. `fund_traction`, `fund_cross_buy`, `sync_metadata`, and `full_fundamental_cache` are defined directly in their sync modules (not in SchemaRegistry).

## Backend Structure (`myra_app/`)

| Package | Contents |
|---------|----------|
| `tasks/` | Declarative **TaskSpec registry** (`registry.py`, 12 tasks) + executor (`executor.py`) with per-task duration logging. Task entrypoints: daily-ingest, fundamentals-sync, institutional-sync, etf-sync, index-sync, db-backup, fund-traction-sync, cross-buy-sync, traction-sma-update, watchdog, screener-enrich. |
| `analysis/` | `rrg.py` — RRG rotation engine. |
| `fetchers/` | `full_fundamentals.py` — Screener.in scrape + chart API + yfinance deep dive (Graham, Piotroski, DCF). |
| `db/` | `bulk_loader.py` — single-query `load_ohlcv_for_universe()` for scanners. |
| `utils/` | Shared helpers (bhavcopy parser, finstack bridge, task_utils, smc_calculator, feature_enricher, etc.). |
| `strategies/` | 63 strategy files (62 top-level + `alpha/position_sizer.py`). |

## API Bridge (`myra_web/`)

`myra_web/myra_fastapi_server.py` (`FastAPI(title="MYRA v3.2 API Bridge")`) includes **19 routers**:

1. `routes/fundamentals.py` → `/api/fundamentals`
2. `routes/full_fundamentals.py` → `/api/full-fundamentals`
3. `routes/sentiment.py` → `/api/sentiment`
4. `routes/ai_opinion.py` → `/api/ai-opinion`
5. `routes/chart.py` → `/api/chart`
6. `routes/search.py` → `/api/search`
7. `routes/finstack.py` → `/api/finstack`
8. `routes/ml.py` → `/api/ml`
9. `routes/tools.py` → `/api/tools`
10. `routes/tools.py` → `/api/portfolio-tools`
11. `routes/portfolio.py` → `/api/portfolio`
12. `routes/health.py` → `/api`
13. `routes/scanners.py` → `/api`
14. `routes/query.py` → `/api/query`
15. `routes/confluence.py` → `/api/confluence`
16. `routes/pipeline.py` → `/api/pipeline`
17. `routes/rrg.py` → `/api/rrg`
18. `routes/fund_traction.py` → `/api/fund-traction`
19. `routes/cross_buy.py` → `/api/cross-buy`

CORS is restricted to `["http://localhost:3000", "http://localhost:5173"]`.

## Scanner Framework

**15 scanners** are registered in `myra_web/routes/scanners.py`. **13** are registered via a **scanner factory** (`register_scanner(name, ...)`), each producing `GET /{name}/status` + `POST /{name}/scan`. **2** custom scanners (Launchpad, Multibagger) are non-factory.

Factory-registered scanners and their classes:

| Route name | Scanner class |
|---|---|
| `invisible-hand` | `InvisibleHandScanner` |
| `trigger` | `TriggerScanner` |
| `liquidity-flip` | `LiquidityFlipDetector` |
| `dcb-bargain` | `DCBBargainScanner` |
| `smart-money-bargain` | `SmartMoneyBargainScanner(DCBBargainScanner)` |
| `operator-fingerprint` | `OperatorFingerprintScanner` |
| `float-exhaustion` | `FloatExhaustionScanner` |
| `seasonal-delivery` | `SeasonalDeliveryHarvester` |
| `darvas` | `DarvasBoxScanner` |
| `wyckoff` | `WyckoffAutomaton` |
| `bottom-hunter` | `BottomHunter` |
| `climax-accumulation` | `ClimaxAccumulationScanner` |
| `delivery-divergence` | `DeliveryDivergenceScanner` |

Each factory scanner:
1. Extends no base class — is a standalone class with a `scan()` method.
2. Fetches its universe via `_get_universe()` (market-cap filtered from `fundamentals`).
3. **Bulk-loads** OHLCV + enrichment for the whole universe in one query via `myra_app/db/bulk_loader.load_ohlcv_for_universe()` (13 scanners) instead of per-symbol SQLite connects.
4. Applies detection logic per-symbol (sliced from the in-memory window).
5. Returns a `pd.DataFrame` with `symbol`, scores, and metadata.
6. Is thread-safe (each call spawns a thread; results cached to JSON in `models/`).

Additional explicit endpoints in `scanners.py`: `GET /dcb-bargain/defaults`, `GET /delivery-divergence/defaults`, `GET/POST /launchpad/*`, `POST /multibagger/scan`, `GET /multibagger/status`, `DELETE /cache/{scanner_name}`.

**Lookback-day convention:** All scanners use calendar days for lookback parameters. Internal minimum-row thresholds use `max(floor, int(lookback_days * 0.6) + 5)` to convert to approximate trading-day counts.

**Wyckoff detection notes:** All Wyckoff baselines (`avg_vol`, `avg_del_pct`, `range_low`, `range_high`) are expanding (rolling-to-signal-day) series — no future information enters any gate. The `range_low_90` / `range_high_90` fields reported per event are signal-local (rolling up to the event candle), not window-global. Springs with `two_candle_confirm=True` are dated on the confirmation candle's `event_date` (the next session), so `days_since` measures from confirmation rather than the grab candle. Equal-low zone detection only scans rows up to the grab candle (no forward look).

## Task Executor

`myra_app/tasks/registry.py` defines a frozen `TaskSpec` dataclass: `module, label, interval_days, catchup, stagger, mark_on_failure, mark_on_success, enabled, entrypoint, self_loop`. The `TASKS` dict has 12 entries; keys are historical thread names (e.g. `"etf-sync"`), and `label` is the `sync_log` key consumed by data-health — both must stay stable.

`executor.py` (`run_periodic`) runs each task in a loop: disabled → return; `self_loop` → call once; startup catch-up when `_is_task_overdue`; then an infinite due-check poll (`POLL_SECONDS=60`). `_execute_once` logs `"[MYRA BG] Task {name} completed in {duration:.2f}s"` (or `failed after ...`) using `time.perf_counter()`, giving per-task duration telemetry. Threads are launched with a 30s stagger.

## Enrichment Pipeline

`feature_enrichment.py` computes SMC indicators using vectorized Polars operations:

- **FVG (Fair Value Gap):** Detects 3-candle gaps; computes gap boundaries, freshness (days since gap), and gap width relative to range.
- **Swing Levels:** Identifies swing highs/lows via rolling window (`swing_window=3`); marks pivot points with distance to nearest swing level.
- **Liquidity Distance:** Normalized distance from current close to nearest swing high/low.
- **Trend Alignment:** SMA-50 vs SMA-200 comparison; categorical trend state (uptrend, downtrend, ranging).
- **Delivery MA:** 5-day and 20-day moving averages of delivery percentage.

Two entry points:
- `process_enrichment_pipeline(lib, conn, target_date)` — single-date enrichment (used by daily ingestion).
- `enrich_from_dataframe(full_df, nifty_df, target_date)` — batch enrichment from a pre-loaded Polars DataFrame (used by `tools/enrich_history.py`).

## ML Models

Two XGBoost models are trained and serialized with `joblib`:

| Model | File | Purpose |
|-------|------|---------|
| Forward Return | `models/forward_return.xgb` | Predicts n-day forward return |
| Launchpad | `models/launchpad_xgb.joblib` | Identifies breakout candidates in digestion phase |

Training is triggered via `/api/ml/train` and `/api/ml/launchpad/train`. Feature importance is queryable via `/api/ml/feature-importance`.

## Background Tasks

`myra_app/tasks/` (executor + registry) manages background work: DB Doctor audit, stale-database catch-up, daily backups (WAL checkpoint + timestamped copy), and configurable pipeline task scheduling. Task execution is logged in the `task_registry` table (meta.db) with status, duration, and error messages, queryable via `GET /api/pipeline/status`.

## Deep Fundamentals

`myra_app/fetchers/full_fundamentals.py` combines three sources and caches into the `full_fundamental_cache` table in `myra_valuation.db`:
1. Scrapling headless-browser scrape of Screener.in company pages (falls back to requests + BS4 chart API if Scrapling is unavailable).
2. Screener.in chart API timeseries (PBV, P/E, ROCE, Market-Cap-to-Sales, ROE).
3. yfinance (analyst recs, D/E, growth, beta, sector).

`generate_insights` computes the **Graham Number + defensive criteria**, a **simplified 6-criterion Piotroski F-Score**, a **two-stage DCF intrinsic value**, and 12+ additional insight categories. Served via `GET /api/full-fundamentals/{symbol}?refresh=false`.

## Portfolio System

The portfolio tracker is a CLI tool (`tools/portfolio.py`) backed by a standalone SQLite database (`myra_portfolio.db`, gitignored) and a data-access layer (`myra_app/portfolio_db.py`).

### Architecture

```
myra_portfolio.db          ←── portfolio_db.py (CRUD + cache layer)
         │                          │
         │                          ├── import_holdings()  — from broker XLSX
         │                          ├── get_all_holdings() — current positions
         │                          ├── record_snapshot()  — daily NAV
         │                          ├── auto_refresh_portfolio() — called by orchestrator
         │                          ├── get_delivery_metrics()  — technical_data join
         │                          ├── get_technical_position() — SMA/52w context
         │                          ├── get_sector_allocation() — valuation.db join
         │                          └── get_scanner_overlap()  — scanner caches join
         │
         ▼
tools/portfolio.py (CLI: view, import, refresh, scanner, risk, alerts, ...)
```

### Cache Strategy

| Cache Table | Source | Refresh Trigger | Read Latency |
|-------------|--------|----------------|--------------|
| `price_cache` | `technical_data` (bhavcopy) | Daily after ingest | Instant |
| `fundamental_cache` | `valuation.fundamentals` | On first view + daily refresh | Instant |
| `portfolio_meta` | Orchestrator timestamps | After each refresh | Instant |

### Integration Points

- **Task executor** calls `auto_refresh_portfolio()` after daily ingestion completes. Best-effort — failure does not block the pipeline.
- **--live flag**: The only code path that calls external APIs (yfinance for 15-min delayed prices). All other operations are fully offline once your broker XLSX is imported.

## Code Conventions

- All paths via `myra_app/constants.py` (`DB_DIR`, `DATA_DIR`, `CACHE_DIR`, etc.) — never `os.getcwd()`.
- DB filenames via `LibrarianCore.DB_MAP["key"]` — never hardcoded strings.
- DataFrame operations: list + `pd.concat()` instead of `df.append()` in loops.
- No chained indexing (`df[x][y]`) — use `.loc[x, y]`.
- No bare `except Exception: pass` — log errors with context.
- No DB queries inside per-symbol loops — batch fetches first (use `bulk_loader`).
- Schema changes: only ADD COLUMNS via ALTER TABLE, never drop.
- Verify syntax before commits: `python -c "import ast; ast.parse(open('file.py').read()); print('OK')"`.

## Frontend

React 19 + TypeScript + Vite 6 in `myra_web/`. Key architecture choices:

- **API client:** `Librarian.ts` wraps all FastAPI calls with error handling.
- **State management:** Zustand stores for chart state, scanner results, settings.
- **Charts:** Plotly.js via `react-plotly.js` with a custom indicator registry for overlays (SMA, Bollinger Bands, FVGs, swing levels) plus Recharts.
- **Routing:** 42 routes in `App.tsx`, grouped into navigation tabs: **Dashboard** (MissionControl, Portfolio), **Scanners** (price action, institutional/flow, ML/momentum, delivery/volume, overview), **Analysis** (Deep Fundamentals, RRG, Fundamentals, News Sentiment, etc.), **Data** (Data Sync, Parquet Lake, Sector Flow, ML Lab), **Experimental**.
- **Scanner UI:** Each scanner has a dedicated view with PresetChip controls for parameter tuning, result cards with sortable columns, and CSV export.
- **Health monitoring:** `HealthStatusBar` component with auto-refresh (15s interval) and 3 severity levels (green/yellow/red) mapping to enrichment %, days behind, and database status.

## References

- `myra_app/schema_registry.py` — All 32 table definitions
- `myra_app/librarian_core.py:52` — `DB_MAP` dictionary (9 keys)
- `myra_app/tasks/registry.py` — TaskSpec registry (12 tasks)
- `myra_app/tasks/executor.py` — Duration-logged task execution
- `myra_app/db/bulk_loader.py` — Single-query OHLCV loader for scanners
- `myra_app/eod2_sync.py` — EOD2/BhavDesk sync
- `myra_app/fund_traction_sync.py`, `myra_app/cross_buy_processor.py` — MF signals
- `myra_app/analysis/rrg.py` — RRG engine
- `myra_app/fetchers/full_fundamentals.py` — Deep Fundamentals
- `myra_app/feature_enrichment.py` — SMC enrichment (daily + batch)
- `myra_app/fundamental_sync.py` — MS_CANONICAL_MAP (camelCase→snake_case)
- `myra_web/myra_fastapi_server.py` — API bridge (19 routers)
- `myra_web/routes/scanners.py` — Scanner factory (15 scanners)
- `tools/enrich_history.py` — Batch enrichment backfill
- `tools/backtest_wyckoff.py` — Wyckoff per-event-type backtest harness (SC / AR / ST / Spring / SOS; seed 42, 400-symbol sample from the 510–530,000 Cr universe, calendar-day horizons [20..180], benchmark excess vs `^NSEI` from `myra_metadata.db`, 180-day forward guard; `--dump-sc` for post-fix SC spot-checks)
- `docs/wyckoff_sc_spotcheck.md` — 12-row manual SC verification proving the post-fix events satisfy their gates with no look-ahead
- `tests/` — 382-test pytest suite
