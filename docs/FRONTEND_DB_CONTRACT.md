# MYRA Frontend DB Contract

## Architecture
- Python backend (MYRA) writes to SQLite DBs at: `myra_app/db/`
- React frontend (localhost:3000) is READ-ONLY — it never writes to any DB
- All frontend DB access goes through the API server (localhost:8000)
- Frontend must never import Python code or access SQLite files directly

## API server base URL
Development: `http://localhost:8000`
All endpoints return JSON. Most are GET (read-only); **scanner scans are POST** (`POST /api/{name}/scan`).

## Database files (for API server reference only)
All files in `myra_app/db/` (9 databases via `LibrarianCore.DB_MAP`):

  myra_metadata.db     — symbols, sectors, index membership, task registry, lineage
  myra_technical.db    — OHLCV + delivery + SMC enrichment price history (~2.2M rows)
  myra_valuation.db    — fundamentals, fund_traction, fund_cross_buy, full_fundamental_cache
  myra_institutional.db— insider trades, large/bulk/block deals, FII/DII flows
  myra_governance.db   — compliance, SAT disclosures, pledge history
  myra_scoring.db      — pre-materialized scores (IAS, fundamental grades)
  myra_calendar.db     — market trading calendar
  myra_cache_network.db— HTTP response cache
  myra_options.db      — option-chain + PCR snapshots (market regime)

## Key tables the frontend uses

### symbols_master (myra_metadata.db)
The primary lookup table for all symbol metadata.

  symbol TEXT PRIMARY KEY
  sector TEXT              -- normalized e.g. "Information Technology"
                           -- NULL means unclassified → display as "Uncharted Sector"
  industry TEXT
  in_nifty500 INTEGER      -- 1 = in NIFTY 500, 0 = not
  in_active_universe INTEGER  -- 1 = currently trading
  source TEXT              -- how sector was determined: NSE_INDEX | MORNINGSTAR | SCREENER | YFINANCE
  confidence REAL          -- 1.0 = official NSE source, 0.8 = screener, 0.6 = yfinance
  last_updated_sector TEXT -- ISO datetime of last sector refresh
  is_active INTEGER        -- 1 = active symbol

### index_constituents (myra_metadata.db)
  index_name TEXT    -- e.g. "NIFTY 50", "NIFTY 500", "NIFTY NEXT 50"
  symbol TEXT
  PRIMARY KEY (index_name, symbol)

### fundamentals (myra_valuation.db) — LIVE
Fundamental data is **live** (no longer paused). Query via the API, not directly:
  - `GET /api/fundamentals/live/{symbol}`
  - `GET /api/full-fundamentals/{symbol}` — deep dive (Graham, Piotroski F-Score, DCF)
  - `GET /api/fund-traction/*` and `GET /api/cross-buy/*` — MF holder traction + cross-buy
PE, ROE, profit margins, market cap, dividend yield, etc. are available and may be displayed in the UI.

### technical_data (myra_technical.db)
  symbol TEXT, date TEXT (YYYY-MM-DD)
  open, high, low, close REAL
  volume INTEGER
  vwap REAL
  delivery, trades INTEGER
  delivery_pct REAL, delivery_ratio REAL, delivery_qty REAL
  delivery_source TEXT
  stock_return, market_return REAL
  delivery_divergence_score, volatility_compression_score,
  relative_volume_score, nifty_outperformance_score REAL
  (plus enrichment columns: sma_50, high_52w, low_52w, delivery_ma_60, FVG/swing/liquidity/trend columns)
  PRIMARY KEY (symbol, date)

## Market cap / universe bucketing rules (implement in frontend)
Given a row from the xref query:
  - indices contains "NIFTY 50"       → bucket: "Large Cap (N50)"
  - indices contains "NIFTY NEXT 50"  → bucket: "Large Cap (N100)"
  - in_nifty500 = 1                   → bucket: "Broader Market (N500)"
  - in_nifty500 = 0, indices is NULL  → bucket: "Deep Frontier"
  - sector = 'Uncharted Sector'       → add "Uncharted" badge (separate from bucket)

## What the frontend must never do
- Never write to any DB
- Never hardcode file paths to .db files — all DB access via API server
- Sector lookups always come from `meta.db → symbols_master` — never `valuation.db`
- Never display null sector as empty — always show "Uncharted Sector"
- Never drop a symbol from results because sector/index data is missing
- Scanner runs are POST requests; do not assume all endpoints are GET
