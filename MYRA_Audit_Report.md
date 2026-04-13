# MYRA Deep-Dive Technical Audit Report

## Part 1: Feature Verification Report (User Request)

Based on a thorough review of the current MYRA codebase, the following requested features have been verified:

### 1. ETF and ISIN Exclusion Logics
*   **Status: Implemented & Active**
*   **Details:**
    *   **ISIN Mapping:** The `isin_mapper.py` utility successfully bridges historical ISINs to symbols and creates an `isin_bridge.parquet` file, which is actively utilized in `fundamental_ranker.py` to join datasets securely.
    *   **ETF Exclusion:** The system features a robust, multi-layered ETF filter. `librarian_ingestor.py` actively screens out known non-equity and ETF symbols during the primary SQLite ingestion using `meta.db`. Furthermore, `gatekeeper.py` features a dedicated "smart gatekeeper" that reads `MW-ETF-*.csv` files, purges ETF rows from `technical_data`, clears them from the `ias_history`, and marks them as `ETF` and `is_active = 0` in `symbols_master`.

### 2. Market Timing and Holiday Awareness
*   **Status: Implemented & Active**
*   **Details:**
    *   **Market Timing:** The `DataFetcher.is_data_ready(dt)` function in `fetcher.py` actively prevents premature fetching. It correctly assumes that the current day's NSE data is only ready after 6:00 PM IST (`now.hour >= 18`).
    *   **Holiday Awareness:** The `DataFetcher._is_holiday(dt)` function successfully detects weekends (Saturday/Sunday), reads from a whitelist in `trading_calendar_master.csv`, and includes a fallback hardcoded list of known NSE holidays (`NSE_HOLIDAYS`), preventing the system from incorrectly assuming missing files for closed days.


---

## Part 2: Deep-Dive Technical Audit

### 1. Data Ingestion & API Resiliency
**Severity:** High
**Location:** `myra_app/fetcher.py` -> `DataFetcher._merge_zip_mto()` and `daily_ingestor.py`
**The Vulnerability:**
When fetching the ZIP Bhavcopy directly from NSE, the system passes the response content directly to `zipfile.ZipFile(io.BytesIO(r.content))`. If the NSE WAF blocks the request (often returning a 200 OK with an HTML captcha or block page) or if the zip file is truncated, this throws a `zipfile.BadZipFile` exception. In `daily_ingestor.py` and `fetcher.py`, this error propagates and fails the entire daily batch process rather than gracefully falling back or identifying the WAF block.
**Actionable Fix:**
Wrap the zip extraction in a `try/except` block specifically checking for `BadZipFile` and `HTTP` response headers for content-type.
```python
# Python Actionable Fix
try:
    with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
        df_bhav = pd.read_csv(z.open(z.namelist()[0]))
except zipfile.BadZipFile:
    logger.error("Failed to unzip Bhavcopy. Likely an HTML WAF block or corrupted download.")
    return None
```

### 2. Data Merging & Integrity Risks
**Severity:** Medium
**Location:** `myra_app/fundamental_ranker.py` -> DuckDB Query inside `_calculate_all_scores_from_duck()`
**The Vulnerability:**
The query joins recent data with historical snapshots using `JOIN latest_snapshot l ON a.symbol = l.symbol`. Corporate actions (stock splits, symbol name changes) will break this exact string matching over time. When a ticker changes, the historical fundamental data under the old ticker will be orphaned, and the system will silently drop or restart the equity's ranking history.
**Actionable Fix:**
Use the `isin_bridge.parquet` as the absolute primary key for temporal joins instead of relying solely on the ticker symbol.
```sql
-- DuckDB Actionable Fix
-- Map everything to ISIN first, then aggregate over time to ensure continuous lineage regardless of ticker changes.
WITH mapped_base AS (
    SELECT COALESCE(b.ISIN, f.symbol) AS uid, f.*
    FROM fundamentals_quarterly f
    LEFT JOIN read_parquet('data/isin_bridge.parquet') b ON f.symbol = b.SYMBOL
)
```

