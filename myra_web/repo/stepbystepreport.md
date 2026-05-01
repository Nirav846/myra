# MYRA Architectural and Data Audit Report

## 1. Executive Summary
MYRA is an advanced stock screening and analytics platform built with a high-performance, vectorized philosophy. The system design relies on the "v3.0/v3.2 Atomic Trilogy" architecture, advocating a decentralized network of SQLite sidecar databases (for specific domains like technical, institutional, valuation) with computationally heavy indicator data caching natively in a Parquet Lake.

However, the actual runtime behavior demonstrates a system trapped mid-migration. Despite clear rules banning DuckDB and legacy monolithic databases (`myra_market_data.db`), significant components (`LibrarianCore`, `Gatekeeper`, `feature_enrichment.py`, and many tools) still actively rely on them, creating deep architectural drift. Additionally, undocumented tooling (`polars`) is widespread, and strictly banned pandas anti-patterns (`.iterrows()`) remain in use in both TUI components and ingestion flows. This divergence poses significant risks of data split-brain scenarios and silent data corruption.

## 2. Source of Truth Summary
**Declared Rules (Extracted from `PROJECT_RULES.md`, `README.md`, `LibrarianCore` mappings):**
- **Architecture:** "Atomic Trilogy" mapping via `LibrarianCore.DB_MAP` to specific SQLite databases (`myra_technical.db`, `myra_institutional.db`, `myra_metadata.db`, etc.). Legacy DBs are deprecated.
- **Storage:** Indicators and strategy results must be offloaded to isolated Parquet files (`data/indicators/`) to prevent SQLite schema bloat.
- **Tooling:** Pandas and Numpy are the official tools for vectorization. `duckdb` is explicitly deprecated.
- **Data Formats:** OHLCV DataFrames MUST use `CamelCase` (`Open`, `High`, etc.), while the Indicator Lake MUST use `lowercase_snake_case`. Dates must be native `datetime64[ns]`.
- **Validation:** Banned patterns include `iterrows()`, `apply()` on large datasets, and `DataFrame.append()` inside loops (O(N²) complexity).
- **Security:** Safe DB table operations must use explicit dictionary mapping (`ALLOWED_QUERIES`) instead of dynamic f-string formatting.

**Source-of-Truth Conflict:** While `PROJECT_RULES.md` mandates Pandas/Numpy and bans DuckDB, `README.md` notes DuckDB deprecation, but DuckDB and an undocumented dependency (`polars`) are still heavily used in core pipelines.

## 3. Actual System Behavior
### Runtime Flow
- **ENTRY:** Initiated via CLI, `daily_ingestor.py`, or scheduled jobs.
- **FETCH:** Retrieves NSE data (Bhavcopy, MTO delivery, corporate metadata) using network fetchers with a "stealth session" and network cache.
- **PROCESS:** Formats columns, filters specific series (`EQ`, `BE`, `SM`), and computes vectorized features via `polars` (e.g., in `feature_enrichment.py`).
- **STORE:** The storage flow is split. Some ingestors successfully map to the new SQLite sidecars (e.g., `myra_technical.db`), while legacy functions and older fallback scripts still write to or query DuckDB and `myra_market_data.db`. Indicator data is stored in the Parquet Lake.
- **READ/OUTPUT:** Strategies/scanners extract data using `DataAdapter` or direct queries and display results on a text-based TUI dashboard (`myra_app/tui_app.py`).

### Data Sources
- NSE India (Bhavcopy, corporate info, etc.).
- Yahoo Finance (via `yfinance`) as a fallback.

### Storage
- SQLite (e.g., `db/myra_technical.db`, `db/myra_meta.db`).
- DuckDB (e.g., `db/myra_market_data.db` still actively used/fallback).
- Apache Parquet for indicators (`data/indicators/`).
- JSON files for sync manifestations and manifests.

### Tooling
- `pandas` and `numpy` (Standard).
- `duckdb` (Deprecated, but heavily used).
- `polars` (Undocumented, actively replacing pandas in enrichment pipelines).

## 4. End-to-End Data Flow
- **SOURCE:** External raw CSV and JSON responses from NSE/YFinance APIs.
- **INGESTION:** `myra_app/fetcher.py` and `librarian_ingestor.py` pull daily data. Raw strings/columns are mapped. Missing deliveries (e.g., strict `1.0` rejections) are handled before DB insertions.
- **TRANSFORMATION:** `process_enrichment_pipeline` uses `polars` to calculate complex technical features (e.g., `delivery_divergence_score`, `stock_return`). Legacy data tools use Pandas with `.iterrows()` in offline bulk imports (`ingest_all_offline.py`).
- **STORAGE:** Clean/transformed data is loaded via SQL `INSERT OR REPLACE` to SQLite databases (Atomic Trilogy) via `executemany` batches, but `Gatekeeper` and legacy processes manage identical state within DuckDB and monolithic DBs.
- **CONSUMPTION:** Scanners, machine learning strategies, and TUI dashboards pull processed data directly from these disjointed data stores.

