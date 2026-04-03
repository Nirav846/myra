# Implementation Plan: AEON UI Polish & Precision Formatting

## Objective
Restore missing institutional columns to Strategy 31 (AEON Agent) and implement universal 2-decimal precision formatting for all numeric metrics in discovery tables.

## Phase 1: Column Restoration (AEON Agent)
- [x] Update `myra_app/myra.py`:
    - Ensure `Strategy 31` (AEON Agent) uses the correct `hero_cols`.
    - Current: `["LTP", "d_poc", "Floor_Gap%", "SMC", "Absorp_Ratio", "RDV"]`
    - Verify `d_poc` (lowercase) vs `D-POC` (uppercase) consistency with `engine.py`. (Fixed in `results_manager.py` to handle both).

## Phase 2: Universal Precision Formatting
- [x] Modify `myra_app/results_manager.py`:
    - Refactor `display_discovery_table` to apply `f"{val:.2f}"` to ALL numeric columns (LTP, Entry, TSL, and all hero columns).
    - Ensure non-numeric strings (like "CONVICTION", "Stage 2") are preserved.
    - Fix the manual formatting loop for `hero_cols` to handle `None` and `NaN` values gracefully.

## Phase 3: Validation
- [ ] Run `python myra_app/myra.py` and execute Strategy 31 (Option 31).
- [ ] Confirm `d_poc` values are visible.
- [ ] Confirm all numbers (LTP, RS, Absorption, etc.) are rounded to 2 decimal places.
