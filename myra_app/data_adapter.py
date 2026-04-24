import logging
import os
import sqlite3
import threading
from typing import Any, Dict

import pandas as pd   # ✅ MUST BE BEFORE ANY FUNCTION USING pd

from myra_core.utils.data_validation import enforce_index_contract, validate_dataframe
import pandas_ta as ta

def get_price_df(
    self, symbol: str, lookback_days: int = 252, as_of_date: str = None
) -> pd.DataFrame:
    """
    Fetches OHLCV + Delivery data with CamelCase formatting.
    HARDENED: Duplicate-safe, index-safe, multiprocessing-safe.
    """
    symbol_clean = symbol.split(".")[0].upper()
    fetch_limit = max(lookback_days, 200) + 20
    cache_key = (symbol_clean, lookback_days, as_of_date)

    with self._lock:
        if cache_key in self._price_cache:
            return self._price_cache[cache_key].copy()

    from .librarian_core import LibrarianCore
    path = os.path.join(self.db_dir, LibrarianCore.DB_MAP["technical"])

    if not os.path.exists(path):
        return pd.DataFrame()

    conn = self._get_connection(path)

    try:
        where = "WHERE symbol = ?"
        params = [symbol_clean]

        if as_of_date:
            where += " AND date <= ?"
            params.append(as_of_date)

        sql = f"""
            SELECT * 
            FROM technical_data
            {where}
            ORDER BY date DESC
            LIMIT {fetch_limit}
        """

        df = pd.read_sql(sql, conn, params=params)

        if df.empty:
            return df

        # =========================
        # 🔥 CRITICAL DATA HARDENING
        # =========================

        # 1. Force datetime
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])

        # 2. REMOVE DUPLICATES (DB-level + runtime-level safety)
        df = df.drop_duplicates(subset=["symbol", "date"], keep="last")

        # 3. SORT properly
        df = df.sort_values(["symbol", "date"])

        # 4. RESET INDEX FIRST (important before set_index)
        df = df.reset_index(drop=True)

        # 5. SET INDEX
        df = df.set_index("date")

        # 6. FINAL DUPLICATE INDEX CHECK (CRITICAL)
        if not df.index.is_unique:
            logging.error(f"[DUPLICATE INDEX DETECTED] {symbol_clean}")
            df = df[~df.index.duplicated(keep="last")]

        # 7. Enforce index contract (your system rule)
        df = enforce_index_contract(df, symbol=symbol_clean)

        # =========================
        # ✅ COLUMN STANDARDIZATION
        # =========================

        rename_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "delivery_pct": "DeliveryPct",
        }

        df.rename(columns=rename_map, inplace=True)

        # Remove duplicate columns if any
        df = df.loc[:, ~df.columns.duplicated()]

        # =========================
        # 🧪 FINAL VALIDATION
        # =========================

        df = validate_dataframe(
            df, context=f"DataAdapter get_price_df: {symbol_clean}"
        )

    except Exception as e:
        logging.error(f"[DATA ADAPTER ERROR] {symbol_clean}: {e}")
        df = pd.DataFrame()

    finally:
        conn.close()

    if df.empty:
        return df

    # =========================
    # 📊 INDICATORS
    # =========================
    df = self.compute_common_indicators(df)

    # =========================
    # 🧠 CACHE
    # =========================
    with self._lock:
        if len(self._price_cache) > 500:
            self._price_cache.clear()
        self._price_cache[cache_key] = df

    return df.copy()
