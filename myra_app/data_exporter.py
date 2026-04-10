#!/usr/bin/env python
import os
import pandas as pd
import sqlite3
from datetime import datetime
from myra_core.utils.myra_log import myra_log


class DataExporter:
    """
    MYRA Data Exporter - High-Speed Backtesting Utility (v4.0 Alpha)
    Converts SQLite sidecars into Symbol-wise Parquet Lake.
    """

    def __init__(self, db_dir="db", export_dir="data/lake"):
        self.db_dir = db_dir
        self.export_dir = export_dir
        os.makedirs(self.export_dir, exist_ok=True)

    def export_all_to_parquet(self, symbol_list=None):
        """Full conversion of technical.db to Parquet Lake."""
        db_path = os.path.join(self.db_dir, "technical.db")
        if not os.path.exists(db_path):
            print(f"[!] {db_path} not found.")
            return

        conn = sqlite3.connect(db_path)
        if symbol_list is None:
            print("[*] Retrieving symbol list from database...")
            symbols = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT symbol FROM technical_data"
                ).fetchall()
            ]
        else:
            symbols = symbol_list

        print(f"[*] Exporting {len(symbols)} symbols to {self.export_dir}...")

        total_symbols = len(symbols)
        for i, symbol in enumerate(symbols):
            myra_log(i + 1, total_symbols, desc="Exporting")
            try:
                # 1. Fetch full history for symbol
                df = pd.read_sql(
                    "SELECT * FROM technical_data WHERE symbol = ? ORDER BY date ASC",
                    conn,
                    params=(symbol,),
                )
                if df.empty:
                    continue

                # 2. Cleanup & Normalization
                df["date"] = pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)

                # Standardize columns for analytics engines
                df.rename(
                    columns={
                        "open": "Open",
                        "high": "High",
                        "low": "Low",
                        "close": "Close",
                        "volume": "Volume",
                        "delivery": "delivery_qty",
                        "delivery_pct": "delivery_percent",
                    },
                    inplace=True,
                )

                # 3. Save to Parquet
                out_path = os.path.join(self.export_dir, f"{symbol}.parquet")
                df.to_parquet(out_path, compression="snappy")
            except Exception:
                # print(f"Error exporting {symbol}: {e}")
                continue

        conn.close()
        print(f"[✔] Export complete. Lake ready at {self.export_dir}")


if __name__ == "__main__":
    exporter = DataExporter()
    exporter.export_all_to_parquet()
