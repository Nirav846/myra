import os
import pandas as pd
import sqlite3
import glob
from datetime import datetime
from myra_core.utils.myra_log import myra_log
from myra_app.librarian_core import LibrarianCore

def ingest_bhavcopies(csv_folder, db_path=None):
    """
    STRICT DELIVERY INGESTION: Rejects any data lacking institutional footprint.
    """
    if db_path is None:
        _myra_app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "myra_app")
        db_path = os.path.join(_myra_app_dir, "db", LibrarianCore.DB_MAP["technical"])

    if not os.path.exists(db_path):
        print(f"[!] Database {db_path} not found.")
        return

    print(f"[MYRA] Starting STRICT ingestion from {csv_folder}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    csv_files = glob.glob(os.path.join(csv_folder, "nse_full_*.csv"))
    stats = {"processed": 0, "inserted": 0, "rejected": 0}

    for i, file_path in enumerate(csv_files, 1):
        myra_log(i, len(csv_files), desc="Ingesting files")
        try:
            df = pd.read_csv(file_path)
            df.columns = [c.strip().upper() for c in df.columns]

            # Filter for Equity Series only
            if "SERIES" in df.columns:
                df = df[df["SERIES"].str.strip().isin(["EQ", "BE", "SM"])]

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
            }
            df = df.rename(
                columns={k: v for k, v in mapping.items() if k in df.columns}
            )

            # 1. Cast numeric fields
            for col in ["open", "high", "low", "close", "volume", "delivery"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # 2. THE GATEKEEPER: Hard reject missing/placeholder delivery
            initial_count = len(df)
            df = df.dropna(subset=["delivery"])
            df = df[df["delivery"] > 1.0]  # Specifically rejects the '1.0' poison
            stats["rejected"] += initial_count - len(df)

            if df.empty:
                continue

            # Date Normalization
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
            df = df.dropna(subset=["date"])

            # Calculate Ratio
            df["delivery_ratio"] = (df["delivery"] / df["volume"]).fillna(0)

            # Insert
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
                    "delivery_ratio",
                ]
            ].values.tolist()
            cursor.executemany(
                "INSERT OR REPLACE INTO technical_data (symbol, date, open, high, low, close, volume, delivery, delivery_ratio) VALUES (?,?,?,?,?,?,?,?,?)",
                records,
            )
            stats["inserted"] += cursor.rowcount
            conn.commit()

        except Exception as e:
            print(f"[!] Error: {e}")

    conn.close()
    print(
        f"\n[+] Done. Inserted: {stats['inserted']}, Rejected (No Delivery): {stats['rejected']}"
    )
