# MYRA Architecture Reference

## Overview

MYRA is a local-first NSE stock screening platform. Data flows from NSE sources through ingestion, enrichment, and storage, then is served via FastAPI to a React frontend. All 9 SQLite databases use WAL mode for concurrent read/write.

---

## Data Pipeline

```
NSE Market Archives (CSV)          Morningstar API          NSE-MCP
         │                              │                      │
         ▼                              ▼                      ▼
  mass_backfill.py              fundamental_sync.py     institutional_sync
  daily_ingestor.py             (bulk MS + yfinance)    (insider, deals,
         │                              │                FII/DII flows)
         ▼                              ▼                      ▼
  myra_technical.db            myra_valuation.db      myra_institutional.db
  (OHLCV + delivery,           (54-column funda)      (5 tables, institu-
   ~2.2M rows)                                         tional activity)
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
         ▼
  FastAPI Server (:8000)
         │
         ▼
  React Frontend (:3000)
```

## Database Layout

All databases reside in `myra_app/db/` and are referenced via `LibrarianCore.DB_MAP` — never hardcoded.

| Key | File | Primary Data |
|-----|------|-------------|
| `technical` | `myra_technical.db` | `technical_data` — OHLCV, delivery, SMC enrichment (~2.2M rows, 36 columns) |
| `valuation` | `myra_valuation.db` | `fundamentals` — PE, ROE, margins, market_cap, div_yield, etc. (54 columns) |
| `institutional` | `myra_institutional.db` | `large_deals`, `bulk_deals`, `block_deals`, `insider_trades`, `fii_dii_daily` |
| `meta` | `myra_metadata.db` | `symbols_master`, `index_constituents`, `etf_blocklist`, `sync_log`, `task_registry` |
| `governance` | `myra_governance.db` | Compliance, SAT disclosures, pledge history |
| `scoring` | `myra_scoring.db` | Pre-materialized fundamental scores (growth, quality, stability, risk) |
| `calendar` | `myra_calendar.db` | Market trading days, muhurat sessions |
| `network_cache` | `myra_cache_network.db` | HTTP response cache for external API calls |
| `options` | `myra_options.db` | `option_chain` + `pcr_snapshot` — live NSE option-chain PCR snapshots for market regime |

Schema definitions are maintained in `myra_app/schema_registry.py` (32 tables across all databases). On startup, `librarian_schema.py` validates every table exists with correct columns.

## Scanner Framework

8 scanners are registered via API endpoints in `myra_fastapi_server.py`. Each scanner:

1. Extends no base class — is a standalone class with a `scan()` method
2. Fetches its own universe via `_get_universe()` (market-cap filtered from `fundamentals` table)
3. Fetches per-symbol OHLCV + enrichment data via `_get_tech_data()`
4. Applies detection logic per-symbol
5. Returns a `pd.DataFrame` with `symbol`, scores, and metadata
6. Is thread-safe (each call spawns a thread; results cached to JSON in `models/`)

**Lookback-day convention:** All scanners use calendar days for lookback parameters. Internal minimum-row thresholds use `max(floor, int(lookback_days * 0.6) + 5)` to convert to approximate trading-day counts.

**DCB Bargain** follows the same `_get_universe`/`_get_tech_data`/`_sanitize_float` pattern as InvisibleHandScanner and LiquidityFlipDetector; its detection logic computes a delivery-weighted close on high-delivery days ( Delivery Cost Basis ) and flags symbols trading below that institutional accumulation price with positive delivery absorption.

## AI Second Opinion (Gemini LLM)

