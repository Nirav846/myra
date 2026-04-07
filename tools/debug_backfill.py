import os
import sys
import pandas as pd
import sqlite3
from datetime import datetime
from io import StringIO

# Fix path
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
sys.path.append(PROJECT_ROOT)
from tools.symbol_mapper import SymbolMapper


def debug_dcal_insertion():
    db_path = os.path.join(PROJECT_ROOT, "db", "technical.db")
    archive_dir = os.path.join(PROJECT_ROOT, "data", "Market_Archives")
    mapper = SymbolMapper()

    # We know 2024-03-28 is in our missing list for DCAL
    test_date = "2024-03-28"
    local_file = os.path.join(archive_dir, f"nse_full_{test_date}.csv")

    if not os.path.exists(local_file):
        print(f"[!] Archive for {test_date} missing. Run backfill once first.")
        return

    print(f"[*] Analyzing {local_file} for DCAL...")
    df = pd.read_csv(local_file)
    df.columns = [c.strip().upper() for c in df.columns]

    # Search for any alias of DCAL
    aliases = ["DCAL", "DISHMAN"]
    found = df[df["SYMBOL"].isin(aliases)]

    if found.empty:
        print("[!] DCAL or DISHMAN NOT found in this CSV.")
        # Print first 5 symbols to see format
        print(f"[*] Sample symbols in file: {df['SYMBOL'].head().tolist()}")
    else:
        print(f"[+] Found {len(found)} rows matching aliases.")
        # Fix 40: Use itertuples for performance
        for row in found.itertuples(index=False):
            raw_sym = str(row.SYMBOL).strip().upper()
            current_sym = mapper.get_current_symbol(raw_sym)
            series = getattr(row, "SERIES", "N/A")
            print(f"    - Raw: {raw_sym}, Current: {current_sym}, Series: {series}")

            # Check database
            conn = sqlite3.connect(db_path)
            res = conn.execute(  # noqa: performance
                "SELECT * FROM technical_data WHERE symbol = ? AND date = ?",
                (current_sym, test_date),
            ).fetchone()
            conn.close()
            if res:
                print(
                    f"    - [!] Row already exists in technical.db for {current_sym} on {test_date}"
                )
            else:
                print(
                    f"    - [ ] Row MISSING in technical.db for {current_sym} on {test_date}"
                )


if __name__ == "__main__":
    debug_dcal_insertion()
