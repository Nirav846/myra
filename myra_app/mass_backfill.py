import os
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import time
import re
import glob
from myra_core.utils.myra_log import myra_log
from myra_app.librarian import Librarian


def mass_backfill(db_path="technical.db", missing_csv="missing_data.csv"):
    """
    Massive backfill for all symbols in the database.
    Strict Local Source: Reads strictly from local Bhavcopy CSV files.
    """
    print("[MYRA] Initializing Mass Market Backfill (3800 Stocks) via STRICT LOCAL ARCHIVES...")

    lib = Librarian(read_only=True)
    all_symbols = lib.get_all_symbols()

    if not os.path.exists(missing_csv):
        print("[!] missing_data.csv not found. Please run missing_detector.py first.")
        return

    df_missing = pd.read_csv(missing_csv)
    # Only focus on symbols we have in our master list
    df_missing = df_missing[df_missing["symbol"].isin(all_symbols)]

    # We group by date because files are organized by date
    unique_missing_dates = df_missing["missing_date"].unique()

    print(f"[*] Found {len(unique_missing_dates)} dates requiring backfill.")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    stats = {"processed": 0, "rows": 0, "errors": 0, "skipped": 0}

    archive_dir = "data/Market_Archives"
    if not os.path.exists(archive_dir):
        print(f"[!] {archive_dir} not found.")
        return

    # Create a mapping of date to file in the archive dir
    all_csvs = glob.glob(os.path.join(archive_dir, "nse_full_*.csv"))
    date_to_file = {}
    for csv_file in all_csvs:
        basename = os.path.basename(csv_file)
        match = re.search(r"nse_full_(\d{4}-\d{2}-\d{2}|\d{8})\.csv", basename)
        if match:
            date_str = match.group(1)
            if len(date_str) == 8:
                try:
                    file_dt = datetime.strptime(date_str, "%d%m%Y")
                except ValueError:
                    continue
            else:
                try:
                    file_dt = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    continue

            iso_date = file_dt.date().isoformat()
            date_to_file[iso_date] = csv_file

    total_dates = len(unique_missing_dates)

    for idx, d_str in enumerate(unique_missing_dates, 1):
        myra_log(idx, total_dates, desc=f"Backfilling {d_str}")

        # Get missing symbols for this specific date
        symbols_needed = df_missing[df_missing["missing_date"] == d_str]["symbol"].tolist()

        # Look for a local CSV
        if d_str not in date_to_file:
            print(f"\n[!] WARNING: Local CSV missing for date {d_str}. Skipping. DO NOT fallback to API.")
            stats["skipped"] += 1
            continue

        csv_path = date_to_file[d_str]

        try:
            df = pd.read_csv(csv_path)
            # Standardize columns to upper case
            df.columns = [c.strip().upper() for c in df.columns]

            # Filter for Equity Series only
            if "SERIES" in df.columns:
                df = df[df["SERIES"].str.strip().isin(["EQ", "BE", "SM"])]

            # Map columns
            mapping = {
                "SYMBOL": "symbol",
                "DATE1": "date",
                "TIMESTAMP": "date",
                "OPEN_PRICE": "open",
                "OPEN": "open",
                "HIGH_PRICE": "high",
                "HIGH": "high",
                "LOW_PRICE": "low",
                "LOW": "low",
                "CLOSE_PRICE": "close",
                "CLOSE": "close",
                "TTL_TRD_QNTY": "volume",
                "TOTTRDQTY": "volume",
                "DELIV_QTY": "delivery",
                "DELIVERY_QTY": "delivery",
                "DELIV_PER": "delivery_pct",
                "DELIVERY_PCT": "delivery_pct",
                "NO_OF_TRADES": "trades",
            }
            df = df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})

            # Filter rows for symbols we need
            df = df[df["symbol"].isin(symbols_needed)]

            if df.empty:
                continue

            # Check for necessary columns
            for col in ["open", "high", "low", "close", "volume", "delivery"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                else:
                    df[col] = None

            # Enforce delivery metrics
            initial_count = len(df)
            df = df.dropna(subset=["delivery"])
            df = df[df["delivery"] > 1.0]

            if df.empty:
                continue

            if "delivery_pct" in df.columns:
                df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")
            else:
                df["delivery_pct"] = (df["delivery"] / df["volume"] * 100).fillna(0)

            df["delivery_ratio"] = (df["delivery"] / df["volume"]).fillna(0)

            # Ensure date column exists and is populated
            if "date" not in df.columns:
                df["date"] = d_str
            else:
                # Use format='mixed' to avoid UserWarning from pandas and ensure consistent parsing
                df["date"] = pd.to_datetime(df["date"], errors="coerce", format='mixed').dt.date.astype(str)
                df["date"] = df["date"].fillna(d_str)

            # Optional columns setup for executemany
            for col in ["trades", "vwap"]:
                if col not in df.columns:
                    df[col] = None

            records = df[
                [
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "delivery",
                    "trades",
                    "vwap",
                    "delivery_pct",
                    "delivery_ratio"
                ]
            ].values.tolist()

            if records:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO technical_data
                    (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )
                conn.commit()
                stats["rows"] += cursor.rowcount

            stats["processed"] += 1

        except Exception as e:
            print(f"[!] Error processing {csv_path}: {e}")
            stats["errors"] += 1

    conn.close()
    print("\n" + "=" * 30)
    print("MASS BACKFILL COMPLETE")
    print("=" * 30)
    print(f"Dates Processed:   {stats['processed']}")
    print(f"Dates Skipped:     {stats['skipped']}")
    print(f"Rows Added:        {stats['rows']}")
    print(f"Errors:            {stats['errors']}")
    print("=" * 30)


if __name__ == "__main__":
    mass_backfill()
