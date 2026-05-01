import argparse
import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd

from myra_app.fetcher import DataFetcher
from myra_app.librarian_core import LibrarianCore

# Fix path - Ensures MYRA can find its internal modules
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def get_db_conn():
    db_path = os.path.join(PROJECT_ROOT, "db", LibrarianCore.DB_MAP["technical"])
    if not os.path.exists(db_path):
        print(f"[!] Critical Error: Database not found at {db_path}")
        sys.exit(1)
    return sqlite3.connect(db_path)


def identify_gaps(symbol, conn):
    """
    Identifies all dates for a given symbol where delivery data is missing or zero.
    """
    query = """
        SELECT date 
        FROM technical_data 
        WHERE symbol = ? AND (delivery IS NULL OR delivery = 0)
        ORDER BY date
    """
    cursor = conn.cursor()
    cursor.execute(query, (symbol,))
    results = cursor.fetchall()
    # Extract dates from tuples
    return [row[0] for row in results]


def fetch_and_patch(symbol):
    """
    Fetches missing delivery data for a given symbol and updates the database.
    """
    conn = get_db_conn()
    missing_dates = identify_gaps(symbol, conn)

    if not missing_dates:
        print(f"[*] No missing delivery data found for {symbol}.")
        conn.close()
        return

    print(f"[*] Found {len(missing_dates)} missing dates for {symbol}. Fetching...")

    fetcher = DataFetcher()
    records_to_update = []

    for date_str in missing_dates:
        try:
            # Parse date string to datetime.date
            current_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            # Fetch data using existing DataFetcher
            result = fetcher.fetch_ohlcv_delivery(current_date)

            # Unpack if a tuple is returned (DataFrame, source_name)
            df = None
            if isinstance(result, tuple):
                df = result[0]
            else:
                df = result

            if df is not None and not isinstance(df, str) and not df.empty:
                # Assuming 'symbol' or 'SYMBOL' column exists
                sym_col = (
                    "symbol"
                    if "symbol" in df.columns
                    else ("SYMBOL" if "SYMBOL" in df.columns else None)
                )
                if sym_col:
                    sym_data = df[df[sym_col] == symbol]
                    if not sym_data.empty:
                        # Extract delivery data. Fallback to common column names
                        deliv_col = None
                        for col in [
                            "delivery",
                            "DELIVERY",
                            "Deliverable Volume",
                            "DELIVERABLE VOLUME",
                            "deliv_qty",
                        ]:
                            if col in sym_data.columns:
                                deliv_col = col
                                break

                        deliv_pct_col = None
                        for col in [
                            "delivery_pct",
                            "DELIVERY_PCT",
                            "% Deli. Qty to Traded Qty",
                            "DELIVERY_PER",
                            "deliv_per",
                        ]:
                            if col in sym_data.columns:
                                deliv_pct_col = col
                                break

                        if deliv_col:
                            delivery_val = float(sym_data[deliv_col].values[0])
                            delivery_pct_val = (
                                float(sym_data[deliv_pct_col].values[0])
                                if deliv_pct_col
                                else None
                            )

                            if delivery_val > 0:
                                records_to_update.append(  # noqa: PG-APPEND
                                    (delivery_val, delivery_pct_val, symbol, date_str)
                                )
        except Exception as e:
            print(f"    - Error fetching {date_str}: {e}")

    # Batch update
    if records_to_update:
        print(
            f"[*] Prepared {len(records_to_update)} updates. Applying batch update..."
        )
        try:
            cursor = conn.cursor()
            cursor.executemany(
                "UPDATE technical_data SET delivery = ?, delivery_pct = ? WHERE symbol = ? AND date = ?",
                records_to_update,
            )
            conn.commit()
            print(
                f"[+] Successfully patched {len(records_to_update)} records for {symbol}."
            )
        except Exception as e:
            print(f"[!] Error updating database for {symbol}: {e}")
            conn.rollback()
    else:
        print(f"[-] No valid delivery data recovered for {symbol}.")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill missing delivery data for a given symbol."
    )
    parser.add_argument(
        "--symbol",
        type=str,
        required=True,
        help="The stock symbol to backfill (e.g., RELIANCE)",
    )

    args = parser.parse_args()
    fetch_and_patch(args.symbol)
