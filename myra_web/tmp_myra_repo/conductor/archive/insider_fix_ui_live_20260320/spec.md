# Specification: Insider Fix & Live UI Overhaul

## 1. Goal
Restore results for Strategy 25 (Insider Conviction Radar) by fixing metric population in `engine.py`, and transform the MYRA UI into a truly live experience that updates the background sync status in real-time.

## 2. Requirements
### 2.1 engine.py (Insider Metrics)
- [ ] Calculate `AV_Accel`:
    - Count unique days of buying in the last 60 days.
    - Score: 3 (High: >5 days), 2 (Medium: 3-5 days), 1 (Passive: 1-2 days).
- [ ] Populate `AV_Latest`, `AV_Total`, and `AV_Accel` in the `funda_map` for all symbols.

### 2.2 UI_Manager.py (Return vs Print)
- [ ] Modify `draw_dashboard()` to **return** the `Layout` object instead of calling `console.print()`. This is required for `screen.update()` and `Live.update()`.

### 2.3 myra.py (Live Refresh)
- [ ] Implement `rich.live.Live` in the main loop.
- [ ] Set a refresh interval (e.g., 1 second) to ensure the footer's background sync status updates even while waiting for user input.
- [ ] Ensure `console.input` remains functional within the `Live` context.

## 3. Success Criteria
- [ ] Strategy 25 returns results (verified with symbols like `NCLIND`, `SASKEN`).
- [ ] The footer background sync status percentage climbs in real-time without user input.
- [ ] The UI layout remains stable and responsive.
