# Specification: UI Overhaul (Rich-based Console)

## Objective
Modernize the MYRA CLI by replacing the existing print-based menu logic with a sophisticated `rich`-based layout, improving readability, categorization, and system status visibility.

## Requirements
1. **Visual Identity**: Use a `rich.panel.Panel` with a `double` border for the logo and welcome message.
2. **Menu Categorization**: Group the 20+ strategy options into logical categories (e.g., Technicals, Institutional, Experimental, Portfolio) using `rich.columns.Columns` or `rich.table.Table` layouts.
3. **System Status Footer**: Implement a footer that displays:
    - **DuckDB Status**: Connected/Disconnected + File Size.
    - **System Date**: Current date and market session info.
4. **Resilience**: Ensure the new UI degrades gracefully if `rich` is not available (though it is a core dependency).

## Technical Details
- **Primary File**: `myra_app/myra.py`.
- **Key Modules**: `rich.console.Console`, `rich.panel.Panel`, `rich.columns.Columns`, `rich.table.Table`.
- **Data Hook**: Query `Librarian` for DB stats.
