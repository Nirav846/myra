# ARCHITECTURE – Data Flow & Design

## High-Level Data Flow

```
data/Market_Archives/ (bhavcopy CSVs)
         │
         ▼
mass_backfill / daily_ingestor
         │
         ▼
myra_technical.db (technical_data table)
         │
         ▼
feature_enrichment.py (SMC indicators)
         │
         ▼
myra_technical.db (enriched columns)

Morningstar API
         │
         ▼
fundamental_sync
         │
         ▼
myra_valuation.db (fundamentals table)

NSE-MCP (Model Context Protocol)
         │
         ▼
institutional_sync (subprocess)
         │
         ▼
myra_institutional.db (large_deals, bulk_deals, block_deals, insider_trades, fii_dii_daily)

background_orchestrator.py (daemon threads)
         │
         ├─► DB Doctor (daily audit)
         ├─► Catch-up logic (missed days)
         └─► Interval-based scheduling

FastAPI Server (myra_fastapi_server.py)
         │
         ├─► /api/health (DB connection status)
         ├─► /api/query (SQL queries via POST)
         ├─► /api/market-breadth (advances/declines)
         ├─► /api/tools/status (pipeline task timestamps)
         ├─► /api/tools/execute (run maintenance scripts)
         ├─► /api/db-size (database size in MB)
         ├─► /api/ml/status (ML model status)
         ├─► /api/ml/train (train ML model)
         ├─► /api/ml/predict (get predictions)
         ├─► /api/ml/launchpad/predict (launchpad predictions)
         ├─► /api/finstack/morning-brief (FinStack MCP, optional)
         └─► /api/fundamentals/live/{symbol} (live fundamentals)

React Frontend (myra_web/src/)
         │
         ├─► Librarian.ts (API client)
         ├─► Views (MissionControl, AdvancedChart, scanners)
         ├─► Chart engine (indicator registry, trace builders)
         └─► PlotlyCanvas + Zustand store
```

## Database Schema Summary

### myra_technical.db
- **technical_data** – 36 columns, PRIMARY KEY (symbol, date), 2.35M+ rows
  - OHLCV: open, high, low, close, volume
  - Delivery: delivery, delivery_pct, delivery_ratio, delivery_qty, delivery_divergence_score
  - Volatility: volatility_compression_score, relative_volume_score
  - Performance: stock_return, market_return, nifty_outperformance_score
  - SMC: fvg_boundary, fvg_freshness, swing_high, swing_low, liquidity_distance, trend_alignment
  - Metadata: delivery_source, trades, vwap

### myra_valuation.db
- **fundamentals** – 44 columns (PE, ROE, margins, market_cap, face_value, issued_size, net_margin, roe_ttm, dividend_yield, daily_volatility, annual_volatility, impact_cost, source_ms, source_nse)

### myra_institutional.db
- **large_deals** – Large deal transactions (> ₹10L)
- **bulk_deals** – Bulk deal transactions
- **block_deals** – Block deal transactions
- **insider_trades** – Insider trading data
- **fii_dii_daily** – FII/DII daily flows

### myra_scoring.db
- **fundamental_scores** – Pre-materialized fundamental scores (symbol, date, growth_score, quality_score, stability_score, risk_score, total_funda_score, grade)

### myra_governance.db
- Compliance and governance metadata for the symbol universe

### myra_scoring.db, myra_cache_network.db, myra_calendar.db
- Scoring cache, network request cache, and market calendar (trading days, muhurat sessions)

### myra_metadata.db
- **etf_blocklist** – ETF symbols to exclude
- **index_constituents** – Index membership (Nifty 50, Nifty 500, etc.)
- **symbols_master** – Symbol metadata (sector, industry, instrument_type, first_seen, last_seen, in_active_universe, in_nifty500)
- **metadata** – General metadata
- **lineage_tracking** – Data lineage tracking
- **sync_log** – Sync task timestamps

## Key Pipeline Components

### background_orchestrator.py
- Interval-based scheduler for daemon threads
- DB Doctor daily audit (schema validation, data quality checks, WAL mode)
- Catch-up logic for missed ingestion days
- Manages background tasks without blocking main thread

### feature_enrichment.py
- SMC indicators calculation:
  - **FVG (Fair Value Gap)** – Detects price gaps between three candles, calculates boundaries and freshness
  - **Swing Levels** – Identifies swing highs/lows based on lookback period
  - **Liquidity Distance** – Measures distance to nearest swing level
  - **Trend Alignment** – SMA-based trend detection (50/200 SMA)
  - **Delivery MA** – Moving average of delivery percentage
