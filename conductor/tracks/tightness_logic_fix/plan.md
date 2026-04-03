# Implementation Plan: Tightness Logic Fix

## Objective
Fix the 0.0% Tightness issue in scan results by ensuring proper data flow from `Librarian` to `Engine`.

## Phase 1: Data Persistence
- [x] Update `myra_app/librarian.py`:
    - Add `std20` to `calculated_indicators` table schema.
    - Ensure `std20` (STDDEV of close over 20 days) is computed and stored in `update_indicator_history`.

## Phase 2: Engine Data Flow
- [x] Update `myra_app/engine.py`:
    - Ensure `std20` is extracted from `cache_df` and added to `funda_map`.
    - Fix `Tightness` calculation in `_worker_task`:
        - Current: `f"{round(funda.get('std20', 0) / df['Close'].iloc[-1] * 100, 2)}%"`
        - Handle division by zero and ensure types are correct.

## Phase 3: Validation
- [ ] Run `1:14` (Scanner 126) or any SMC scan.
- [ ] Verify `Tightness` column shows non-zero values (e.g., 0.85%).
- [ ] Cross-verify with `LTP` and `std20` in the database if possible.
