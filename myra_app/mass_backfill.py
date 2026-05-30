import glob
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from myra_app.librarian import Librarian
from myra_app.schema_registry import SchemaRegistry
from myra_app.utils.date_parser import parse_bhavcopy_date
from myra_core.utils.myra_log import myra_log


def mass_backfill(
    db_path=os.path.join("db", "myra_technical.db"),
    missing_csv=os.path.join("data", "missing_data.csv"),
    full_mode=False,
):
    """
    Massive backfill for all symbols in the database.
    Strict Local Source: Reads strictly from local Bhavcopy CSV files.
    """
    if full_mode:
        print(
            "[MYRA] Initializing FULL REBUILD from Archive (All Symbols & Dates) via STRICT LOCAL ARCHIVES..."
        )
    else:
        print(
            "[MYRA] Initializing Incremental Backfill (Missing Data Only) via STRICT LOCAL ARCHIVES..."
        )

    lib = Librarian(read_only=True)
    all_symbols = lib.get_all_symbols()

    if full_mode:
        # In full mode, we'll build dates from the archive later
        unique_missing_dates = []
    else:
        if not os.path.exists(missing_csv):
            print(
                "[!] missing_data.csv not found. Please run missing_detector.py first."
            )
            return

        df_missing = pd.read_csv(missing_csv)
        df_missing = df_missing[df_missing["symbol"].isin(all_symbols)]
        unique_missing_dates = df_missing["missing_date"].unique()

        print(f"[*] Found {len(unique_missing_dates)} dates requiring backfill.")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()

    # Create ingestion_rejects table if missing
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_rejects (
            symbol TEXT,
            date TEXT,
            reason TEXT,
            raw_values TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Start transaction for atomicity
    conn.execute("BEGIN")

    stats = {"processed": 0, "rows": 0, "errors": 0, "skipped": 0}

    archive_dir = "data/Market_Archives"
    if not os.path.exists(archive_dir):
        print(f"[!] {archive_dir} not found.")
        conn.close()
        return

    all_csvs = glob.glob(os.path.join(archive_dir, "nse_full_*.csv"))
    date_to_file = {}
    for csv_file in all_csvs:
        basename = os.path.basename(csv_file)
        # Extract the date part from filename (remove 'nse_full_' prefix and '.csv' suffix)
        date_part = basename.replace("nse_full_", "").replace(".csv", "")

        # Use the utility function to parse the date
        iso_date = parse_bhavcopy_date(date_part)

        if iso_date is None:
            print(f"Could not parse date from {basename}, skipping")
            continue

        date_to_file[iso_date] = csv_file

    if full_mode:
        # In full mode, process all dates from the archive, sorted chronologically
        unique_missing_dates = sorted(date_to_file.keys())
        print(
            f"[*] Found {len(unique_missing_dates)} dates in archive for full rebuild."
        )

    total_dates = len(unique_missing_dates)

    for idx, d_str in enumerate(unique_missing_dates, 1):
        myra_log(idx, total_dates, desc=f"Backfilling {d_str}")

        if full_mode:
            # In full mode, skip symbol filtering - keep all valid symbols
            symbols_needed = None
        else:
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

            # Convert all column names to lower case first
            df.columns = [c.lower() for c in df.columns]
            # Then strip whitespace
            df.columns = df.columns.str.strip()

            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.strip()

            if "series" in df.columns:
                df["series"] = df["series"].astype(str).str.strip()
                df["series"] = df["series"].str.upper()
                df = df[df["series"].isin(["EQ", "BE", "SM", "ST", "BZ"])]

            if "date1" in df.columns:
                df["date1"] = pd.to_datetime(df["date1"]).dt.strftime(
                    "%Y-%m-%d"
                )  # noqa: PG-STRFTIME
            elif "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(
                    df["timestamp"]
                ).dt.strftime(  # noqa: PG-STRFTIME
                    "%Y-%m-%d"
                )

            # Dynamic column renaming using project's schema registry
            rename_cols = {}
            for col in df.columns:
                canonical = SchemaRegistry.get_canonical_column(col)
                if canonical:
                    rename_cols[col] = canonical
            df.rename(columns=rename_cols, inplace=True)

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
                "vwap",
            ]:
                if col not in df.columns:
                    df[col] = None

            # ARMOR: Force upper and strip on the dataframe before matching
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
                if symbols_needed is not None:
                    # Incremental mode: filter to only needed symbols
                    df = df[df["symbol"].isin(symbols_needed)]
                # Full mode: keep all symbols (no filtering)

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
                "vwap",
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

            # Row validation before insertion
            def validate_row(row):
                reasons = []
                
                # Check open/high/low/close > 0
                for col in ['open', 'high', 'low', 'close']:
                    if col in row and (pd.isna(row[col]) or float(row[col]) <= 0):
                        reasons.append(f"{col} <= 0")
                
                # Check volume > 0
                if 'volume' in row and (pd.isna(row['volume']) or int(row['volume']) <= 0):
                    reasons.append("volume <= 0")
                
                # Check delivery between 0 and volume
                if 'delivery' in row and 'volume' in row:
                    if not pd.isna(row['delivery']) and not pd.isna(row['volume']):
                        delivery_val = float(row['delivery'])
                        volume_val = int(row['volume'])
                        if delivery_val < 0 or delivery_val > volume_val:
                            reasons.append("delivery out of range [0, volume]")
                
                return reasons
            
            # Filter out invalid rows and log rejects
            valid_rows = []
            reject_rows = []
            
            for _, row in df.iterrows():
                reject_reasons = validate_row(row)
                if reject_reasons:
                    # Log reject to ingestion_rejects table
                    raw_values = {col: row[col] for col in ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'delivery'] if col in row}
                    cursor.execute(
                        "INSERT INTO ingestion_rejects (symbol, date, reason, raw_values) VALUES (?, ?, ?, ?)",
                        (row.get('symbol', ''), row.get('date', ''), '; '.join(reject_reasons), str(raw_values))
                    )
                    reject_rows.append(row)
                else:
                    valid_rows.append(row)
            
            if reject_rows:
                conn.commit()
                print(f"  [REJECTED] {len(reject_rows)} invalid rows skipped and logged")
            
            # Replace df with valid rows only
            if valid_rows:
                df = pd.DataFrame(valid_rows)
            else:
                df = pd.DataFrame()  # Empty if all rows rejected
                print(f"  [REJECTED] All rows invalid, skipping file")
                continue

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
                    "vwap",
                ]
            ].values.tolist()

            if records:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO technical_data
                    (symbol, date, open, high, low, close, volume, delivery, delivery_pct, delivery_ratio, vwap)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )
                stats["rows"] += cursor.rowcount

        except Exception as e:
            print(f"[!] Error processing {csv_path}: {e}")
            stats["errors"] += 1
            # Continue processing other dates rather than failing completely

    if stats["rows"] > 0:
        conn.commit()
    else:
        # Still need to end the transaction even if no rows were inserted
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
    # Check for --full flag
    full_mode = "--full" in sys.argv
    mass_backfill(full_mode=full_mode)
