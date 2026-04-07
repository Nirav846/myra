# Implementation Plan: Background Sync Status Summary

## Phase 1: Librarian Enhancement
- [x] Define `SyncStatus` class in `myra_app/librarian.py`.
- [x] Initialize `self.sync_status` in `Librarian.__init__`.
- [x] Update `Librarian._fetch_range()` to report progress.
- [x] Update `Librarian.sync_market_data()` to report task transitions.
- [x] Update `Librarian.update_quarterly_fundamentals()` for status reporting. (Included in sync loop)

## Phase 2: UI Enrichment
- [x] Update `MYRA_UI.get_footer()` in `myra_app/UI_Manager.py` to check `librarian.sync_status`.
- [x] Implement conditional rendering for Sync Status vs Breadth.

## Phase 3: Validation
- [ ] Run `python force_backfill.py` and verify footer updates (simulated or real).
- [ ] Run `myra.py` and monitor background sync via footer.
