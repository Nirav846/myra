# Implementation Plan: Data Health & Strategy Restoration

## Phase 1: Immediate Backfill
- [x] Create `force_backfill.py` to sync 1.5 years of history for all symbols. (Commit: 7f3a2b1)
- [x] Execute `force_backfill.py` and monitor progress. (Commit: 7f3a2b1)
- [x] Verify database row count for `SUNPHARMA`. (Commit: 7f3a2b1)

## Phase 2: Indicator Re-computation
- [x] Trigger `Librarian.update_indicator_history()` to fill in missing technical metrics for the new data range. (Included in sync)
- [x] Ensure `smart_money_score` is computed and not NaN. (Verified)

## Phase 3: Strategy Verification
- [ ] Re-run `test_whale_debug.py` for `SUNPHARMA` (should now have >250 rows).
- [ ] Run `myra.py` Option 15 and confirm it returns results.
