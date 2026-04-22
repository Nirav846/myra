# End-to-End Data Lineage & Storage Audit

This document is a comprehensive, system-level audit of the MYRA project's data architecture, detailing the end-to-end data lineage, storage mechanics, and processing logic. It verifies execution paths directly from the v3.2 Atomic Trilogy codebase.

---

## 1. DATABASE DISCOVERY & STRUCTURE

The MYRA system operates on a decentralized "Atomic Trilogy" architecture (v3.2) utilizing strictly defined SQLite sidecars.

### A. Database Maps

All databases reside under `db/` with strictly enforced naming.

#### 1. `myra_metadata.db` (System Brain)
*   **`symbols_master`**
    *   `symbol` (TEXT PRIMARY KEY)
    *   `first_seen`, `last_seen` (TEXT)
    *   `in_active_universe`, `in_nifty500` (INTEGER DEFAULT 0)
    *   `sector`, `industry`, `raw_sector`, `raw_industry` (TEXT)
    *   `source` (TEXT)
    *   `confidence` (REAL)
    *   `last_updated_sector` (TEXT)
    *   `sector_locked` (INTEGER DEFAULT 0)
    *   `is_active` (INTEGER DEFAULT 1)
    *   `instrument_type` (TEXT DEFAULT 'EQUITY')
    *   `last_fundamental_update` (TEXT)
    *   *Differentiation logic:* Stocks vs ETFs are differentiated explicitly by the `instrument_type` column (defaulting to 'EQUITY').
    *   *Indexes:* `idx_master_active`, `idx_master_sector`, `idx_master_industry`
*   **`index_constituents`**
    *   `index_name` (TEXT), `symbol` (TEXT), PRIMARY KEY (index_name, symbol)
*   **`benchmarks`**
    *   `symbol` (TEXT), `date` (TEXT), `close` (REAL), PRIMARY KEY (symbol, date)
*   **`metadata`**
    *   `key` (TEXT PRIMARY KEY), `value` (TEXT)

#### 2. `myra_technical.db` (Price History)
*   **`technical_data`**
    *   `symbol` (TEXT NOT NULL), `date` (TEXT NOT NULL), PRIMARY KEY (symbol, date)
    *   `open`, `high`, `low`, `close` (REAL)
    *   `volume`, `delivery`, `trades` (INTEGER)
    *   `vwap`, `delivery_pct`, `delivery_ratio` (REAL)
    *   *Indexes:* `idx_tech_date`, `idx_tech_symbol`
*   **`prices`** *(Legacy / High-Speed Cache)*
    *   `symbol` (VARCHAR), `date` (DATE), PRIMARY KEY (symbol, date, exchange)
    *   `open`, `high`, `low`, `close` (DOUBLE)
    *   `volume`, `delivery_qty` (BIGINT), `delivery_percent` (DOUBLE), `exchange` (VARCHAR)

#### 3. `myra_institutional.db` (Smart Money)
*   **`fii_dii_history`**
    *   `symbol` (TEXT), `date` (TEXT), PRIMARY KEY (symbol, date)
    *   `fii_pct`, `dii_pct`, `promoter_pct`, `pledged_pct`, `fii_change`, `dii_change`, `car_ratio` (REAL)
    *   `is_hidden_accumulation` (INTEGER DEFAULT 0)
*   **`institutional_owners`**
    *   `symbol`, `owner_name`, `date` (TEXT), PRIMARY KEY (symbol, owner_name, date)
    *   `owner_type` (TEXT), `shares_held` (INTEGER), `pct_held` (REAL)
*   **`large_deals`**
    *   `symbol`, `client`, `date` (TEXT), `qty` (INTEGER), `price` (REAL), PRIMARY KEY (symbol, client, date, qty, price)
    *   `type`, `buy_sell` (TEXT), `value_cr` (REAL)
    *   *Indexes:* `idx_deals_sym`

#### 4. `myra_valuation.db` (Fundamentals)
*   **`fundamentals`**
    *   `symbol` (TEXT PRIMARY KEY)
    *   `pe`, `roe`, `eps`, `book_value`, `market_cap` (REAL)
    *   `sector`, `last_updated` (TEXT)
*   **`quarterly_results`**
    *   `symbol`, `report_date` (TEXT), PRIMARY KEY (symbol, report_date)
    *   `period_end` (TEXT), `revenue`, `net_profit`, `eps`, `opm_pct` (REAL)

#### 5. `myra_governance.db` (Pledge & SAST)
*   **`sast_disclosures`**
    *   `disclosure_id` (TEXT PRIMARY KEY), `symbol`, `date`, `acq_name`, `type` (TEXT), `qty_pct` (REAL)
