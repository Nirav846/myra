# Specification: Finalize Responsive Dashboard

## Objective
Enhance the "Mission Control" UI to be fully responsive across all terminal sizes, restore the complete set of MYRA strategies into the tactical grid, and polish the layout with consistent padding and stretching.

## Requirements
1. **Responsive Layout**: Use `Layout(ratio=1)` for the 4-column tactical grid in `UI_Manager.py` to ensure proportional stretching.
2. **Complete Strategy Map**: Restore all 29 options (including Piped Playbooks, BB Squeeze, System Daemons, etc.) into the categorized grid.
3. **UI Polish**:
    - Add `padding` to main panels to prevent edge-touching.
    - Set `expand=True` for Logo and Footer panels.
    - Ensure all panels use the double border style consistently.
4. **Resilience**: The dashboard must remain legible even in narrow terminal windows (horizontal overflow should be handled gracefully by Rich).

## Technical Details
- **Module**: `myra_app/UI_Manager.py`.
- **Primary Function**: `MYRA_UI.get_menu_grid()`.
- **Layout Logic**: Replace static widths with ratio-based splits.