### 3. Error Handling & State Logging
**Severity:** Medium
**Location:** `myra_app/fetcher.py` -> `GhostSession._set_cache()`
**The Vulnerability:**
The SQLite WAL cache persistence wraps its `INSERT OR REPLACE` query inside a broad `try... except Exception as e: pass` or `logger.error` block without retrying. If the database is locked due to concurrent thread access (`sqlite3.OperationalError: database is locked`), the cache silently fails to persist. This forces the engine to redundantly fetch the same URL on the next scan, severely increasing the likelihood of an NSE or Morningstar IP ban.
**Actionable Fix:**
Implement exponential backoff specifically for SQLite lock errors.
```python
# Actionable Fix
import time
for attempt in range(3):
    try:
        conn = sqlite3.connect(self.cache_path, timeout=20)
        # Execute insert...
        conn.commit()
        break
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            time.sleep(1 + attempt)  # Exponential backoff
        else:
            logger.error(f"Unrecoverable DB Cache Error: {e}")
            break
```

### 4. Technical Debt & Architecture
**Severity:** High
**Location:** `myra_app/data_adapter.py` -> `DataAdapter.compute_common_indicators()`
**The Vulnerability:**
Despite the README's declaration of an "Atomic Trilogy" and "Parquet Indicator Lake," `DataAdapter` uses `pandas_ta` to compute moving averages, RSI, and ATR *dynamically on-the-fly* for the fetched dataframe (`df.ta.study()`). This redundant recalculation introduces massive technical debt, wastes CPU cycles, creates memory spikes when querying thousands of symbols, and violates the architectural rule to read precomputed indicators from the Parquet lake.
**Actionable Fix:**
Refactor the Adapter to strictly fetch indicators from `myra_app/librarian_core.py`'s Parquet loader and remove dynamic `pandas_ta` computations from the read-path.
```python
# Polars/DuckDB Actionable Fix (Parquet Lake Route)
def get_indicators(symbol):
    query = f"""
    SELECT date, RSI, sma20, sma50, sma150, sma200, atr20
    FROM read_parquet('data/indicators/{symbol}.parquet')
    """
    return duckdb.execute(query).df()
```

### 5. Performance & Memory Management
**Severity:** High
**Location:** `myra_app/strategy_engine.py` -> `run_strategy()`
**The Vulnerability:**
The strategy engine pulls a 10-day window for *all symbols* into memory via `pl.read_database` to calculate a single day's relative volume score and delivery divergence. While Polars is fast, pulling 10 days of raw technical data for 4,000+ equities into RAM just to filter down to the `latest_date` is highly inefficient and risks memory exhaustion on constrained hardware (like the specified AMD APU).
**Actionable Fix:**
Leverage SQLite Window Functions in the SQL layer *before* it hits Polars, pushing the computation down to the database and retrieving only the final row per symbol.
```sql
-- SQLite Actionable Fix
SELECT symbol, date, close, high, low, delivery, delivery_pct,
       AVG(delivery) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) as avg_10d_delivery,
       delivery_pct - LAG(delivery_pct) OVER (PARTITION BY symbol ORDER BY date) as delivery_divergence
FROM technical_data
WHERE date IN (SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT 10)
```

### 6. Scanner Logic & Screening Accuracy
**Severity:** Critical
**Location:** `myra_app/fundamental_manager.py` -> `fetch_fundamentals()` (Growth Metrics)
**The Vulnerability:**
The logic calculating `sales_growth` and `profit_growth` uses a basic division: `((l_profit - p_profit) / abs(p_profit)) * 100`. If a company is a turnaround stock (e.g., recovering from a massive loss of -100 to a profit of 10), the absolute denominator results in highly distorted, massive percentage spikes. Alternatively, if `prev` metrics are zero, the system skips it (`p_profit != 0`). This causes edge cases to produce infinite/NaN returns or silently omit highly lucrative turnaround companies.
**Actionable Fix:**
Implement a mathematically sound bounds-capped calculation for turnaround growth using vectorized logic in Polars/DuckDB.
```sql
-- DuckDB Actionable Fix
AVG(
    CASE
        WHEN prev_profit < 0 AND net_profit > 0 THEN 100.0 -- Hard cap for full turnaround
        WHEN prev_profit < 0 AND net_profit < 0 THEN ((net_profit - prev_profit) / ABS(prev_profit)) * 100
        WHEN prev_profit > 0 THEN ((net_profit - prev_profit) / prev_profit) * 100
        ELSE 0
    END
) as profit_growth
```