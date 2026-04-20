import logging
import os
import sqlite3
import threading
from typing import Any, Dict

import pandas as pd
import pandas_ta as ta


class DataAdapter:
    """
    MYRA Data Adapter (v3.2) - ATOMIC TRILOGY
    Bridges modular SQLite DBs and Parquet Indicator Lake with Engine/Scanners.
    Compliance: Style Guide Line 39 (CamelCase OHLCV).
    """

    _instance = None
    _lock = threading.Lock()
    _price_cache = {}  # (symbol, lookback, as_of_date) -> df
    _funda_cache = {}  # symbol -> funda_dict
    _funda_cols = None

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DataAdapter, cls).__new__(cls)
            return cls._instance

    def __init__(self, librarian=None):
        if not hasattr(self, "initialized"):
            self.librarian = librarian
            self.db_dir = os.path.join(os.getcwd(), "db")
            self.initialized = True

    def _get_connection(self, path: str):
        """Thread-safe connection factory with WAL mode."""
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def get_lookback_for_scanner(self, strategy_name: str) -> int:
        """
        Determines the required lookback period based on the scanner type.
        """
        if not strategy_name:
            return 252

        s_name = str(strategy_name)

        # Multibagger / Long-term setups need deep history
        if s_name == "35" or "Multibagger" in s_name:
            return 756  # 3 years
        
        # RS Scanners need at least a year
        if s_name in ["3", "28", "12"] or "RS" in s_name:
            return 300

        # Default for short-term setups (Delivery Spikes, Breakouts, etc.)
        return 252  # 1 year

    def get_price_df(
        self, symbol: str, lookback_days: int = 252, as_of_date: str = None
    ) -> pd.DataFrame:
        """
        Fetches OHLCV + Delivery data with CamelCase formatting.
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

            # Fix: Added ORDER BY date DESC to ensure we get recent data
            sql = f"SELECT * FROM technical_data {where} ORDER BY date DESC LIMIT {fetch_limit}"
            df = pd.read_sql(sql, conn, params=params)
            
            if not df.empty:
                # Efficient Date Parsing
                df["date"] = pd.to_datetime(df["date"], errors='coerce')
                df = df.dropna(subset=["date"]).sort_values("date")
                df.set_index("date", inplace=True)
                df = df[~df.index.duplicated(keep='last')]
                
                # Compliance: CamelCase Rename
                rename_map = {
                    "open": "Open", "high": "High", "low": "Low",
                    "close": "Close", "volume": "Volume", "delivery_pct": "DeliveryPct"
                }
                df.rename(columns=rename_map, inplace=True)
        except Exception as e:
            logging.error(f"Price fetch failed for {symbol_clean}: {e}")
            df = pd.DataFrame()
        finally:
            conn.close()

        if df.empty: return df

        df = self.compute_common_indicators(df)

        with self._lock:
            if len(self._price_cache) > 500:
                self._price_cache.clear() # Emergency flush
            self._price_cache[cache_key] = df

        return df.copy()

    def get_technical_history(
        self, symbol: str, days: int = 150, as_of_date: str = None
    ) -> pd.DataFrame:
        """
        Refactored bridge for ias_timing_engine.py.
        Ensures date-sorting and CamelCase compliance.
        """
        # We reuse the logic in get_price_df to ensure consistency
        return self.get_price_df(symbol, lookback_days=days, as_of_date=as_of_date)

    def get_latest_funda(self, symbol: str, df: pd.DataFrame = None) -> Dict[str, Any]:
        """
        Fetches precomputed fundamentals with WAL mode enabled.
        """
        symbol_clean = symbol.split(".")[0].upper()

        with self._lock:
            if symbol_clean in self._funda_cache:
                return self._funda_cache[symbol_clean].copy()
        
        funda = {"symbol": symbol_clean}
        from .librarian_core import LibrarianCore

        path = os.path.join(self.db_dir, LibrarianCore.DB_MAP["valuation"])
        if os.path.exists(path):
            conn = self._get_connection(path)
            try:
                res = conn.execute("SELECT * FROM fundamentals WHERE symbol = ?", (symbol_clean,)).fetchone()
                if res:
                    if not self._funda_cols:
                        cursor = conn.execute("PRAGMA table_info('fundamentals')")
                        self._funda_cols = [row[1] for row in cursor.fetchall()]
                    funda.update(dict(zip(self._funda_cols, res)))
            finally:
                conn.close()

        # Self-Healing for 52-Week Lows and Delivery
        if df is not None and not df.empty:
            if not funda.get("low_1y"):
                funda["low_1y"] = float(df["Low"].iloc[-252:].min()) if len(df) >= 252 else float(df["Low"].min())
            if "DeliveryPct" in df.columns:
                funda["delivery_percent"] = float(df["DeliveryPct"].iloc[-1])

        with self._lock:
            self._funda_cache[symbol_clean] = funda
        return funda

    def compute_common_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes missing RSI and SMA levels dynamically."""
        if df.empty or len(df) < 20:
            return df
        try:
            missing_ta = []
            if "RSI" not in df.columns: missing_ta.append({"kind": "rsi", "length": 14})
            
            for length in [20, 50, 150, 200]:
                if f"sma{length}" not in df.columns:
                    missing_ta.append({"kind": "sma", "length": length})

            if not missing_ta: return df

            study = ta.Study(name="CommonIndicators", cores=0, ta=missing_ta)
            df.ta.study(study)

            rename_map = {
                "RSI_14": "RSI", "SMA_20": "sma20", "SMA_50": "sma50",
                "SMA_150": "sma150", "SMA_200": "sma200", "ATRr_20": "atr20"
            }
            df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)
            return df
        except Exception as e:
            logging.error(f"TA calculation failed: {e}")
            return df

    def clear_cache(self):
        with self._lock:
            self._price_cache.clear()
            self._funda_cache.clear()