*   **`pledged_history`**
    *   `symbol`, `date` (TEXT), PRIMARY KEY (symbol, date)
    *   `promoter_holding`, `pledged_pct`, `change_qoq` (REAL)
*   **`shareholding_history`**
    *   `symbol`, `date` (TEXT), PRIMARY KEY (symbol, date)
    *   `fii_pct`, `dii_pct`, `promoter_pct` (REAL)
*   **`ias_history`**
    *   `symbol`, `date` (TEXT), PRIMARY KEY (symbol, date)
    *   `ias_score`, `ias_rank` (REAL), `tags` (TEXT)

#### 6. `myra_cache_network.db` (GhostSession Cache)
*   **`cache`**
    *   `key` (TEXT PRIMARY KEY), `value` (BLOB), `expiry` (REAL), `params` (TEXT)

#### 7. `myra_calendar.db` (Holidays)
*   **`market_calendar`**
    *   `date` (TEXT PRIMARY KEY), `is_trading_day` (INTEGER NOT NULL), `holiday_name` (TEXT)

### Differentiations
- **Stocks vs ETFs:** Differentiated via the `instrument_type` column (default 'EQUITY') in the `symbols_master` table.
- **Naming Conventions:** Core OHLCV uses standard lowercase field names in `technical_data`. Legacy systems map to `TitleCase` or `camelCase`. `Bhavcopy` fields use ALL CAPS (e.g., `SERIES`, `SYMBOL`, `DATE1`).

---

## 2. DATA ORIGIN (INGESTION LAYER)

The system enforces centralized network fetching via `myra_app/fetcher.py` using `GhostSession` (a wrapper around `scrapling`).

| Raw Source | Fetch Function (`fetcher.py`) | Transformation / Target | DB Insert |
| :--- | :--- | :--- | :--- |
| **NSE Bhavcopy** (CSV) | `fetch_bhavcopy_with_retry` / `fetch_ohlcv_delivery` | Extracts Equity (`EQ`), normalizes dates to `%Y-%m-%d`. Combines Bhavcopy and MTO delivery metrics. Vectorized. | `technical_data` (`INSERT OR REPLACE`) |
| **NSE APIs** (JSON) | `fetch_large_deals_v2`, `fetch_sast_disclosures`, `fetch_pledged_info`, `fetch_fii_dii_activity`, `fetch_shareholding_pattern`, `fetch_corporate_announcements`, `fetch_index_constituents` | Parses nested JSON arrays to dictionaries. Sanitizes floats. Unifies symbols. | Respective tables in `institutional`, `governance`, `meta` |
| **Screener.in / Google Finance** | `fetch_fundamentals`, `fetch_deep_history` | Web scraping via BeautifulSoup. Parses HTML tables for P&L data and fundamentals. | `fundamentals`, `quarterly_results` |
| **Bhavcopy CSVs (Local Archives)** | `backfill_technical.py`, `mass_backfill.py` | Parses mixed local file formats (`nse_full_YYYY-MM-DD.csv`, `nse_full_DDMMYYYY.csv`). Ensures `delivery_qty` matching. | `technical_data` |

*Pipeline: RAW SOURCE → `GhostSession.get` (stealth/cache) → Extraction/Pandas Transform → `LibrarianCore.safe_execute` → `INSERT OR REPLACE`.*

---

## 3. DATA STORAGE LOGIC

- **Upserts via `INSERT OR REPLACE`**: To preserve data integrity during interrupted pipelines, the standard ingestion pattern uses `INSERT OR REPLACE` (e.g., in `daily_ingestor.py`, `librarian_sync.py`, `mass_backfill.py`, `fundamental_manager.py`). The `technical_data` table uses `(symbol, date)` as the composite primary key. `pandas.to_sql(if_exists='append')` is banned.
- **Granularity**: Daily (`EOD`).
- **Sector/Industry Mapping**: 
    - Stored statically in `symbols_master` (`sector`, `industry`, `raw_sector`, `raw_industry`) and denormalized into `fundamentals` (`sector`). 
    - Sectors are dynamically assigned via `fetch_nse_master` or `fetch_morningstar_bulk` in `sector_manager.py` but can be locked (`sector_locked`).

---

## 4. CALCULATIONS & INDICATORS

