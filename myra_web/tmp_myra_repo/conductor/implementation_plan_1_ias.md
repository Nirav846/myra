# IMPLEMENTATION PLAN: Governance & IAS Engine (Plan 1)

## Objective
Build the data infrastructure and scoring logic for Institutional Activity tracking (3-month+ horizon).

## Key Files & Context
- `myra_app/librarian_schema.py`: Handles multi-DB initialization.
- `myra_app/fetcher.py`: Handles all network requests.
- `myra_app/librarian_sync.py`: Orchestrates background data updates.
- `myra_app/ias_manager.py` (New): Logic for IAS calculation and governance data processing.

## Implementation Steps

### Phase 1: Schema & Data Layer
1.  **Update `LibrarianSchemaMixin`**:
    - Initialize `self._gov_conn` pointing to `db/governance.db`.
    - Create tables: `sast_disclosures`, `pledged_history`, `shareholding_history`, `ias_history`.
    - Add indices on `symbol` and `date`.

### Phase 2: High-Fidelity Fetchers
1.  **Update `DataFetcher` in `fetcher.py`**:
    - `fetch_sast_disclosures(days=3)`: Calls `/api/corporate-sast-reg29`.
    - `fetch_pledged_info(symbol)`: Calls `/api/corporate-pledged-info`.
    - Ensure these use the verified `GhostSession` stealth patterns.

### Phase 3: IAS Engine (`ias_manager.py`)
1.  **Implement `IASManager`**:
    - `calculate_ias(symbol)`: Aggregates the 5 pillars (SAST, Delivery, Price, Volume, Compression).
    - `get_sast_score(symbol)`: Analyzes net 30d accumulation from `governance.db`.
    - `get_interaction_bonuses(...)`: Implements the Confluence and Trap boosts.
    - `update_ias_cache()`: Batches IAS calculation for the entire universe and stores in `ias_history`.

### Phase 4: Automation Hooks
1.  **Update `LibrarianSyncMixin` in `librarian_sync.py`**:
    - **Daily Hook**: Add `sync_sast_incremental()` after Masters update.
    - **Weekly Hook**: Add `sync_governance_full()` triggered on Saturdays.
2.  **Update `DataAdapter` in `data_adapter.py`**:
    - Add `get_latest_ias(symbol)` to return the current score and tag (e.g., "STRONG_ACCUMULATION").

## Verification & Testing
1.  **Unit Test**: Run `fetch_sast_disclosures` and verify deduplication by `disclosure_id`.
2.  **Integrity Check**: Confirm `pledged_pct` changes are correctly calculated QoQ.
3.  **End-to-End**: Run a scan on a symbol with known insider buying and verify IAS score > 7.
