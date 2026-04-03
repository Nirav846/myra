# Implementation Plan: Responsive Live Layout & UI_Manager

## Phase 1: Create UI_Manager.py
- [ ] Implement `get_responsive_layout(lib, breadth, forecast)` function.
- [ ] Implement `draw_dashboard()` to render the layout.
- [ ] Define the `4-column` Table for option categorization.
- [ ] Add category header styling (colors specified in spec).

## Phase 2: Refactor myra.py
- [ ] Integrate `console.screen()` context manager.
- [ ] Move loop logic into the screen context.
- [ ] Replace `show_welcome` and manual menu calls with `UI_Manager.draw_dashboard()`.
- [ ] Verify all 29 options route correctly after refactor.

## Phase 3: Validation & Polish
- [ ] Test on different terminal widths (80, 100, 120+ chars).
- [ ] Verify color rendering for all category headers.
- [ ] Ensure `KeyboardInterrupt` (Ctrl+C) works correctly within the screen context.
