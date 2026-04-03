# Implementation Plan: Post-Input Crash Fix

## Objective
Fix the "Silent Exit" bug where scans return to the menu without showing results.

## Phase 1: Error Trapping & Logging
- [x] Add try-except blocks to `screener.execute_scan` in `myra_app/screener.py`.
- [x] Add status messages to indicate when 0 stocks pass the filter vs a crash.

## Phase 2: Logic Fixes
- [x] Update `myra_app/engine.py`:
    - Ensure `smc_phase` and `d_poc` are correctly calculated in `funda_map` before the worker task starts.
    - Fix the `rename_map` in `run_scan` to correctly handle `Close` vs `close` casing.
- [x] Update `myra_app/librarian.py`:
    - Ensure `precompute_indicators` returns the most recent data even if today's sync is incomplete.

## Phase 3: Validation
- [ ] Run `1:14` (Scanner 126) and verify results table is shown.
- [ ] Run a standard technical scan (e.g., Choice 1) and verify results.
