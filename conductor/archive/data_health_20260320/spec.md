# Specification: Data Health & Strategy Restoration

## 1. Problem
The "Elite Whale Tracker" (Option 15) and other complex strategies return zero results because the local database (`myra_market_data.db`) only contains data back to mid-September 2025 (~125 rows), while the strategies require a minimum of 250 rows for feature engineering and ML training.

## 2. Success Criteria
- [ ] Database contains at least 375 trading days (1.5 years) of history for NIFTY 500 stocks.
- [ ] `calculated_indicators` is fully recomputed and populated for the extended history.
- [ ] `whale_tracker.py` produces signals when run (verified via debug script).

## 3. Implementation Approach
1. **Force Backfill Script:** Create a standalone utility `force_backfill.py` that utilizes `Librarian.sync_market_data` with a larger `history_years` parameter (e.g., 2.0).
2. **Indicator Refresh:** Ensure `update_indicator_history` is triggered after the backfill to populate missing columns (like `sma150`, `smart_money_score`).
3. **Automated Validation:** Verify row counts for key symbols (SUNPHARMA, RELIANCE) before declaring success.
