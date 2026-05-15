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
         ├─► /api/query (SQL queries)
         ├─► /api/tools/status (DB health)
         ├─► /api/market-breadth (breadth metrics)
         └─► /api/scanner/* (scanner endpoints)

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

### myra_meta.db
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
- Sidecar architecture (technical, valuation, institutional, meta) prevents schema contention
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
