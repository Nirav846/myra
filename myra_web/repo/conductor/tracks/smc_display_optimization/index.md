# Track: SMC Display Optimization
**Status**: ACTIVE
**Owner**: Gemini CLI

## Objective
Customize the result parameters for SMC scans (14 and 30) to prioritize institutional metrics (D-POC, Absorption, Ignition) and remove fundamental overhead.

## Plan
- [x] Define the ideal "SMC Hero Columns" for Phase 1 and Phase 2. (Done: D-POC, POC_Dist, Absorption, Tightness, etc.)
- [x] Modify `screener.py` to bypass fundamental enrichment for these scan IDs. (Fixed: IDs 126 and 30 now skip fundamentals).
- [x] Update `myra.py` to pass the optimized column lists to the results manager. (Fixed: Custom hero_cols added for 126 and 30).
- [x] Ensure `engine.py` provides the raw metrics (`absorption`, `tightness`, `d_poc`) to the results payload. (Fixed: Updated _worker_task payload).
- [ ] Final Validation: Run `1:14` and verify the new technical table layout.
