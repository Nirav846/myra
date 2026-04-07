import os
import pandas as pd
import sqlite3
import glob
from datetime import datetime
from tqdm import tqdm


def ingest_bhavcopies(csv_folder, db_path="db/technical.db", batch_size=5000):
    """
    High-performance ingestion of NSE Bhavcopy CSVs into SQLite.
    Optimized with batch inserts and 'INSERT OR IGNORE'.
    """
    if not os.path.exists(db_path) and os.path.exists(os.path.basename(db_path)):
        db_path = os.path.basename(db_path)

    print(f"[MYRA] Starting ingestion from {csv_folder} to {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Get list of files
    csv_files = glob.glob(os.path.join(csv_folder, "nse_full_*.csv"))
    if not csv_files:
        print(f"[!] No bhavcopy files found in {csv_folder}.")
        return

    stats = {"processed": 0, "inserted": 0, "duplicates": 0, "errors": 0}

    first_file = True
    for file_path in tqdm(csv_files, desc="Ingesting files"):
        try:
            df = pd.read_csv(file_path)

            # Clean column names (strip whitespace)
            df.columns = [c.strip().upper() for c in df.columns]

            if first_file:
                # print(f"DEBUG: Columns in {file_path}: {df.columns.tolist()}")
                # print(f"DEBUG: First 5 SERIES values: {df['SERIES'].head().tolist()}")
                first_file = False

            # Filter relevant series (EQ only)
            if "SERIES" in df.columns:
                df["SERIES"] = df["SERIES"].str.strip()
                df = df[df["SERIES"] == "EQ"]

            # Mapping
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
                "NO_OF_TRADES": "trades",
                "AVG_PRICE": "vwap",
                "DELIV_PER": "delivery_pct",
            }

            # Rename columns based on mapping (if they exist)
            df = df.rename(
                columns={k: v for k, v in mapping.items() if k in df.columns}
            )

            # Ensure all required columns exist
            required = ["symbol", "date", "open", "high", "low", "close", "volume"]
            missing_cols = [col for col in required if col not in df.columns]
            if missing_cols:
                print(
                    f"[!] Skipping file {file_path}. Missing required columns: {missing_cols}"
                )
                continue

            # Convert DATE1 (DD-MMM-YYYY or similar) to YYYY-MM-DD
            # Optimized with vectorized pd.to_datetime (Fix 84, 88: Avoid .apply and .strftime)
            if "date" in df.columns:
                df["date_dt"] = pd.to_datetime(
                    df["date"].astype(str).str.strip(),
                    format="%d-%b-%Y",
                    errors="coerce",
                )
                df["date"] = (
                    df["date_dt"]
                    .dt.date.astype(str)
                    .where(df["date_dt"].notna(), df["date"])
                )
                df.drop(columns=["date_dt"], inplace=True)

            # Drop rows with missing date
            df = df.dropna(subset=["date"])

            # Cast numeric fields BEFORE derived columns
            numeric_cols = [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "trades",
                "vwap",
                "delivery_pct",
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # Drop rows where critical price fields are NaN or <= 0
            critical_prices = ["open", "high", "low", "close"]
            for col in critical_prices:
                if col in df.columns:
                    df = df[df[col].notna()]
                    df = df[df[col] > 0]

            # Derived Column: delivery_ratio
            if "delivery" in df.columns and "volume" in df.columns:
                # Optimized with vectorized division (Fix 108: Avoid .apply)
                df["delivery_ratio"] = (df["delivery"] / df["volume"]).fillna(0)
                df.loc[df["volume"] <= 0, "delivery_ratio"] = 0
            else:
                df["delivery_ratio"] = 0

            # Final numeric cast for all numeric fields including derived
            final_numeric = numeric_cols + ["delivery_ratio"]
            for col in final_numeric:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # Data to Insert
            final_cols = [
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
                "delivery_ratio",
            ]
            # Reorder and handle missing columns with None
            for col in final_cols:
                if col not in df.columns:
                    df[col] = None

            records = df[final_cols].values.tolist()

            # Batch Insert
            cursor.executemany(
                """
                INSERT OR IGNORE INTO technical_data 
                (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                records,
            )

            stats["processed"] += len(df)
            stats["inserted"] += cursor.rowcount
            stats["duplicates"] += len(df) - cursor.rowcount

            conn.commit()

        except Exception as e:
            print(f"[!] Error processing {file_path}: {e}")
            stats["errors"] += 1

    conn.close()

    print("\n" + "=" * 30)
    print("INGESTION SUMMARY")
    print("=" * 30)
    print(f"Files processed: {len(csv_files)}")
    print(f"Rows processed:  {stats['processed']}")
    print(f"Rows inserted:   {stats['inserted']}")
    print(f"Duplicates:      {stats['duplicates']}")
    print(f"Errors:          {stats['errors']}")
    print("=" * 30)


if __name__ == "__main__":
    import sys

    folder = "data/Market_Archives"
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    ingest_bhavcopies(folder)