## 5. Database & Storage Inventory
- **SQLite Databases (db/):** `myra_technical.db`, `myra_institutional.db`, `myra_valuation.db`, `myra_metadata.db`, `myra_governance.db`, `myra_scoring.db`, `myra_calendar.db`, `myra_cache_network.db`.
- **DuckDB (db/):** `myra_market_data.db` (monolithic, legacy but still active).
- **Parquet (data/indicators/):** Offloaded Indicator files caching indicators (SMA, RSI, etc.).
- **JSON:** System state arrays (`data_sync_manifest.json`).

## 6. Data Structure & Format Analysis
- **Formats:** Data typically normalizes to lowercase snake_case in standard DB inserts but expects CamelCase translation (`Open`, `High`) when pushed to Pandas dataframes for legacy strategy scripts.
- **Dates:** Dates parsed strictly into ISO format string (`YYYY-MM-DD`) internally or transformed to `datetime64[ns]` vectorization.
- **Handling of Nulls/Defaults:** There is explicit logic to fill null deliveries or missing records with a `1.0` fallback, avoiding crashing during rolling calculations, although pure ingestors reject 1.0 delivery values initially.

## 7. Drift Report (MAIN SECTION)

### Drift Item 1: Legacy Database Monolith Usage
- **Category:** Storage / Architecture
- **Expected:** All DB connections must use the modular mapped sidecars via `LibrarianCore.DB_MAP` (SQLite).
- **Actual:** DuckDB-driven monolithic `myra_market_data.db` remains hardcoded in `LibrarianCore` fallback mechanisms and active scripts.
- **Drift Type:** Deprecated but still used / Contradictory Implementation.
- **Severity:** HIGH
- **Confidence:** HIGH
- **Where observed:** `myra_app/librarian_core.py`, `tools/migrate_duck_to_sqlite.py`, multiple tools.

### Drift Item 2: Deprecated Tooling Active Execution
- **Category:** Tooling
- **Expected:** `duckdb` is deprecated and prohibited in favor of SQLite sidecars.
- **Actual:** DuckDB serves as a primary driver in `Gatekeeper`, `FeatureEnrichment`, and `LibrarianCore`.
- **Drift Type:** Deprecated but still used.
- **Severity:** HIGH
- **Confidence:** HIGH
- **Where observed:** `myra_app/gatekeeper.py`, `myra_app/feature_enrichment.py`, `myra_app/librarian_core.py`.

### Drift Item 3: Undocumented Tool Usage (Polars)
- **Category:** Tooling
- **Expected:** Pandas and Numpy are the sole officially supported tools for Data manipulation.
- **Actual:** `polars` is actively running inside the vectorized feature enrichment processes.
- **Drift Type:** Missing Implementation (Documentation).
- **Severity:** MEDIUM
- **Confidence:** HIGH
- **Where observed:** `myra_app/feature_enrichment.py`.

### Drift Item 4: Banned Anti-Pattern (.iterrows())
- **Category:** Coding Standards / Performance
- **Expected:** No `iterrows()`; Hard Fail upon PR validation.
- **Actual:** `.iterrows()` is actively being used for batch iterations in UI tables and ingestion logic.
- **Drift Type:** Contradictory Implementation.
- **Severity:** MEDIUM
- **Confidence:** HIGH
- **Where observed:** `test/ingest_all_offline.py`, `myra_app/tui_app.py`.

### Drift Item 5: Security Linter Bypasses / Dynamic SQL Formatting
- **Category:** Security / Enforcement
- **Expected:** Dynamic f-strings are banned for table operations; must rely on `ALLOWED_QUERIES`.
- **Actual:** `myra_app/feature_enrichment.py` bypasses this rule utilizing string formatting for DB querying (`f"SELECT * FROM {table_name}"`) and silences the warning via `# noqa: S608`.
- **Drift Type:** Contradictory Implementation.
- **Severity:** HIGH
- **Confidence:** HIGH
- **Where observed:** `myra_app/feature_enrichment.py`.

