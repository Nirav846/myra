# MYRA Sector & Industry Intelligence - Design Spec (v3.0 Atomic)

## 1. Objective
Enable sector-based analysis, relative strength, and institutional flow tracking by mapping every NSE symbol to its standardized Macro-Economic Sector, Sector, and Industry classification.

## 2. Architecture (Atomic Trilogy Integration)
In accordance with the MYRA v3.0 mandate, sector metadata is stored in the **System Brain (`db/meta.db`)** to ensure unified lookup across all scanners.

### 2.1 Database Schema (`db/meta.db:symbols_master`)
The following columns will be added/updated:

| Column | Type | Description |
| :--- | :--- | :--- |
| `sector` | TEXT | Standardized Macro-Economic Sector (Normalized). |
| `industry` | TEXT | Standardized Industry (Normalized). |
| `raw_sector` | TEXT | Original sector string from the source. |
| `raw_industry` | TEXT | Original industry string from the source. |
| `source` | TEXT | Data source identifier (`NSE`, `SCREENER`, `YFINANCE`). |
| `confidence` | REAL | Confidence score (1.0 = Official, <1.0 = Fallback). |
| `last_updated_sector` | DATETIME | ISO-8601 timestamp of last update. |
| `sector_locked` | INTEGER | 1 = Prevents automated updates; 0 = Open for sync. |

### 2.2 Indexes
*   `idx_master_sector` on `symbols_master(sector)`
*   `idx_master_industry` on `symbols_master(industry)`

## 3. Data Acquisition (Hybrid Strategy)

### 3.1 Sources (`config/sources.json`)
1.  **Primary (NSE Indices):** `https://niftyindices.com/IndustryClassification/Ind_nifty500list.csv`
    *   *High Reliability:* Contains Official 4-tier mapping (Macro-Economic Sector, Sector, Industry, Basic Industry).
2.  **Fallback (Screener.in):** Fetched via `Fetcher` for symbols missing from NSE Index lists.
3.  **Last Fallback (yfinance):** Standard API call for SME/Liquid symbols.

### 3.2 Normalization (`config/sector_map.json`)
A mapping dictionary to collapse disparate naming conventions into clean, tradable buckets.
*   Example: `{"Financial Services": "Financials", "Public Sector Bank": "Banking"}`

## 4. Automation Logic (`SectorManager`)

### 4.1 Sync Modes
*   **Maintenance (Monthly):** Full sweep of `symbols_master`. Refreshes all non-locked symbols.
*   **Daily (Incremental):** Triggered during daily backfill.
    *   Targets symbols where `sector IS NULL`.
    *   Targets stale data where `current_date - last_updated_sector > 90 days`.

### 4.2 Confidence Scoring
*   **NSE:** 1.0 (Official Source)
*   **Screener:** 0.8 (Reliable Public Source)
*   **yfinance:** 0.6 (Best-effort fallback)

## 5. Implementation Steps
1.  Update `LibrarianSchemaMixin` in `myra_app/librarian_schema.py` to include new columns.
2.  Create `config/sector_map.json` with initial common mappings.
3.  Implement `myra_app/sector_manager.py` with `fetch`, `normalize`, and `update` methods.
4.  Add `tools/sync_sectors.py` for manual/scheduled maintenance.
5.  Hook `SectorManager` into `myra_app/librarian_sync.py` for daily incremental updates.

## 6. Success Criteria
*   95%+ coverage for Nifty 500 stocks.
*   80%+ coverage for broad NSE market (3700+ symbols).
*   Zero schema locks on `meta.db` during daily operations.
