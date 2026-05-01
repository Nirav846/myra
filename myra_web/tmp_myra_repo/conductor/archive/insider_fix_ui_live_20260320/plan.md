# Implementation Plan: Insider Fix & Live UI Overhaul

## Phase 1: Engine Metrics Restoration
- [x] Modify `Engine.run_scan` in `myra_app/engine.py`:
    - Update `insider_map` calculation to include count of unique buy dates.
    - Calculate `AV_Accel` based on date count.
    - Ensure all `AV_*` fields are mapped into `funda_map`.

## Phase 2: UI Manager Refactor
- [x] Modify `myra_app/UI_Manager.py`:
    - Change `draw_dashboard` signature/logic to return the `Layout` object.
    - Remove `console.print(layout)` and `console.clear()`.

## Phase 3: Live UI Integration
- [x] Modify `myra_app/myra.py`:
    - Implement `rich.live.Live` around the main input loop.
    - Update logic to handle continuous background refresh.
    - Remove the initial `show_welcome` if it causes flickering with `Live`.

## Phase 4: Validation
- [ ] Test Strategy 25 on NIFTY 500.
- [ ] Monitor background sync progress in the live footer.
