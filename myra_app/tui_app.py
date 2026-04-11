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
    }
    """

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self.df = df

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Horizontal():
            yield DataTable(cursor_type="row")
            yield Markdown("Select a row to see details here", id="detail-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the DataTable with data from the DataFrame."""
        table = self.query_one(DataTable)

        # Ensure we have data
        if self.df is None or self.df.empty:
            table.add_column("Status")
            table.add_row("No data available")
            return

        # Add columns dynamically
        cols = list(self.df.columns)
        table.add_columns(*cols)

        # Add rows dynamically, preserving correct types for sorting where possible
        for _, row in self.df.iterrows():
            row_vals = []
            for val in row:
                if pd.isna(val):
                    row_vals.append("")
                elif isinstance(val, (int, float)):
                    row_vals.append(val)
                else:
                    row_vals.append(str(val))
            table.add_row(*row_vals)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlighting to update the detail side-panel."""
        table = self.query_one(DataTable)
        # Handle case where table is empty
        if self.df is None or self.df.empty:
            return

        row_data = table.get_row(event.row_key)
        cols = list(self.df.columns)

        # Format the side-panel text dynamically as a Markdown list of key-value pairs
        md_text = "## Fundamental Metrics\n\n"
        for col, val in zip(cols, row_data):
            md_text += f"**{col}**: {val}\n\n"

        # Update the markdown widget
        self.query_one("#detail-panel", Markdown).update(md_text)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Enable column sorting by clicking on headers."""
        table = self.query_one(DataTable)
        table.sort(event.column_key)

if __name__ == "__main__":
    # A simple test stub
    df = pd.DataFrame({
        "Ticker": ["AAPL", "TSLA", "NVDA"],
        "Price": [150, 800, 450],
        "RSI": [55, 75, 40]
    })
    app = MyraDashboard(df)
    # app.run()
