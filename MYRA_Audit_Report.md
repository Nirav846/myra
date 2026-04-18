# MYRA System Architecture & Database Audit Report

## 1. Executive Summary (how system actually works at runtime)
MYRA operates as a decoupled, multi-stage data processing pipeline ("v3.2 GHOST / Atomic Trilogy").
At runtime, execution begins via CLI commands (e.g., `myra_app/myra.py` or `myra_app/cli.py`) or scheduled cron/batch jobs (e.g., `myra_app/daily_ingestor.py` triggering `run_daily_update()`).
Network requests are routed exclusively through a "stealth session" manager (`GhostSession` in `myra_app/fetcher.py`) that implements aggressive caching (`myra_cache_network.db` or generic `cache` table) to prevent N+1 query overhead.
Raw data is fetched from external sources (primarily NSE India), validated dynamically, and inserted into specialized, decoupled SQLite "sidecar" databases (`myra_technical.db`, `myra_institutional.db`, `myra_metadata.db`, etc.) using `INSERT OR REPLACE` to handle idempotency.
Once raw data is persisted, enrichment pipelines (e.g., `process_enrichment_pipeline` in `myra_app/feature_enrichment.py`) calculate features in a vectorized manner using Polars and update the technical databases.
Finally, the `DataAdapter` reads from these SQLite databases and provides data to ML agents (e.g., `AEON Agent`, `DeepEvolutionStrategy`) and analytical scanners (`myra_app/strategy_engine.py`, `myra_app/screener.py`), while calculated indicators are offloaded to an "Indicator Lake" (Parquet files) to prevent SQLite schema bloat.

## 2. End-to-End Data Flow (step-by-step pipeline)
- **SOURCE:** External APIs (NSE India `https://www.nseindia.com`, Yahoo Finance as fallback) provide raw market data.
- **FETCH:** `DataFetcher.fetch_ohlcv_delivery` attempts to pull daily Bhavcopy and MTO (delivery) data. `GhostSession` acts as a network gatekeeper and caches responses.
- **PROCESS:** `myra_app/daily_ingestor.py` normalizes columns to snake_case, filters for standard Equities (`SERIES` IN 'EQ', 'BE', 'SM'), parses dates to ISO format strings (`YYYY-MM-DD`), and explicitly checks `PRAGMA table_info` to drop unknown columns before insertion.
- **STORE:** Processed rows are persisted into `db/myra_technical.db` (and other DBs based on context) using batched `executemany` with `INSERT OR REPLACE` to prevent duplicates.
- **ENRICH:** `feature_enrichment.py` reads data using Polars/DuckDB/SQLite, computes vectorised features (e.g., `delivery_divergence_score`, `stock_return`), and writes back to `stg_enriched_market_data` before atomically renaming to `technical_data`. Indicators are exported to Parquet (`data/indicators/`).
- **READ:** Strategies use `DataAdapter.get_price_df` to read historical data (applying CamelCase renaming on the fly for compatibility).
- **OUTPUT:** Scanners execute logic over the `DataFrame` and output results to the UI (`rich` terminal dashboards) or `TelegramNotifier`.

## 3. Data Source Map
- **NSE India (Primary):** `bhavcopy`, `zip_mto_merge`, direct CSV formats. Fetched daily via `DataFetcher.fetch_ohlcv_delivery` (after 18:30 IST). Failure handling: Pre-flight checks (`_is_holiday`, `is_data_ready`), retry loops, and 404 categorization.
- **Yahoo Finance (Secondary):** Handled via `yfinance` in `DataAdapter` or index components, primarily used for global indices or missing technical fallback.
- **Local Metadata (Generated):** `db/myra_calendar.db` explicitly manages trading days vs holidays, driving scheduling.

## 4. Storage & Database Inventory
- **SQLite (Atomic Trilogy Sidecars):** Located in `db/` directory. Initialized via `LibrarianCore.DB_MAP` mappings or explicitly in tools.
  - `myra_technical.db`: Core price action (`technical_data` table).
  - `myra_institutional.db`: Insider trading, large deals, FII/DII activities.
  - `myra_valuation.db`: Fundamentals, quarterly results.
  - `myra_metadata.db`: System state, job status, cache metadata.
  - `myra_scoring.db`: Fundamental scores.
  - `myra_calendar.db`: Trading day calendar (`market_calendar`).
  - `myra_cache_network.db`: Scrapling cache.
- **Apache Parquet (Indicator Lake):** Located in `data/indicators/` (or `data/lake/`). Caches computed indicators (SMA, RSI, VWAP) to avoid schema bloat in SQLite.
- **JSON (Filesystem):** `data_sync_manifest.json` handles post-ingestion system health scores and missing delivery records.