- **Computation vs Storage**: Technical indicators (RSI, SMA, FVG, EMA, Bollinger Bands, CHoCH, BOS) are **computed in batch** and stored as serialized Parquet files (Parquet Lake), *not* stored directly in SQLite tables.
- **Execution**: Managed via `myra_app/librarian_intelligence.py` -> `precompute_indicators(self, as_of_date)`.
- **Trigger**: Run automatically during market scans or updated via `update_indicator_history()` which writes to the Parquet Lake.
- **Dependencies**: Relies entirely on `technical_data` price history, strictly read and sorted by `date`. Requires Polars or Vectorized Pandas execution.

---

## 5. HOLIDAYS & TRADING CALENDAR

- **Definition**: Managed by `myra_app/calendar_generator.py`. Generates a multi-year SQLite database `myra_calendar.db`.
- **Logic**: Combines hardcoded NSE fixed-date holidays (e.g., `01-26` Republic Day) with logic to exclude weekends (Saturday/Sunday).
- **Validation**: Scripts like `daily_ingestor.py` do a pre-flight check querying `market_calendar` (`SELECT is_trading_day...`). If `0`, it skips fetching. Time-series data implicitly handles gaps by relying on pandas `.rolling()` against rows rather than continuous dates.

---

## 6. DATA FETCHING (READ PATH)

- **Entry**: Screeners (`myra_app/screener.py`) trigger data loads via `Librarian.precompute_indicators()` and `pandas.read_sql()`.
- **Query Logic**: 
    - Avoids `.execute().df()` (DuckDB legacy).
    - Uses `safe_execute(query, conn=self._<db>_conn)`.
- **Flow**: `myra_technical.db` + `Parquet` → `pd.concat` (Indicators) → Join `myra_valuation.db` (Fundamentals for Sector) → Output to Screeners.
- **Example (`run_market_xray`)**:
  ```python
  df = self.lib.precompute_indicators()
  funda = pd.read_sql("SELECT symbol, sector FROM fundamentals", self.lib._val_conn)
  df = df.merge(funda, on="symbol", how="left")
  ```

---

## 7. SCANNERS / SCREENERS LOGIC

- **Input**: Strategy classes (e.g., `multibagger_early_detection.py`) expect a Pandas DataFrame `df` containing technical history and indicators, plus a `funda` dictionary for fundamental overlays.
- **Constraints**: Strategies expect strict OHLCV TitleCase columns (`Open`, `High`, etc.) while indicators remain `lowercase`/`snake_case`.
- **Missing Data Handling**: Graceful degradation. If `df` is `.empty` or `< min_length`, strategies return `{"signal": False}` early. Try-except blocks wrap calculations. E.g., handling missing `rs_rating`: "Safely catch APIs that return N/A or -".

---

## 8. EDGE CASES & FAILURE HANDLING

- **Missing Data**: Handled aggressively. `fetch_bhavcopy_with_retry` employs 5 retries with a 20s gap. 
- **Corrupt/Incomplete rows**: Filtered prior to database writes. Re-indexing guarantees uniqueness `df[~df.index.duplicated(keep='last')]`. 
- **Corrupt Bhavcopy**: Validated using `validate_against_anchor(df)`. It queries a stable anchor (e.g., `RELIANCE`). If the closing price deviates outside expected bounds, or the universe standard deviation is 0, it skips ingestion.
- **Thread Safety**: Multithreaded DB writes enforce `with self._db_lock:`.

---

## 9. DATA PIPELINE TIMING

- **Scheduled Routines**: Data pipelines (`daily_ingestor.py`) verify the time is after **18:30 IST**. Bhavcopy data is not finalized by the NSE until this time.
- **Caching**: Successful synchronizations trigger metadata cache updates (`data_sync_manifest.json`) via `set_metadata` in `LibrarianCore`.

---

## 10. RISKS / GAPS & RECOMMENDATIONS

### D. RISKS / GAPS
- **Redundant Storage:** `prices` table in `myra_technical.db` mirrors `technical_data`. This is a legacy artifact.
- **Dead Code:** DuckDB implementations still leave traces in code semantics despite being banned.
- **Indicator Sprawl:** Parquet storage works, but mapping its lifecycle back to SQLite `technical_data` updates can become desynced without atomic commits across formats.
- **Schema Drift:** `technical_data` contains algorithm columns like `vwap` that are sometimes not explicitly mapped during raw CSV insertion.

### E. RECOMMENDATIONS
- **Deprecate `prices`:** Formally remove the `prices` legacy table.
- **Consolidated INGESTION Pipelines:** Merge backfill and daily ingest CSV parsing to use the exact same strict schema checks.
- **Parquet Sync Checks:** Implement a checksum system between the last `date` in SQLite vs the last row in Parquet precomputed files to auto-invalidate stale indicators.
