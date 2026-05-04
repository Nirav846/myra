# MYRA Frontend DB Contract

## Architecture
- Python backend (MYRA) writes to SQLite DBs at: myra_app/db/
- React frontend (localhost:3000) is READ-ONLY — it never writes to any DB
- All frontend DB access goes through the API server (localhost:8000)
- Frontend must never import Python code or access SQLite files directly

## API server base URL
Development: http://localhost:8000
All endpoints return JSON. All are GET requests (read-only).

## Database files (for API server reference only)
All files in myra_app/db/:
  myra_metadata.db     — symbols, sectors, index membership
  myra_technical.db    — OHLCV + delivery price history (2.4M rows)
  myra_valuation.db    — fundamentals (PAUSED — do not query)
  myra_institutional.db — insider trades, large deals

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

### technical_data (myra_technical.db)
  symbol TEXT, date TEXT (YYYY-MM-DD)
  open, high, low, close REAL
  volume INTEGER
  delivery_pct REAL         -- delivery as % of volume (0–100)
  vwap REAL
  delivery_divergence_score REAL
  volatility_compression_score REAL
  relative_volume_score REAL
  nifty_outperformance_score REAL
  PRIMARY KEY (symbol, date)

## Standard cross-reference query (use this for symbol metadata xref)
Run once on app load against myra_metadata.db, cache in React Context:

  SELECT
    m.symbol,
    COALESCE(m.sector, 'Uncharted Sector') AS sector,
    m.industry,
    m.in_nifty500,
    m.confidence,
    GROUP_CONCAT(i.index_name) AS indices
  FROM symbols_master m
  LEFT JOIN index_constituents i ON m.symbol = i.symbol
  WHERE m.is_active = 1
  GROUP BY m.symbol

## Market cap / universe bucketing rules (implement in frontend)
Given a row from the xref query:
  - indices contains "NIFTY 50"       → bucket: "Large Cap (N50)"
  - indices contains "NIFTY NEXT 50"  → bucket: "Large Cap (N100)"
  - in_nifty500 = 1                   → bucket: "Broader Market (N500)"
  - in_nifty500 = 0, indices is NULL  → bucket: "Deep Frontier"
  - sector = 'Uncharted Sector'       → add "Uncharted" badge (separate from bucket)

## Fundamentals — PAUSED
myra_valuation.db exists but fundamental ingestion is paused.
Do NOT query: pe, roe, graham_score, eps, book_value from fundamentals table.
Do NOT display PE ratio, ROE, Fundamental Score columns anywhere in the UI.
Replace with technical confidence scores only.

## What the frontend must never do
- Never write to any DB
- Never query myra_valuation.db for display data
- Never hardcode file paths to .db files — all DB access via API server
- Never display null sector as empty — always show "Uncharted Sector"
- Never drop a symbol from results because sector/index data is missing