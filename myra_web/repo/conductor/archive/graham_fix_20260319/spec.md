# Specification: Graham Value & AI Forecast Fix

## Objective
Fix the "Graham Deep Value" scanner (Option 13) to ensure it returns valid results and verify that the "AI Trend Forecast" is correctly displayed in the Mission Control dashboard.

## Requirements
1. **Fundamental Data Accuracy**:
    - Ensure `eps` and `book_value` are correctly scraped from `ScreenerSource`.
    - Verify `Librarian` updates `fundamentals_quarterly` and `fundamentals` tables without `NaN`.
    - Confirm `Engine` correctly maps `EPS_Latest` and `BVPS_Latest` from the fundamentals lookup.
2. **Scanner Logic (123)**:
    - Verify the Graham Number formula: `(22.5 * eps * bv) ** 0.5`.
    - Ensure `run_scanner` in `primitives.py` correctly handles the trigger condition.
3. **AI Forecast Visibility**:
    - Confirm `draw_dashboard` in `UI_Manager.py` receives and renders the forecast data.
    - Verify the `warmup` thread in `myra.py` successfully completes and updates the shared state.

## Technical Details
- **Files**: `myra_app/data_sources/screener_source.py`, `myra_app/librarian.py`, `myra_app/engine.py`, `myra_app/UI_Manager.py`.
- **Target Tables**: `fundamentals`, `fundamentals_quarterly`.
