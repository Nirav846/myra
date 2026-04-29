import glob
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta

import pandas as pd

from myra_app.librarian import Librarian
from myra_core.utils.myra_log import myra_log


def mass_backfill(
    db_path=os.path.join("db", "myra_technical.db"),
    missing_csv=os.path.join("data", "missing_data.csv"),
):
    """
    Massive backfill for all symbols in the database.
    Strict Local Source: Reads strictly from local Bhavcopy CSV files.
    """
    print(
        "[MYRA] Initializing Mass Market Backfill (3800 Stocks) via STRICT LOCAL ARCHIVES..."
    )

    lib = Librarian(read_only=True)
    all_symbols = lib.get_all_symbols()

    if not os.path.exists(missing_csv):
        print("[!] missing_data.csv not found. Please run missing_detector.py first.")
        return

    df_missing = pd.read_csv(missing_csv)
    df_missing = df_missing[df_missing["symbol"].isin(all_symbols)]
    unique_missing_dates = df_missing["missing_date"].unique()

    print(f"[*] Found {len(unique_missing_dates)} dates requiring backfill.")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()

    stats = {"processed": 0, "rows": 0, "errors": 0, "skipped": 0}

    archive_dir = "data/Market_Archives"
    if not os.path.exists(archive_dir):
        print(f"[!] {archive_dir} not found.")
        return

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

        # ARMOR: Force upper and strip on the needed symbols list
        raw_symbols_needed = df_missing.loc[
            df_missing["missing_date"] == d_str, "symbol"
        ].tolist()
        symbols_needed = [str(s).strip().upper() for s in raw_symbols_needed]

        if d_str not in date_to_file:
            print(f"\n[!] WARNING: Local CSV missing for date {d_str}. Skipping.")
            stats["skipped"] += 1
            continue

        csv_path = date_to_file[d_str]

        try:
            df = pd.read_csv(csv_path)
            stats[
                "processed"
            ] += 1  # Moved counter up to accurately reflect files touched

            df.columns = df.columns.str.strip().str.upper()

            if "SYMBOL" in df.columns:
                df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()

            if "SERIES" in df.columns:
                df["SERIES"] = df["SERIES"].astype(str).str.strip()
                df = df[df["SERIES"].isin(["EQ", "BE", "SM", "ST", "BZ"])]

            if "DATE1" in df.columns:
                df["DATE1"] = pd.to_datetime(df["DATE1"]).dt.strftime("%Y-%m-%d")
            elif "TIMESTAMP" in df.columns:
                df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"]).dt.strftime(
                    "%Y-%m-%d"
                )

            rename_map = {
                "SYMBOL": "symbol",
                "DATE1": "date",
                "TIMESTAMP": "date",
                "OPEN_PRICE": "open",
                "HIGH_PRICE": "high",
                "LOW_PRICE": "low",
                "CLOSE_PRICE": "close",
                "OPEN": "open",
                "HIGH": "high",
                "LOW": "low",
                "CLOSE": "close",
                "TOTTRDQTY": "volume",
                "TTL_TRD_QNTY": "volume",
                "DELIV_QTY": "delivery",
                "DELIV_PER": "delivery_pct",
            }
            df.rename(columns=rename_map, inplace=True)

            mapped_cols = list(set(rename_map.values()))
            available_cols = [c for c in mapped_cols if c in df.columns]
            df = df[available_cols]

            for col in [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "delivery_pct",
            ]:
                if col not in df.columns:
                    df[col] = None

            # ARMOR: Force upper and strip on the dataframe before matching
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
                df = df[df["symbol"].isin(symbols_needed)]

            if df.empty:
                continue

            for col in [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "delivery_pct",
            ]:
                if col in df.columns:
                    df[col] = (
                        df[col]
                        .astype(str)
                        .str.replace(",", "", regex=False)
                        .str.strip()
                    )
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                else:
                    df[col] = None

            df = df.dropna(subset=["delivery"])
            df = df[df["delivery"] > 1.0]

            if df.empty:
                continue

            if "delivery_pct" not in df.columns or df["delivery_pct"].isnull().all():
                df["delivery_pct"] = (df["delivery"] / df["volume"] * 100).fillna(0)

            df["delivery_ratio"] = (df["delivery"] / df["volume"]).fillna(0)

            if "date" not in df.columns:
                df["date"] = d_str
            else:
                df["date"] = pd.to_datetime(
                    df["date"], errors="coerce", format="mixed"
                ).dt.date.astype(str)
                df["date"] = df["date"].fillna(d_str)

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
                    "delivery_pct",
                    "delivery_ratio",
                ]
            ].values.tolist()

            if records:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO technical_data
                    (symbol, date, open, high, low, close, volume, delivery, delivery_pct, delivery_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )
                stats["rows"] += cursor.rowcount

        except Exception as e:
            print(f"[!] Error processing {csv_path}: {e}")
            stats["errors"] += 1

    if stats["rows"] > 0:
        conn.commit()

    conn.close()
    print("\n" + "=" * 30)
    print("MASS BACKFILL COMPLETE")
    print("=" * 30)
    print(f"Dates Evaluated:   {stats['processed']}")
    print(f"Dates Skipped:     {stats['skipped']}")
    print(f"Valid Rows Added:  {stats['rows']}")
    print(f"Errors:            {stats['errors']}")
    print("=" * 30)


if __name__ == "__main__":
    mass_backfill()
