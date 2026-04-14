# MYRA Tools Directory Guide

This folder contains utilities, migration scripts, and maintenance tasks for the MYRA system.

## v3.2 Compliant Tools (Atomic Trilogy)

### `create_technical_db.py`
* **Purpose**: Infrastructure Initialization. Creates the modular `myra_technical.db` schema.
* **When to use**: During initial environment setup or if the technical database is corrupted and needs a fresh start.
* **How to use**: `python -m tools.create_technical_db`
* **Trilogy Status**: v3.2 Compliant

### `data_health_dashboard.py`
* **Purpose**: Diagnostics & Auditing. Reads `data_sync_manifest.json` and prints the Data Confidence Score and symbols missing delivery data.
* **When to use**: After daily ingestion to verify data integrity and delivery parsing health.
* **How to use**: `python -m tools.data_health_dashboard`
* **Trilogy Status**: v3.2 Compliant

### `force_indicator_refresh.py`
* **Purpose**: Maintenance. Forces a full recalculation of technical indicators for all symbols in `technical_data`.
* **When to use**: When indicator logic changes, or if historical indicator data becomes out of sync with prices.
* **How to use**: `python -m tools.force_indicator_refresh`
* **Trilogy Status**: v3.2 Compliant

### `performance_guard.py`
* **Purpose**: Performance Check. Scans codebase for BANNED_METHODS (e.g. iterrows, strftime in loops).
* **When to use**: Run as a pre-commit check or during PR reviews to enforce "quant-ready" strict vectorized code rules.
* **How to use**: `python -m tools.performance_guard`
* **Trilogy Status**: v3.2 Compliant

### `benchmark_db_batching.py`
* **Purpose**: Performance Benchmarking. Standardized baseline to measure the impact of batching and chunking on SQLite performance.
* **When to use**: When optimizing ingestion scripts or tuning batch sizes for `GhostSession`.
* **How to use**: `python -m tools.benchmark_db_batching`
* **Trilogy Status**: v3.2 Compliant

### `migrate_db_names.py`
* **Purpose**: Migration. Renames legacy database files to their standard Trilogy Map names.
* **When to use**: Run only once during architecture shifts to map old databases to `myra_technical.db`, `myra_institutional.db`, etc.
* **How to use**: `python -m tools.migrate_db_names`
* **Trilogy Status**: v3.2 Compliant

### `migrate_duck_to_sqlite.py`
* **Purpose**: Migration. Migrates DuckDB legacy parity files to standard SQLite schema.
* **When to use**: Run only once during the v3.0 to v3.2 phase out of DuckDB.
* **How to use**: `python -m tools.migrate_duck_to_sqlite`
* **Trilogy Status**: v3.2 Compliant

### `migrate_institutional.py`, `migrate_metadata.py`, `migrate_valuation.py`
* **Purpose**: Migration. Component-specific data transfer from monolithic/legacy stores into the Atomic Trilogy DBs.
* **When to use**: Run only once when populating the localized DB modules from legacy.
* **How to use**: `python -m tools.migrate_institutional`
* **Trilogy Status**: v3.2 Compliant

## Verification & Auditing Tools

### `mission_control_audit.py`, `validate_librarian.py`, `audit_scanners.py`
* **Purpose**: Audit. Comprehensive verification that Librarian integrations, modules, and positional scans are functioning.
* **When to use**: Pre-release sanity check or debugging systematic logic failures.
* **How to use**: `python -m tools.mission_control_audit`
* **Trilogy Status**: v3.2 Compliant

### `analyze_factors.py`, `trace_multibagger.py`, `tuner.py`
* **Purpose**: Strategy & Analytics. Tools to verify rankers and test hypothesis against historical factors.
* **When to use**: When researching new indicators or tuning SMC rules.
* **How to use**: `python -m tools.analyze_factors`
* **Trilogy Status**: v3.2 Compliant

## Data Management & Fixes

### `backfill_year.py`, `force_backfill.py`, `force_sync.py`
* **Purpose**: Data Repair. Historical data fetching and mass repopulation bypassing daily sync.
* **When to use**: Setting up a new environment or filling large date gaps in technical history.
* **How to use**: `python -m tools.backfill_year`
* **Trilogy Status**: v3.2 Compliant

### `fix_symbol_lineage.py`, `symbol_mapper.py`, `classify_symbols.py`, `sync_sectors.py`
* **Purpose**: Metadata Management. Resolves ticker changes, assigns sectors, and maps ISINs.
* **When to use**: Monthly maintenance or when NSE issues bulk ticker renaming.
* **How to use**: `python -m tools.fix_symbol_lineage`
* **Trilogy Status**: v3.2 Compliant

## Debugging Scripts (Legacy/Ad-hoc)

### `check_dates.py`, `check_depth.py`, `check_insider_data.py`, `check_insider_status.py`, `check_nifty.py`, `check_portfolio_data.py`, `check_sync_date.py`, `query_db.py`, `query_meta.py`, `query_raw.py`
* **Purpose**: Fast diagnostic prints of local SQLite DB states.
* **When to use**: Only when debugging a specific data corruption or testing fetcher.
* **How to use**: `python -m tools.check_dates`
* **Trilogy Status**: Legacy / Ad-hoc

### `debug_backfill.py`, `debug_mapping.py`, `investigate_gaps.py`, `repair_calculated_indicators.py`, `test_insider_fetch.py`, `recovery_resources.py`
* **Purpose**: Incident Response. Debuggers explicitly designed to trace missing data or correct specific cache faults.
* **When to use**: As needed for tactical hot-fixes on database invariants.
* **How to use**: `python -m tools.debug_backfill`
* **Trilogy Status**: Legacy / Ad-hoc

---
*Note: All python scripts within tools/ must be executed via `python -m tools.<script_name>` from the project root to ensure module imports from `myra_app` function correctly.*
