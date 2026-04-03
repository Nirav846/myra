# Implementation Plan: Database Expansion (2021-Present)

## Objective
Backfill historical data to create a 5-year dataset (2021-2026) and optimize indicators for ML training.

## Phase 1: Backfill Completion (2022)
- [x] Analyze missing data for 2022. (Current: 158/250 days fetched).
- [x] Create `force_backfill_2022.py` for targeted acquisition.
- [~] Execute backfill in background. (Active).

## Phase 2: Indicator Re-calculation (ML Readiness)
- [x] Modify `myra_app/librarian.py` to use a 2000-day window (`INTERVAL 2000 DAY`) in `update_indicator_history`.
- [ ] Implement `repair_calculated_indicators.py` to force-refresh the entire history from 2021.
- [ ] Run the refresh script once backfill is 100% complete.

## Phase 3: Validation
- [ ] Verify `calculated_indicators` count for a sample stock (e.g., RELIANCE) is > 1200 rows.
- [ ] Confirm no NaN values in `std20`, `d_poc`, and `smc_phase` for historical dates.
