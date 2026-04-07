# Specification: Mission Control UI (Rich Layout)

## Objective
Implement a "Mission Control" style interface for MYRA using the Rich library's `Layout` and `Table` engines. This setup provides a non-scrolling, high-density dashboard.

## Requirements
1. **Header Panel**: A `rich.panel.Panel` with a `bright_magenta` border displaying "M Y R A" and the version.
2. **Tactical Command Grid**: A 4-column `Table.grid` with solid background banners for categories:
    - **Technicals** (Yellow)
    - **Institutional** (Magenta)
    - **ML / EXP** (Cyan)
    - **Value** (Green)
3. **Status Footer**: A `rich.table.Table.grid` displaying:
    - DuckDB Status & Size.
    - Market Breadth.
    - System Date.
4. **Layout Engine**: Use `rich.layout.Layout` to split the screen into header, body, and footer.
5. **Integration**: Refactor `myra_app/myra.py` to use `draw_dashboard()` as the primary interface.

## Technical Details
- **Module**: `myra_app/UI_Manager.py`.
- **Primary Function**: `draw_dashboard()`.
- **Style**: High-density, color-coded categories, double borders for panels.