On-demand endpoint that calls the Gemini LLM to produce a BUY/SELL/HOLD signal for any NSE ticker.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ai-opinion/{ticker}` | GET | Returns signal, reason, confidence, source, cached status, and the technical summary the model evaluated |

**Key design decisions:**
- **On-demand only** — not wired into the alpha_ranker per-candidate loop (would hit the API per candidate).
- **24-hour cache** — results stored in `myra_ai_cache.db` (`ai_opinion_cache` table) to avoid redundant calls.
- **Graceful degradation** — if the Gemini API is unavailable or rate-limited, returns a degraded HOLD signal (never raises).
- **Module:** `myra_app/ai_second_opinion.py` — `build_technical_summary(symbol)` builds a compact text summary from local SQLite data; `get_ai_second_opinion(symbol, summary)` calls Gemini and normalises the response.

## Historical Time-Travel Scan (Invisible Hand)

The Invisible Hand scanner supports scanning as-of any past trading day, enabling users to backtest or investigate historical setups.

### How it works

1. **Frontend** (`InvisibleHandScanner.tsx`) renders a `<input type="date">` calendar widget next to the Scan button. On mount it fetches `GET /api/latest-trading-day` to set `max` on the picker (preventing future dates).

2. When the user clicks Scan, `scan_date` (YYYY-MM-DD) is included in the POST payload only if set. An empty/cleared date means "live scan" (latest available data).

3. **Backend** (`myra_fastapi_server.py`):
   - `POST /api/invisible-hand/scan` extracts `scan_date` from the payload.
   - `_get_latest_trading_day_before(date_str)` queries `technical_data` to find the nearest trading day on or before the given date (handles weekends and NSE holidays automatically).
   - If no `scan_date` is provided, the latest trading day is used (live scan).
   - `target_date` (the adjusted date) is passed to `InvisibleHandScanner(target_date=...)`.

4. **`InvisibleHandScanner`** (`invisible_hand_scanner.py`):
   - `__init__` accepts `target_date: Optional[str]`. When set, all data fetching uses this as the reference date.
   - `_get_tech_data(symbol, min_date, max_date=None)` adds `AND date <= max_date` to the SQL query when `max_date` is provided.
   - `scan()` uses `self.target_date or date.today().isoformat()` as the reference for lookback calculations.

5. **Response** includes `scanned_date` in the status payload — the actual date used after holiday/weekend adjustment. The frontend displays "Adjusted to YYYY-MM-DD (previous trading day)" when the user-selected date was adjusted.

### Constraints

- Historical scans are **read-only** — no database writes.
- The universe (symbols + market cap) is drawn from the **current** fundamentals table. For dates far in the past, some symbols may not yet be listed.
- The `scanned_date` field is always present in the status response after a scan completes.

## Enrichment Pipeline

`feature_enrichment.py` computes SMC indicators using vectorized Polars operations:

- **FVG (Fair Value Gap):** Detects 3-candle gaps; computes gap boundaries, freshness (days since gap), and gap width relative to range.
- **Swing Levels:** Identifies swing highs/lows via rolling window (`swing_window=3`); marks pivot points with distance to nearest swing level.
- **Liquidity Distance:** Normalized distance from current close to nearest swing high/low.
- **Trend Alignment:** SMA-50 vs SMA-200 comparison; categorical trend state (uptrend, downtrend, ranging).
- **Delivery MA:** 5-day and 20-day moving averages of delivery percentage.

Two entry points:
- `process_enrichment_pipeline(lib, conn, target_date)` — single-date enrichment (used by daily ingestion).
- `enrich_from_dataframe(full_df, nifty_df, target_date)` — batch enrichment from pre-loaded Polars DataFrame (used by `tools/enrich_history.py`).

## ML Models

Two XGBoost models are trained and serialized with `joblib`:

| Model | File | Features | Training Data | Purpose |
|-------|------|----------|---------------|---------|
| Forward Return | `models/forward_return.xgb` | 14 features (OHLCV + SMC) | ~578K train / 144K test | Predicts n-day forward return |
| Launchpad | `models/launchpad_xgb.joblib` | Technical + fundamental mix | Launchpad-labelled events | Identifies breakout candidates in digestion phase |

Training is triggered via the `/api/ml/train` and `/api/ml/launchpad/train` endpoints. Feature importance is queryable via `/api/ml/feature-importance`.

## Background Tasks

`background_orchestrator.py` manages daemon threads:

- **DB Doctor** — Daily schema/data-quality audit (runs at 02:00 IST)
- **Stale DB catch-up** — Detects missed ingestion days and triggers backfill
- **Database backups** — All 8 databases backed up with WAL checkpoint + timestamped copy
- **Pipeline task scheduling** — Configurable intervals for ingestion, enrichment, sync tasks

Task execution is logged in the `task_registry` table (meta.db) with status, duration, and error messages, queryable via `GET /api/pipeline/status`.

## Portfolio System

The portfolio tracker is a CLI tool (`tools/portfolio.py`) backed by a standalone SQLite database (`myra_portfolio.db`) and a data-access layer (`myra_app/portfolio_db.py`).

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

### Data Flow

1. **Bhavcopy ingestion** populates `technical_data` (OHLCV + delivery) in `myra_technical.db`.
2. After ingestion succeeds, the background orchestrator calls `auto_refresh_portfolio()`.
3. `auto_refresh_portfolio()` reads latest closes and dates from `technical_data` for each holding, storing them in the `price_cache` table.
4. Fundamentals join from `myra_valuation.db` → `fundamental_cache`.
5. CLI `view` reads from cache — no database joins at render time.

### Cache Strategy

| Cache Table | Source | Refresh Trigger | Read Latency |
|-------------|--------|----------------|--------------|
| `price_cache` | `technical_data` (bhavcopy) | Daily after ingest | Instant |
| `fundamental_cache` | `valuation.fundamentals` | On first view + daily refresh | Instant |
| `portfolio_meta` | Orchestrator timestamps | After each refresh | Instant |

### Integration Points

- **Background orchestrator** (`background_orchestrator.py:362`): Calls `auto_refresh_portfolio()` after daily ingestion completes. Best-effort — failure does not block the pipeline.
- **myra_portfolio.db** is gitignored. Contains all sensitive holding data locally.
- **--live flag**: The only code path that calls external APIs (yfinance for 15-min delayed prices). All other operations are fully offline once your broker XLSX is imported.

## Code Conventions

- All paths via `myra_app/constants.py` (`DB_DIR`, `DATA_DIR`, `CACHE_DIR`, etc.) — never `os.getcwd()`.
- DB filenames via `LibrarianCore.DB_MAP["key"]` — never hardcoded strings.
- DataFrame operations: list + `pd.concat()` instead of `df.append()` in loops.
- No chained indexing (`df[x][y]`) — use `.loc[x, y]`.
- No bare `except Exception: pass` — log errors with context.
- No DB queries inside per-symbol loops — batch fetches, then process.
- Schema changes: only ADD COLUMNS via ALTER TABLE, never drop.
- Verify syntax before commits: `python -c "import ast; ast.parse(open('file.py').read()); print('OK')"`.

## Frontend

React 19 + TypeScript + Vite in `myra_web/`. Key architecture choices:

- **API client:** `Librarian.ts` wraps all FastAPI calls with error handling.
- **State management:** Zustand stores for chart state, scanner results, settings.
- **Charts:** Plotly.js via `react-plotly.js` with a custom indicator registry for overlays (SMA, Bollinger Bands, FVGs, swing levels).
- **Scanner UI:** Each scanner has a dedicated view with PresetChip controls for parameter tuning, result cards with sortable columns, and CSV export.
- **Health monitoring:** `HealthStatusBar` component with auto-refresh (15s interval) and 3 severity levels (green/yellow/red) mapping to enrichment %, days behind, and database status.

## References

- `myra_app/schema_registry.py` — All 30 table definitions
- `myra_app/librarian_core.py:52` — `DB_MAP` dictionary
- `myra_app/feature_enrichment.py` — SMC enrichment (daily + batch)
- `myra_app/fundamental_sync.py` — MS_CANONICAL_MAP (camelCase→snake_case)
- `myra_app/background_orchestrator.py` — Daemon task management
- `myra_web/myra_fastapi_server.py` — All API endpoints (~70 routes)
- `tools/enrich_history.py` — Batch enrichment backfill
- `tests/` — 312-test pytest suite
