# MYRA Database Context — Read This Before Any DB Task

## The rule
All DB access goes through `LibrarianCore.DB_MAP`. Never hardcode a filename.
Never use `os.getcwd()` for paths — use `constants.py` (`DB_DIR`, `DATA_DIR`, `PROJECT_ROOT`).

## DB file locations
All files live in `myra_app/db/`. DB_MAP keys → filenames (9 keys):

| Key          | File                      | Primary tables                                      |
|--------------|---------------------------|-----------------------------------------------------|
| "technical"  | myra_technical.db         | technical_data, launchpad_events, launchpad_features |
| "meta"       | myra_metadata.db          | symbols_master, index_constituents, benchmarks, metadata, etf_blocklist, task_registry, lineage_tracking, etf_sync_log, sync_log |
| "valuation"  | myra_valuation.db         | fundamentals, quarterly_results, fund_traction, fund_cross_buy, full_fundamental_cache |
| "institutional" | myra_institutional.db  | insider_trades, large_deals, bulk_deals, block_deals, fii_dii_daily |
| "governance" | myra_governance.db        | sast_disclosures, pledged_history, shareholding_history, ias_history |
| "scoring"    | myra_scoring.db           | ias_scores, fundamental grades |
| "calendar"   | myra_calendar.db          | market_calendar |
| "cache"      | myra_cache_network.db     | network cache |
| "options"    | myra_options.db           | option_chain, pcr_snapshot |

> `myra_news.db` and `myra_portfolio.db` also exist but are **not** in `DB_MAP` (portfolio is gitignored).

## Where sector and index data lives
- Sector / industry per symbol → myra_metadata.db → symbols_master (columns: sector, industry, raw_sector, raw_industry, source, confidence, last_updated_sector, sector_locked)
- Index constituents (NIFTY 50, NIFTY 500 etc.) → myra_metadata.db → index_constituents (index_name TEXT, symbol TEXT)
- Benchmark OHLCV (^NSEI prices) → myra_metadata.db → benchmarks
- **DO NOT query valuation.db for sector lookups** — that is wrong. Use meta.db → symbols_master.

## fund_traction / fund_cross_buy (myra_valuation.db)
- `fund_traction` — PK `(symbol, month)`; columns: symbol, month, traction_score, number_of_funds, adds_new, reduces_closes, sma_30, month_end_close, close_latest, pct_vs_sma. Plus a `sync_metadata` table.
- `fund_cross_buy` — PK `(symbol, month)`; columns: symbol, month, total_funds, large_funds, mid_funds, small_funds, multi_funds, other_funds, cross_buy_ratio, signal_tag, last_updated.
- Written by `myra_app/fund_traction_sync.py` and `myra_app/cross_buy_processor.py`.
- **Do not modify these tables directly** — re-sync via the pipeline tasks (`fund-traction-sync`, `cross-buy-sync`).

## symbols_master full schema
symbol TEXT PRIMARY KEY,
first_seen TEXT, last_seen TEXT,
in_active_universe INTEGER DEFAULT 0,
in_nifty500 INTEGER DEFAULT 0,
sector TEXT,          -- normalized sector name
industry TEXT,        -- normalized industry name
raw_sector TEXT,      -- original string from source
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
delivery_source TEXT,          -- e.g. "eod2_adjusted"
sma_50 REAL, high_52w REAL, low_52w REAL, delivery_ma_60 REAL,
FVG / swing / liquidity / trend / enrichment columns (added via enrichment pipeline),
PRIMARY KEY (symbol, date)

> The live table has more columns than the SchemaRegistry definition. EOD2 sync (`eod2_sync.py`) maps BhavDesk headers (`Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY`) and writes 16 columns via `_INSERT_COLS`.

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

## Critical rules for agent/CI tasks
- Adding a column to technical_data → also add to TECHNICAL_EXPECTED_COLS in tools/db_doctor.py
- Adding a column to symbols_master → also add to META_EXPECTED_COLS in tools/db_doctor.py
- ALTER TABLE ADD COLUMN must use IF NOT EXISTS guard (match delivery_source pattern)
- No df.append() in loops → list + pd.concat
- No .strftime() on Pandas Series → .dt.strftime()
- CamelCase OHLCV in DataFrames (Open/High/Low/Close/Volume), lowercase in DB inserts
- WAL mode must stay on — never set journal_mode=DELETE
- Prefer `myra_app/db/bulk_loader.load_ohlcv_for_universe` for scanner data — never per-symbol SQLite connects
