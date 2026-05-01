# Implementation Plan: Librarian Modularization (Atomic Trilogy)

This plan outlines the systematic migration from a monolithic DuckDB to a modular SQLite architecture.

## Phase 1: Data Extraction & Migration
Migrate all non-OHLCV tables from DuckDB to specialized SQLite sidecars.

- [ ] **Step 1.1:** Create `tools/migrate_institutional.py`
  - Targets: `insider_trades`, `large_deals`
- [ ] **Step 1.2:** Create `tools/migrate_metadata.py`
  - Targets: `symbols_master`, `index_constituents`, `benchmarks`, `metadata`
- [ ] **Step 1.3:** Create `tools/migrate_valuation.py`
  - Targets: `fundamentals`, `quarterly_results`
- [ ] **Step 1.4:** Execute all migration scripts and verify row counts.

## Phase 2: Core Librarian Refactoring
Update the persistence layer to handle multi-DB routing.

- [ ] **Step 2.1:** Modify `myra_app/librarian_core.py`
  - Implement dynamic connections to `technical.db`, `institutional.db`, `meta.db`, and `valuation.db`.
- [ ] **Step 2.2:** Update `myra_app/librarian_schema.py`
  - Redefine table creation logic across the new sidecars.
- [ ] **Step 2.3:** Update `myra_app/librarian_sync.py`
  - Update all `INSERT` and `UPDATE` statements to use the correct modular connections.

## Phase 3: Strategy Decoupling (The Parquet Lake)
Shift scanner output from SQLite to strategy-isolated Parquet files.

- [ ] **Step 3.1:** Implement `IndicatorManager` in `myra_app/data_loader.py`.
- [ ] **Step 3.2:** Refactor `ml_engine.py` and `SMC_Scanner.py` to use Parquet for indicator storage.
- [ ] **Step 3.3:** Clean up `calculated_indicators` table (Deprecation).

## Phase 4: Hardening & Final Purge
Verify system stability and remove legacy files.

- [ ] **Step 4.1:** Run `myra_app/technical_audit.py` on the new modular stack.
- [ ] **Step 4.2:** Execute full market sync test.
- [ ] **Step 4.3:** Safely delete `db/myra_market_data.db`.
- [ ] **Step 4.4:** Move `http_cache.sqlite` to `db/network_cache.sqlite` and update `fetcher.py`.