- Vectorized operations using Polars for performance
- Writes enriched columns back to technical_data table

### fundamental_sync.py
- Morningstar bulk sync for all symbols
- NSE per-symbol fallback for missing data
- Live-first, DB-fallback for fundamental snapshots
- Handles PE, ROE, margins, market cap, and valuation metrics

### institutional_sync.py
- NSE-MCP integration via subprocess
- Fetches insider trades, large deals, bulk deals, block deals
- FII/DII daily flow data
- Error handling and retry logic for network issues

## Frontend Architecture

### Views
- **MissionControl** – Dashboard with market breadth, sector flow, scanner results
- **AdvancedChart** – Interactive charting with custom indicators
- **ReversionEngine** – Mean reversion strategy scanner
- **MultibaggerMatrix** – Multibagger detection matrix
- **SectorFlow** – Sector-wise money flow analysis
- **HistoricalSearch** – Historical pattern search
- **Leaderboard** – Top performers ranking
- **FVGScanner** – Fair Value Gap scanner
- **GhostSimulator** – Ghost pattern scanner
- **InstDOM** – Institutional DOM analysis
- **FiiDiiScanner** – FII/DII flow scanner
- **PriceDeliveryDivergenceScanner** – Price vs delivery divergence scanner
- **ValueRanker** – Value ranking scanner
- **Settings** – Application settings
- **Tools** – Utility tools

### Core Libraries
- **Librarian.ts** – API client for FastAPI backend
- **scannerPresets.ts** – Scanner preset configurations
- **bucketUtils.ts** – Bucketing and aggregation utilities

### Chart Engine
- **Indicator Registry** – Central registry for all technical indicators
- **Trace Builders** – Plotly trace builders for each indicator type
- **PlotlyCanvas** – Plotly canvas component with zoom, pan, and crosshair
- **Zustand Store** – State management for chart state and settings

### Performance Optimizations
- **Web Worker Aggregation** – Heavy computations in Web Workers
- **Chunked Historical Loading** – Load historical data in chunks
- **Performance Mode** – Reduced rendering for large datasets

## Design Decisions

### SQLite + WAL Mode
- **Rationale:** Single-user laptop reliability without database server overhead
- WAL (Write-Ahead Logging) allows concurrent reads and writes
- Sidecar architecture — 8 dedicated DBs (technical, valuation, institutional, meta, governance, scoring, calendar, network_cache) in `myra_app/db/` — prevents schema contention. All paths resolved exclusively via `LibrarianCore.DB_MAP`.
- Zero configuration, portable database files

### Daily Batch EOD vs Real-Time Streaming
- **Rationale:** NSE data is only available EOD; real-time streaming adds complexity without benefit
- Batch processing is more efficient and reliable for historical analysis
- Allows for comprehensive data validation and enrichment
- Reduces API rate limits and network dependency

### Frontend-Side Indicator Calculation
- **Rationale:** Flexibility for users to customize indicators without backend changes
- Reduces backend load and API latency
- Enables interactive parameter tuning
- Plotly provides rich visualization capabilities

### Interval-Based Scheduling vs Hardcoded Weekdays
- **Rationale:** Handles holidays and market closures gracefully
- More resilient to schedule changes
- Allows for catch-up logic on missed days
- Configurable intervals per task

### Live-First, DB-Fallback for Fundamental Snapshots
- **Rationale:** Ensures latest data is always available
- Fallback to cached data if API is down
- Reduces API calls and rate limit issues
- Provides better user experience during outages

## DB Access Contract

| Rule | Detail |
|------|--------|
| DB location | Always `myra_app/db/` via `DB_DIR` from `constants.py` |
| DB filename | Always via `LibrarianCore.DB_MAP["key"]` — never hardcoded |
| Connections | Opened once in `LibrarianCore.__init__()`, shared via `_tech_conn`, `_meta_conn`, `_val_conn`, `_inst_conn`, `_gov_conn` |
| Thread safety | All writes use `with LibrarianCore._db_lock:` |
| Frontend access | Read-only via FastAPI — never direct DB access from the React frontend |
| Valuation DB | `myra_valuation.db` is backend-only — never exposed directly to the frontend |

## Backtest Flow

