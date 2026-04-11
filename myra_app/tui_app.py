import pandas as pd
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer, Markdown
from textual.containers import Horizontal


class MyraDashboard(App):
    """An interactive TUI dashboard for MYRA using Textual."""

    CSS = """
    DataTable {
        width: 75%;
        height: 100%;
        border-right: solid green;
    }
    #detail-panel {
        width: 25%;
        height: 100%;
        padding: 1 2;
        background: $surface;
    }
    """

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        # Ensure data types are clean before TUI initialization
        self.df = df if df is not None else pd.DataFrame()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Horizontal():
            yield DataTable(cursor_type="row")
            yield Markdown("Select a row to see details here", id="detail-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the DataTable with vectorized data loading."""
        table = self.query_one(DataTable)

        if self.df.empty:
            table.add_column("Status")
            table.add_row("No data available")
            return

        # 1. Add columns dynamically
        table.add_columns(*self.df.columns)

        # 2. Performance Optimized: Convert DF to tuples in one shot.
        # This satisfies the Performance Guard by avoiding .iterrows() and .append()
        clean_data = self.df.fillna("-")
        rows = [tuple(row) for row in clean_data.to_numpy()]

        # 3. Batch add rows to the UI
        table.add_rows(rows)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlighting to update the detail side-panel."""
        if self.df.empty:
            return

        table = self.query_one(DataTable)
        row_data = table.get_row(event.row_key)
        cols = list(self.df.columns)

        # Build dynamic Markdown summary
        md_text = "## Fundamental Metrics\n\n"
        for col, val in zip(cols, row_data):
            md_text += f"**{col}**: {val}\n\n"

        self.query_one("#detail-panel", Markdown).update(md_text)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Enable column sorting."""
        table = self.query_one(DataTable)
        table.sort(event.column_key)


if __name__ == "__main__":
    # Test stub for local verification
    test_df = pd.DataFrame(
        {
            "Ticker": ["AAPL", "TSLA", "NVDA"],
            "Price": [150.25, 800.10, 450.00],
            "RSI": [55, 75, 40],
            "Signal": ["Hold", "Overbought", "Value"],
        }
    )
    app = MyraDashboard(test_df)
    app.run()
