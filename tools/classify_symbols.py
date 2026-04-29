import os
import sqlite3
import sys

import pandas as pd
from rich.console import Console
from rich.progress import track

# Add current dir to path for imports
sys.path.append(os.getcwd())

from PKNSETools.morningstartools import Security

from myra_app.librarian_core import LibrarianCore

console = Console()


def classify_all():
    db_path = os.path.join(os.getcwd(), "db", LibrarianCore.DB_MAP["meta"])
    if not os.path.exists(db_path):
        console.print(f"[red][!] Metadata DB not found at {db_path}[/]")
        return

    conn = sqlite3.connect(db_path)
    symbols = [
        r[0] for r in conn.execute("SELECT symbol FROM symbols_master").fetchall()
    ]

    console.print(f"🚀 Starting Bulk Classification for {len(symbols)} symbols...")

    def _classify_one(symbol):
        try:
            # Level 1: Morningstar Engine
            sec = Security(symbol)
            asset_type = getattr(sec, "asset_type", "stock").upper()

            # Map Morningstar types to MYRA types
            final_type = "EQUITY"
            if asset_type == "ETF":
                final_type = "ETF"
            elif asset_type == "FUND":
                final_type = "FUND"

            # Level 3: Manual Keyword Overrides (SDL, GSEC, etc.)
            noise_keywords = ["LIQUID", "SDL", "GSEC", "CASH"]
            if any(k in symbol for k in noise_keywords):
                final_type = "ETF"  # Treat SDL/GSEC as non-equity

            return (final_type, symbol)
        except Exception:
            return ("EQUITY", symbol)

    updates = [_classify_one(s) for s in track(symbols, description="Classifying...")]

    if updates:
        console.print(
            f"[yellow][+] Applying {len(updates)} classification updates...[/]"
        )
        conn.executemany(
            "UPDATE symbols_master SET instrument_type = ? WHERE symbol = ?", updates
        )

        # De-activate any non-EQUITY from scan universe
        conn.execute(
            "UPDATE symbols_master SET is_active = 0, in_active_universe = 0 WHERE instrument_type != 'EQUITY'"
        )
        conn.commit()

    conn.close()
    console.print("[bold green][✓] Classification Complete.[/]")


if __name__ == "__main__":
    classify_all()
