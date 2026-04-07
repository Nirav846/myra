# Implementation Plan: SMC Result Cleanup & Logic Refinement

## Objective
Remove duplicate columns in the discovery table and refine Phase 1 'Tightness' criteria to filter out volatile stocks.

## Phase 1: Column De-duplication
- [x] Investigate `myra_app/myra.py` and `myra_app/results_manager.py`:
    - Check if `Stage` or `Confluence` are being added to `hero_cols` twice.
    - Ensure `display_discovery_table` handles overlapping columns between default set and `hero_cols`. (Fixed in `results_manager.py` by excluding standard columns from `hero_cols` iteration).

## Phase 2: Tightness Logic Refinement (Phase 1)
- [x] Update `myra_app/scanners/primitives.py` (or where SMC Phase 1 logic lives):
    - Stricter `Tightness` threshold for Phase 1 (e.g., < 1.5%).
    - Current logic in `engine.py` worker task uses `funda.get('std20', 0) / df['Close'].iloc[-1] * 100`. (Updated in both `engine.py` and `librarian.py`).

## Phase 3: Volume Dry-up Check
- [x] Implement `Volume Dry-up` logic:
    - Current Volume < 60% of 20-day Average Volume.
    - Add this as a condition for "Basing" (Phase 1) signals. (Implemented in `SMCManager` and SQL logic).

## Phase 4: Validation
- [ ] Run `1:14` (Scanner 126) and verify:
    - No duplicate columns.
    - Volatile stocks (e.g., FORCEMOT) with high `std20` are filtered out of Phase 1.
    - "Basing" signals show low relative volume.
