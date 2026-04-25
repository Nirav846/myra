import sqlite3
import pandas as pd
import logging
import os
from myra_app.librarian_core import LibrarianCore


class DataAdapter:
    def __init__(self, db_path=None, librarian=None):
        self.librarian = librarian
        if db_path is not None:
            self.db_path = db_path
        else:
            # Anchor to this file's location so it works regardless of cwd
            _here = os.path.dirname(os.path.abspath(__file__))
            self.db_path = os.path.join(
                _here, "db", LibrarianCore.DB_MAP["technical"]
            )

    def get_lookback_for_scanner(self, strategy_name: str) -> int:
        return 300

    def get_price_df(self, symbol: str, lookback_days: int = 252, as_of_date: str = None) -> pd.DataFrame:
        try:
            conn = sqlite3.connect(self.db_path)

            query = f"""
                SELECT *
                FROM technical_data
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT ?
            """

            df = pd.read_sql(query, conn, params=(symbol, lookback_days))
            conn.close()

            if df.empty:
                return pd.DataFrame()

            # 🔥 STANDARDIZE COLUMN NAMES (CRITICAL FIX)
            df.columns = [col.lower() for col in df.columns]

            # 🔥 RENAME to match ENGINE expectations
            df = df.rename(columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume"
            })

            # 🔥 DATE handling
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])

            # 🔥 SORT properly
            df = df.sort_values("date")

            # 🔥 REMOVE DUPLICATES (CRITICAL FIX)
            df = df.drop_duplicates(subset=["date"], keep="last")

            # 🔥 SET INDEX
            df = df.set_index("date")

            # 🔥 FINAL SAFETY CHECK
            if not df.index.is_unique:
                logging.error(f"[DATA ADAPTER] Duplicate index after cleaning for {symbol}")
                df = df[~df.index.duplicated(keep="last")]

            return df

        except Exception as e:
            logging.error(f"[DATA ADAPTER ERROR] {symbol}: {str(e)}")
            return pd.DataFrame()