`myra_app/backtest_engine.py` implements a single-position-per-day backtest loop that walks the trading calendar top-down, opens a new top-1 position each day, evaluates one of three exit modes, applies NSE-style costs, and aggregates per-trade P&L into a summary. Below is the per-day control flow:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  run_backtest(conn, config)                                                 │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │ _resolve_window(requires_delivery, start, end)                       │  │
│   │   → picks train (2015/2019 → 2023) | holdout (2024 → HOLDOUT_END) | all│ │
│   │   → TRAIN_START_DELIVERY if signal.requires_delivery else 2015-01-01 │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                  ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │ _trading_days(conn, start, end)                                       │  │
│   │   → prefer myra_calendar.market_calendar.is_trading_day=1             │  │
│   │   → fallback: DISTINCT date FROM technical_data                       │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                  ▼                                          │
│   for day_idx, day_iso in enumerate(trading_days):                          │
│                                                                             │
│   ┌─── (1) UNIVERSE FILTER ───────────────────────────────────────────┐    │
│   │ _eligible_symbols_at_date(conn, day_ts, seed_universe=None)        │    │
│   │   a. instrument_type='EQUITY' from symbols_master                   │    │
│   │   b. has technical_data within trailing 90 days                     │    │
│   │   c. NOT in discontinuity blackout (±5 trading days of z>6 events)  │    │
│   │   → returns sorted list[str] of eligible symbols                   │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                  ▼                                          │
│   ┌─── (2) SIGNAL SCORING ────────────────────────────────────────────┐    │
│   │ signal.score(day_ts, eligible, conn) -> pd.Series (idx=symbol)     │    │
│   │   dropna(); restrict to eligible symbols                            │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                  ▼                                          │
│   ┌─── (3) TOP-1 SELECTION ───────────────────────────────────────────┐    │
│   │ top_sym = scores.idxmax()        # always open a new position       │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                  ▼                                          │
│   ┌─── (4) POSITION MANAGEMENT ───────────────────────────────────────┐    │
│   │ • ADV cache refreshed every 20 trading days for impact cost        │    │
│   │ • Forward slice loaded (≤ max(fixed_hold+5, 200) trading days)     │    │
│   │ • Position size = ₹10,000 (POSITION_VALUE_INR) per new position    │    │
│   │ • Concurrent positions allowed — multiple open simultaneously     │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                  ▼                                          │
│   ┌─── (5) EXIT RULE EVALUATION (one of three modes) ─────────────────┐    │
│   │ exit_mode='fixed'   → _exit_fixed_holding:  close at N trading days│    │
│   │ exit_mode='trailing'→ _exit_trailing_stop: 20% trail on max-high   │    │
│   │ exit_mode='rule'    → _exit_rule_based: 5% stop OR close < SMA(N)  │    │
│   │   returns (exit_idx, exit_reason); exits at last day if no trigger │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                  ▼                                          │
│   ┌─── (6) COST APPLICATION ──────────────────────────────────────────┐    │
│   │ entry_value = ₹10,000 (no STT on buy)                              │    │
│   │ exit_value  = shares × exit_price                                   │    │
│   │ costs = total_round_trip_costs(entry_value, exit_value, ADV_value) │    │
│   │   • STT: 0.025% of sell value                                       │    │
│   │   • Brokerage: min(₹20, 0.03% of trade value) per side             │    │
│   │   • Impact: k·√(position/ADV) with 0.5% flat fallback               │    │
│   │ pnl_net = pnl_gross − costs.total                                   │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                  ▼                                          │
│   ┌─── (7) TRADE RECORD ──────────────────────────────────────────────┐    │
│   │ trades.append({entry_date, exit_date, symbol, entry_price,         │    │
│   │                 exit_price, n_hold_days, pnl_gross, costs,         │    │
│   │                 pnl_net, exit_reason})                             │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│   ┌─── (8) RESULT AGGREGATION ────────────────────────────────────────┐    │
│   │ _compute_summary(trades_df, config) →                              │    │
│   │   total_trades, win_rate, avg_return, max_drawdown,                │    │
│   │   peak_concurrent_capital, total_pnl_net                           │    │
│   │ _max_concurrent_positions: vectorised event sweep (exit=-1, entry=+1)│    │
│   │ _max_drawdown_from_cumsum: peak − trough of cumulative P&L         │    │
│   └────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key invariants**

- **Leak-free**: discontinuity blackout prevents buying into split/bonus artefacts; `_trading_days` restricts iteration to real market sessions.
- **Concurrent positions**: a new position opens every day regardless of existing open positions; the engine does **not** enforce capacity limits — peak concurrent capital scales linearly with overlap.
- **Forward-window cost**: trailing and rule exits pre-load up to 200 trading days per trade for evaluation; fixed exits use `fixed_hold_days + 5` for safety.
- **Delivery-aware signals**: signals with `requires_delivery=True` skip pre-`2019-10-01` dates automatically.

See `README.md` → *Backtest Engine* for usage examples (signal registration, custom signals, configuration).
