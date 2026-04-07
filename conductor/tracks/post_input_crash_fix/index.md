# Track: Post-Input Crash Fix (Empty Result Investigation)
**Status**: ACTIVE
**Owner**: Gemini CLI

## Objective
Fix the issue where scans exit immediately back to the main menu without displaying results or errors.

## Plan
- [x] Wrap `execute_scan` in `myra.py` with error trapping. (Identified silent exit as empty results).
- [x] Add debug logging to `screener.py` to check the `symbols` count before the scan. (Symbols were resolved correctly).
- [x] Add debug logging to `engine.py` to identify why `run_scan` returns empty results. (Fixed: `smc_phase` and `d_poc` were missing from the worker's `funda_map`).
- [x] Investigate if `SMCManager` logic is causing all stocks to be filtered out. (Fixed: Logic was correct but inputs were missing).
- [x] Verify that `Librarian` is actually providing data for the requested symbols. (Fixed: Indicators were stale; implemented optimized 300-day refresh).
- [ ] Final Validation: User to run `1:14` and confirm results are displayed.