## 8. Root Cause Analysis
- **Incomplete Migration (CONFIRMED):** The move from DuckDB/Monolithic DB to Atomic Trilogy/SQLite Sidecars (v3.0/v3.2) was started but never finished, leaving bridging logic active in `LibrarianCore`.
- **Performance Trade-offs (LIKELY):** The utilization of Polars inside `feature_enrichment.py` was likely driven by execution speed needs over massive data batches, outperforming traditional Pandas setups.
- **Lack of Enforcement (CONFIRMED):** Security and linter restrictions (`performance_guard.py` or pre-commit hooks) are actively being circumvented using `# noqa` tags or are failing to run against specific directories (`test/`).

## 9. Risk Assessment
- **HIGH: Data Split-Brain / Silent Corruption:** Since `LibrarianCore` and processes simultaneously query SQLite sidecars and legacy DuckDB connections, data states are fragmenting. Some ML models are likely operating on disjointed or stale signals.
- **HIGH: Security Vulnerabilities:** Use of `# noqa: S608` disables SQL injection protection for table name inputs, presenting a significant risk if the tables are eventually populated via API or user-driven configs.
- **MEDIUM: Dependency Bloat / Fragmentation:** Supporting DuckDB, SQLite, Pandas, and Polars in a single application environment increases cognitive overhead for maintainers, reduces testability, and slows down local environment setup.
- **MEDIUM: Performance Inconsistencies:** The continued use of `iterrows()` creates non-linear execution time growth on large historical market datasets.

## 10. Validation & Integrity Audit
- **Schema Validation:** Ingestors use strict schema checks (`PRAGMA table_info`) and `INSERT OR REPLACE` to avoid duplication.
- **Type/Null Checks:** Nulls are coerced effectively (e.g., filling with `1.0` for missing delivery arrays).
- **Enforcement Bypasses:** Linter bypassing is severe (`# noqa: S608`). Explicit structural mandates from `PROJECT_RULES.md` are routinely ignored in non-core utilities and tests.

## 11. PR Review (if applicable)
- N/A - This is a repository-wide architectural audit.

## 12. Architectural Weak Points
- **LibrarianCore as a God Object/Crutch:** Attempting to simultaneously manage an old monolith via DuckDB and the new multi-sidecar architecture makes this module unpredictable and prone to masking failures (silent fallbacks).
- **Tight Coupling in Tooling:** `test/` and `tools/` directories deeply rely on the legacy DuckDB implementation. Disabling DuckDB immediately breaks these offline scripts.
- **Inconsistent Naming Logic:** Juggling `lowercase_snake_case` in SQLite and `CamelCase` in Panda Dataframes introduces unnecessary renaming loops and potential mapping bugs in adapters.

## 13. Observability Gaps
- **Silent Database Fallbacks:** Failing to connect to an expected SQLite DB triggers an automatic attempt to connect to the DuckDB structure in `LibrarianCore` without throwing terminal-level fatal errors or robust logging.
- **Missing Monitoring for Calendar:** There is no clear observation pipeline to verify if `myra_calendar.db` stays synced, which is critical for defining trading days correctly.

## 14. Improvement Recommendations (NO CODE CHANGES)
- **Process Improvements:**
  - Execute a finalized "deprecation day" for DuckDB. Remove the fallback logic entirely in `LibrarianCore` and force migrating all `tools/` and `test/` scripts to SQLite.
  - Document the usage of `polars` explicitly in `PROJECT_RULES.md` if it represents the future vectorization stack.
- **Enforcement Ideas:**
  - Configure pre-commit hooks to outright reject commits holding `# noqa: S608` and the `.iterrows()` method across the entire repository (including `/test`).
  - Introduce `ALLOWED_QUERIES` dictionary mapping to remove dynamic f-string execution in feature enrichments securely.
- **Monitoring Suggestions:**
  - Centralize logging outputs. Swap out generic `print()` error handlers in `LibrarianCore` with a structured logger (`myra_log`) emitting alerts when sidecar connections fail to prevent silent fallback usage.

## 15. Unknowns / Uncertain Areas
- **Indicator Lake Hygiene:** It's UNCERTAIN whether the legacy DuckDB processes write their indicators to the newer `Parquet Lake` architecture or dump them redundantly back into their monolithic tables.
- **Event-Driven Calendar Update Flow:** It's UNCERTAIN how NSE holiday data enters the `myra_calendar.db` and how often the platform self-corrects against unpredicted ad-hoc exchange closures.
- **Complete Scope of Polars Migration:** It's UNCERTAIN if the goal is to completely rip out Pandas in favor of Polars system-wide, or if it remains relegated specifically to ML processing nodes.
