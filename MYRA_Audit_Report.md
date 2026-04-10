# MYRA Deep-Dive Technical Audit Report

## 1. Data Ingestion & API Resiliency
**Severity:** High
**Location:** `myra_app/fetcher.py` -> `DataFetcher._merge_zip_mto()`
**The Vulnerability:**
The function directly attempts to extract the zip file using `zipfile.ZipFile(io.BytesIO(zip_content))`. If the NSE WAF blocks the request and returns an HTML page (with a 200 OK status, which is a common defense mechanism on NSE), or if the file is corrupted during download, `zipfile.BadZipFile` is raised. This exception is not caught within the method and will propagate up, crashing the entire daily ingestion batch job instead of gracefully skipping, logging, or retrying the specific fetch.
**Actionable Fix:**
Implement a `try/except` block specifically for Zip decompression and validate the MIME type before parsing.
```python
# Python Actionable Fix
try:
    with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
        df_bhav = pd.read_csv(z.open(z.namelist()[0]))
except zipfile.BadZipFile:
    logger.error("Failed to unzip Bhavcopy. Likely an HTML WAF block or corrupted download.")
    return None
```

## 2. Data Merging & Integrity Risks
**Severity:** Medium
**Location:** `myra_app/fundamental_ranker.py` -> `_calculate_all_scores_from_duck()`
**The Vulnerability:**
The DuckDB query uses an exact match join `JOIN latest_snapshot l ON a.symbol = l.symbol`. When corporate actions result in symbol changes (e.g., stock splits, ticker name changes), the historical fundamental data recorded under the old symbol will not match the new symbol's `latest_snapshot`. This breaks the temporal alignment, leading to orphaned historical data, incomplete records, and skipped equities during the daily run.
**Actionable Fix:**
Introduce a `symbol_mapping` lookup table or alias map to resolve ticker changes dynamically during the DuckDB query.
```sql
-- DuckDB Actionable Fix
WITH mapped_base AS (
    SELECT COALESCE(sm.new_symbol, f.symbol) AS symbol, f.*
    FROM fundamentals_quarterly f
    LEFT JOIN symbol_master_changes sm ON f.symbol = sm.old_symbol
)
-- Use mapped_base for aggregations and joins to maintain unbroken lineage
```

## 3. Performance & Memory Management
**Severity:** High
**Location:** `myra_app/positional_engine.py` -> `PositionalScorer.rank()`
**The Vulnerability:**
Although the code avoids the banned `.iterrows()` by using `.itertuples(index=False)`, it still iterates over the entire `results_df` dataframe to build dictionaries via `row._asdict()` and sequentially calls a scalar `compute_score()` function for thousands of equities. This hidden Python loop violates the project's strict vectorization rule and causes a massive bottleneck during batch processing, as Python function call overhead scales linearly O(N).
**Actionable Fix:**
Translate the `compute_score` logic into a fully vectorized Polars or DuckDB pipeline.
```python
# Polars Actionable Fix
import polars as pl

def rank_vectorized(df: pl.DataFrame, regime: float) -> pl.DataFrame:
    # Vectorized score computation without any Python loops
    return df.with_columns(
        MYRA_Score_v25=(
            (pl.col("trend_score") * 0.25 + pl.col("stability_score") * 0.15 +
             pl.col("delivery_score") * 0.20 + pl.col("liquidity_score") * 0.10 +
             pl.col("base_score") * 0.10) * 0.7 + (pl.col("fundamental_score") * 0.3)
        ) * regime
    ).with_columns(
        # Vectorized drawdown penalty filter
        MYRA_Score_v25=pl.when(pl.col("drawdown") > 0.4).then(pl.col("MYRA_Score_v25") * 0.8)
        .when(pl.col("drawdown") > 0.2).then(pl.col("MYRA_Score_v25") * 0.95)
        .otherwise(pl.col("MYRA_Score_v25")).round(1)
    ).sort("MYRA_Score_v25", descending=True)
```

## 4. Error Handling & State Logging
**Severity:** Medium
**Location:** `myra_app/fetcher.py` -> `GhostSession._set_cache()`
**The Vulnerability:**
The caching logic wraps the SQLite insertion in a broad `try...except Exception as e: pass`. In a high-concurrency batch ingestion setting, if the SQLite DB is locked or out of disk space, it will silently fail without persisting the cache. This forces the fetcher to make redundant network API calls on subsequent requests, eventually triggering rate limits or bans from Morningstar and NSE endpoints, without any alerts in the state logs.
**Actionable Fix:**
Implement exponential backoff for DB locking and actively log or raise exceptions for unrecoverable errors.
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
            time.sleep(2 ** attempt)  # Exponential backoff
        else:
            logger.error(f"Unrecoverable DB Cache Error: {e}")
            break
```

## 5. Technical Debt & Architecture
**Severity:** High
**Location:** `myra_app/data_adapter.py` -> `DataAdapter.compute_common_indicators()`
**The Vulnerability:**
The adapter uses `pandas_ta` to dynamically calculate indicators (SMA, RSI, ATR) on the fly during data fetching inside the `get_price_df` pipeline. This directly violates the project's core rule: *"Precompute > Recompute: Cache indicators in the Parquet Lake."* It introduces severe technical debt by redundantly recalculating the same metrics across multiple scans or symbols on each run, ballooning memory usage and destroying scaling efficiency.
**Actionable Fix:**
Strip dynamic calculations from the data adapter and strict-route requests to query precomputed indicators via DuckDB from the Parquet lake.
```python
# DuckDB Actionable Fix (Parquet Lake Route)
def get_indicators(symbol):
    query = f"""
    SELECT date, RSI, sma20, sma50, sma150, sma200, atr20
    FROM read_parquet('data/indicators/{symbol}.parquet')
    """
    return duckdb.execute(query).df()
```

## 6. Scanner Logic & Screening Accuracy
**Severity:** Critical
**Location:** `myra_app/fundamental_ranker.py` -> DuckDB Query inside `_calculate_all_scores_from_duck()`
**The Vulnerability:**
The DuckDB SQL used to calculate quarterly profit growth contains the following condition: `AVG(CASE WHEN prev_revenue > 0 THEN (revenue - prev_revenue)/prev_revenue ELSE 0 END)`. This effectively assigns a flat `0%` growth score to any company recovering from a negative revenue or net profit state. It completely filters out valid turnaround stocks (companies shifting from massive losses to positive earnings). Additionally, if `prev_revenue` is tiny, it produces highly distorted, massive percentage spikes.
**Actionable Fix:**
Deploy a mathematically sound calculation that accounts for negative baseline turnarounds and caps outliers.
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