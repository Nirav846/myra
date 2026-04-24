import sqlite3
import pandas as pd
import logging


class DataAdapter:
    def __init__(self, db_path="db/myra_technical.db", **kwargs):
        self.db_path = db_path

    def get_price_df(
        self,
        symbol: str,
        lookback_days: int = 252,
        as_of_date: str = None
    ) -> pd.DataFrame:
        try:
            conn = sqlite3.connect(self.db_path)

            query = """
                SELECT *
                FROM technical_data
                WHERE symbol = ?
            """

            if as_of_date:
                query += " AND date <= ?"
                df = pd.read_sql(query, conn, params=(symbol, as_of_date))
            else:
                df = pd.read_sql(query, conn, params=(symbol,))

            conn.close()

            if df.empty:
                return None

            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])

            df = df.drop_duplicates(subset=["symbol", "date"], keep="last")
            df = df.sort_values("date")

            df = df.set_index("date")

            if not df.index.is_unique:
                logging.error(f"[DATA ADAPTER] Duplicate index detected for {symbol}")
                df = df[~df.index.duplicated(keep="last")]

            if lookback_days:
                df = df.tail(lookback_days)

            return df

        except Exception as e:
            logging.exception(f"[DATA ADAPTER ERROR] {symbol} failed")
            return None

    def get_lookback_for_scanner(self, strategy_name: str) -> int:
        return 300