## 5. Data Structure & Format Analysis
- **Date/Time Formats:** `myra_app/daily_ingestor.py` strictly uses ISO format strings `YYYY-MM-DD` (`.isoformat()`) for database insertion, avoiding string comparison issues.
- **Naming Conventions:** Lowercase snake_case in SQLite (`open`, `high`, `low`, `close`), converted dynamically to CamelCase (`Open`, `High`, `Low`, `Close`, `Volume`) inside `DataAdapter` for legacy components.
- **Serialization:** Parquet for large analytical subsets; JSON for sync metrics; SQLite for structured tabular data.
- **Numeric Handling:** Ingestion (`myra_app/ingest_bhavcopy.py`) drops non-numeric or missing `delivery` values explicitly and converts columns dynamically. Strict validation rejects values where delivery is missing or exactly `1.0`.

## 6. Transformation Pipeline Breakdown
- **Normalization:** Column names mapped to standard (e.g., `TOTTRDQTY` -> `volume`, `DELIV_QTY` -> `delivery`).
- **Data Filtering:** Non-equities dropped (`SERIES` IN ('EQ', 'BE', 'SM')).
- **Feature Engineering:** Vectorized Polars operations in `process_enrichment_pipeline` calculate `delivery_divergence_score`, `stock_return`, and `volatility_compression_score`.
- **Handling Missing Data:** Replaces missing delivery metrics with `1.0` dynamically before transformation, but raw ingest explicitly rejects completely invalid delivery rows.

## 7. Validation & Integrity Audit
- **Schema Validation:** `myra_app/daily_ingestor.py` asks SQLite (`PRAGMA table_info`) what columns exist, gracefully dropping missing ones before batched insertion.
- **Data Integrity:** `myra_app/ingest_bhavcopy.py` implements a "hard reject" for placeholder `delivery` values (rejecting exact 1.0 or NaN).
- **Null/Missing Handling:** Feature enrichment dynamically handles NaNs (`fill_nan(1.0).fill_null(1.0)`).
- **Silent Failures Risks:** The fallback to `1.0` inside the enrichment phase might mask genuine data gaps if the raw ingestion lets bad data through.

## 8. Runtime Risks & Failure Points
- **Silent SQLite DB Creation:** If paths are incorrect, SQLite might silently create an empty database file. Explicit checks `os.path.exists(path)` exist in `DataAdapter` to mitigate this.
- **Holiday/Calendar Drift:** If `myra_calendar.db` is out of date, ingestion jobs may fail or run unnecessarily.
- **Data Locking:** If multiple processes write concurrently to the same sidecar DB without proper isolation, `database is locked` errors could arise, though WAL mode is enabled via `PRAGMA journal_mode=WAL;`.
- **API Failure:** Handled via `GhostSession` retries and explicit NSE publication checks (status code 404).

## 9. Performance & Scalability Observations
- **N+1 Query Elimination:** Implemented via batched `executemany` inserts and vectorized `IN` queries.
- **Caching Mechanism:** Results are heavily cached in `GhostSession` for network responses and `_price_cache` in `DataAdapter`. Parquet Lake offloads read loads from SQLite.
- **Efficient Filtering:** Date clauses are parameterized securely in SQL queries, avoiding full table scans.
- **Scalability Limit:** High RAM usage if `_price_cache` grows infinitely without eviction strategies.

## 10. Critical Risks (HIGH / MEDIUM / LOW)
- **HIGH:** Missing or poorly timed `myra_calendar.db` updates could lead to data drift, false positives in multi-day signals, and pipeline errors.
- **MEDIUM:** Unbounded memory growth in `DataAdapter._price_cache` if scanning thousands of symbols over multiple days.
- **MEDIUM:** Hardcoded fallback values (e.g., delivery set to 1.0) might mask deeper data integrity issues down the line.
- **LOW:** Legacy engine dependency on CamelCase column names adds minor overhead during dynamic renaming inside `DataAdapter`.

## 11. Improvement Recommendations (NO CODE CHANGES)
- Implement an LRU eviction policy for `DataAdapter._price_cache` to prevent memory leaks during massive batch runs.
- Standardize all downstream consumer modules to accept snake_case columns, eventually deprecating the CamelCase proxy mapping in `DataAdapter`.
- Add active monitoring/alerting if `market_calendar` hasn't been updated for the current month.
- Ensure the Parquet Indicator Lake routinely cleans up stale indicator files to avoid disk space bloat.

## 12. Unknowns / Uncertain Areas
- **UNCERTAIN:** How `myra_calendar.db` is initially populated or actively kept synchronized with NSE ad-hoc holiday changes.
- **UNCERTAIN:** Whether the `cache` table in `myra_cache_network.db` has an active background cleanup process for expired entries, or if it grows indefinitely.
- **UNCERTAIN:** The exact trigger mechanisms for Parquet export workflows (`data_exporter.py`). It's unclear if this is fully automated or manually run via CLI tools.

## Architectural Concerns & Observability Gaps
- **Observability Gap:** While there are prints and logger warnings, centralized structured logging for database write failures and API retry exhaustion seems fragmented between `print()` and standard `logging`.
- **Architectural Risk:** The system is heavily decoupled (Atomic Trilogy), which is great for file-locking prevention but makes joining cross-domain data (e.g., technicals + fundamentals) rely entirely on application-layer logic (Pandas/Polars merges), which might become a bottleneck for complex joins.
