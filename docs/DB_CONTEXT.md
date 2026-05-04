# MYRA Database Context — Read This Before Any DB Task

## The rule
All DB access goes through `LibrarianCore.DB_MAP`. Never hardcode a filename.
Never use `os.getcwd()` for paths — use `constants.py` (`DB_DIR`, `DATA_DIR`, `PROJECT_ROOT`).

## DB file locations
All files live in `myra_app/db/`. DB_MAP keys → filenames:

| Key          | File                      | Primary tables                                      |
|--------------|---------------------------|-----------------------------------------------------|
| "technical"  | myra_technical.db         | technical_data                                      |
| "meta"       | myra_metadata.db          | symbols_master, index_constituents, benchmarks, metadata, etf_blocklist |
| "valuation"  | myra_valuation.db         | fundamentals, quarterly_results                     |
| "institutional" | myra_institutional.db  | insider_trades, large_deals, fii_dii_history        |
| "governance" | myra_governance.db        | sast_disclosures, pledged_history, shareholding_history, ias_history |
| "scoring"    | myra_scoring.db           | ias_scores                                          |
| "calendar"   | myra_calendar.db          | market_calendar                                     |
| "cache"      | myra_cache_network.db     | network cache                                       |

## Where sector and index data lives
- Sector / industry per symbol → myra_metadata.db → symbols_master (columns: sector, industry, raw_sector, raw_industry, source, confidence, last_updated_sector, sector_locked)
- Index constituents (NIFTY 50, NIFTY 500 etc.) → myra_metadata.db → index_constituents (index_name TEXT, symbol TEXT)
- Benchmark OHLCV (^NSEI prices) → myra_metadata.db → benchmarks
- DO NOT query valuation.db for sector lookups — that is wrong. Use meta.db → symbols_master.

## symbols_master full schema
symbol TEXT PRIMARY KEY,
first_seen TEXT, last_seen TEXT,
in_active_universe INTEGER DEFAULT 0,
in_nifty500 INTEGER DEFAULT 0,
sector TEXT,          -- normalized sector name
industry TEXT,        -- normalized industry name
raw_sector TEXT,      -- original string from source
raw_industry TEXT,
source TEXT,          -- NSE_INDEX | MORNINGSTAR | SCREENER | YFINANCE
confidence REAL,      -- 1.0=official, 0.8=screener, 0.6=yfinance
last_updated_sector TEXT,
sector_locked INTEGER DEFAULT 0,  -- 1 = skip automated updates
is_active INTEGER DEFAULT 1,
instrument_type TEXT DEFAULT 'EQUITY',
last_fundamental_update TEXT

## technical_data full schema
symbol TEXT NOT NULL, date TEXT NOT NULL,
open REAL, high REAL, low REAL, close REAL,
volume INTEGER, delivery INTEGER, trades INTEGER, vwap REAL,
delivery_pct REAL, delivery_ratio REAL, delivery_qty REAL,
stock_return REAL, market_return REAL,
delivery_divergence_score REAL, volatility_compression_score REAL,
relative_volume_score REAL, nifty_outperformance_score REAL,
delivery_source TEXT,
PRIMARY KEY (symbol, date)

## Connections (from LibrarianCore)
self._tech_conn  → technical.db
self._meta_conn  → meta.db
self._val_conn   → valuation.db
self._inst_conn  → institutional.db
self._gov_conn   → governance.db

## How sector updates work
SectorManager (myra_app/sector_manager.py):
- Primary source: Morningstar bulk API (4000 symbols, confidence 1.0)
- Secondary: NiftyIndices.com CSV (official 4-tier classification, confidence 1.0)
- Fallback: screener.in per-symbol (0.8), yfinance (0.6)
- Writes to: myra_metadata.db → symbols_master
- Update trigger: incremental_sync() runs on every sync_market_data() call
- Targets: NULL sectors + last_updated_sector older than 90 days
- sector_locked=1 symbols are never overwritten

## Critical rules for Jules/AI agents
- Adding a column to technical_data → also add to TECHNICAL_EXPECTED_COLS in tools/db_doctor.py
- Adding a column to symbols_master → also add to META_EXPECTED_COLS in tools/db_doctor.py
- ALTER TABLE ADD COLUMN must use IF NOT EXISTS guard (match delivery_source pattern)
- No df.append() in loops → list + pd.concat
- No .strftime() on Pandas Series → .dt.strftime()
- CamelCase OHLCV in DataFrames (Open/High/Low/Close/Volume), lowercase in DB inserts
- WAL mode must stay on — never set journal_mode=DELETE