import logging
import os
import sqlite3
import threading
from typing import Any, Dict

import pandas as pd
import pandas_ta as ta


class DataAdapter:
    """
    MYRA Data Adapter (v3.0) - ATOMIC TRILOGY
    Bridges modular SQLite DBs and Parquet Indicator Lake with Engine/Scanners.
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

    def get_price_df(
        self, symbol: str, lookback_days: int = 252, as_of_date: str = None
    ) -> pd.DataFrame:
        """
        Fetches OHLCV + Delivery data from Technical SQLite DB.
        """
        symbol_clean = symbol.split(".")[0].upper()
        fetch_limit = max(lookback_days, 200) + 20
        cache_key = (symbol_clean, lookback_days, as_of_date)

        with self._lock:
            if cache_key in self._price_cache:
                return self._price_cache[cache_key].copy()

        # Connect directly to SQLite Technical Database
        from .librarian_core import LibrarianCore

        path = os.path.join(self.db_dir, LibrarianCore.DB_MAP["technical"])
        if not os.path.exists(path):
            return pd.DataFrame()

        conn = sqlite3.connect(path)
        try:
            where = "WHERE symbol = ?"
            params = [symbol_clean]
            if as_of_date:
                where += " AND date <= ?"
                params.append(as_of_date)

            sql = f"SELECT * FROM technical_data {where} ORDER BY date DESC LIMIT {fetch_limit}"
            df = pd.read_sql(sql, conn, params=params)
            if not df.empty:
                df = df.sort_values("date")
                df["date"] = pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)
                rename_map = {
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
                df.rename(columns=rename_map, inplace=True)
        except Exception:
            df = pd.DataFrame()
        finally:
            conn.close()

        if df.empty:
            return df

        if "delivery" in df.columns:
            if df["delivery"].isna().any() or (df["delivery"] == 0).all():
                logging.critical(
                    f"Missing or zero delivery data found for {symbol_clean} in the requested window."
                )

        # 2. Lazy Fallback Check & Technical Consistency
        df = self.compute_common_indicators(df)

        with self._lock:
            if len(self._price_cache) > 500:
                try:
                    self._price_cache.pop(next(iter(self._price_cache)))
                except Exception:
                    pass
            self._price_cache[cache_key] = df

        return df.copy()

    def get_technical_history(
        self, symbol: str, days: int = 150, as_of_date: str = None
    ) -> pd.DataFrame:
        """
        Fetches OHLCV + Delivery data from Technical SQLite DB, preserving lowercase schema.
        Properly handles string dates to datetime and sorts them.
        """
        symbol_clean = symbol.split(".")[0].upper()
        fetch_limit = max(days, 200) + 20
        cache_key = (f"{symbol_clean}_tech", days, as_of_date)

        with self._lock:
            if cache_key in self._price_cache:
                return self._price_cache[cache_key].copy()

        from .librarian_core import LibrarianCore

        path = os.path.join(self.db_dir, LibrarianCore.DB_MAP["technical"])
        if not os.path.exists(path):
            return pd.DataFrame()

        conn = sqlite3.connect(path)
        try:
            where = "WHERE symbol = ?"
            params = [symbol_clean]
            if as_of_date:
                where += " AND date <= ?"
                params.append(as_of_date)

            sql = f"SELECT * FROM technical_data {where} LIMIT {fetch_limit}"
            df = pd.read_sql(sql, conn, params=params)

            if not df.empty:
                # Proper conversion logic for date parsing and sorting
                df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")

                # Fallback if the format wasn't mixed (e.g. ISO)
                if df["date"].isna().all():
                     df["date"] = pd.to_datetime(pd.read_sql(sql, conn, params=params)["date"])

                df = df.dropna(subset=["date"]).sort_values("date")
                df.set_index("date", inplace=True)

        except Exception as e:
            logging.error(f"Error fetching technical history for {symbol_clean}: {e}")
            df = pd.DataFrame()
        finally:
            conn.close()

        if df.empty:
            return df

        df = self.compute_common_indicators(df)

        with self._lock:
            if len(self._price_cache) > 500:
                try:
                    self._price_cache.pop(next(iter(self._price_cache)))
                except Exception:
                    pass
            self._price_cache[cache_key] = df

        return df.copy()


    def get_latest_funda(self, symbol: str, df: pd.DataFrame = None) -> Dict[str, Any]:
        """
        Fetches precomputed fundamentals from valuation.db and indicators from Parquet Lake.
        """
        symbol_clean = symbol.split(".")[0].upper()

        with self._lock:
            if symbol_clean in self._funda_cache:
                funda = self._funda_cache[symbol_clean].copy()
            else:
                funda = {"symbol": symbol_clean}

        if len(funda) <= 1:  # Only symbol is present
            # Direct SQL Fetch for valuation.db and meta.db
            from .librarian_core import LibrarianCore

            path = os.path.join(self.db_dir, LibrarianCore.DB_MAP["valuation"])
            meta_path = os.path.join(self.db_dir, LibrarianCore.DB_MAP["meta"])
            if os.path.exists(path):
                conn = sqlite3.connect(path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL;")
                try:
                    res = conn.execute(
                        "SELECT * FROM fundamentals WHERE symbol = ?",
                        (symbol_clean,),
                    ).fetchone()
                    if res:
                        if not self._funda_cols:
                            cursor = conn.execute("PRAGMA table_info('fundamentals')")
                            self._funda_cols = [row[1] for row in cursor.fetchall()]
                        funda.update(dict(zip(self._funda_cols, res)))
                except Exception as e:
                    logging.debug(f"Error fetching fundamentals: {e}")
                finally:
                    conn.close()

            # Fetch Sector from meta.db if not already in funda
            if os.path.exists(meta_path) and (
                not funda.get("sector") or funda.get("sector") == "Unknown"
            ):
                conn_m = sqlite3.connect(meta_path, check_same_thread=False)
                conn_m.execute("PRAGMA journal_mode=WAL;")
                try:
                    m_res = conn_m.execute(
                        "SELECT sector, industry FROM symbols_master WHERE symbol = ?",
                        (symbol_clean,),
                    ).fetchone()
                    if m_res:
                        funda["sector"] = m_res[0] or "Unknown"
                        funda["industry"] = m_res[1] or "Unknown"
                except Exception as e:
                    logging.debug(f"Error fetching sector: {e}")
                finally:
                    conn_m.close()

        # 3. SELF-HEALING: Compute missing metrics from DF
        if df is not None and not df.empty:
            if not funda.get("low_1y") or funda.get("low_1y") == 0:
                funda["low_1y"] = (
                    float(df["Low"].iloc[-252:].min())
                    if len(df) >= 252
                    else float(df["Low"].min())
                )

            if not funda.get("delivery_percent"):
                if "delivery_pct" in df.columns:
                    funda["delivery_percent"] = float(df["delivery_pct"].iloc[-1])
                elif "Delivery_Qty" in df.columns:
                    funda["delivery_percent"] = (
                        (
                            float(df["Delivery_Qty"].iloc[-1])
                            / float(df["Volume"].iloc[-1])
                            * 100
                        )
                        if df["Volume"].iloc[-1] > 0
                        else 0
                    )

        # 4. VALUATION: Compute Graham Number if possible
        if not funda.get("graham_number"):
            eps = funda.get("eps")
            bv = funda.get("book_value")
            if eps and bv and float(eps) > 0 and float(bv) > 0:
                funda["graham_number"] = round(
                    (22.5 * float(eps) * float(bv)) ** 0.5, 2
                )

        with self._lock:
            self._funda_cache[symbol_clean] = funda

        return funda

    def compute_common_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < 20:
            return df
        try:
            missing_ta = []
            if "RSI" not in df.columns:
                missing_ta.append({"kind": "rsi", "length": 14})

            missing_ta.extend(
                [
                    {"kind": "sma", "length": length}
                    for length in [20, 50, 150, 200]
                    if f"sma{length}" not in df.columns
                ]
            )

            if "atr20" not in df.columns:
                missing_ta.append({"kind": "atr", "length": 20})

            if not missing_ta:
                return df

            # Lazy Fallback Warning
            logging.warning(
                f"[MYRA Memory System] Lazy Fallback triggered: Missing indicators {missing_ta} in Parquet. Computing dynamically."
            )

            study = ta.Study(name="CommonIndicators", cores=0, ta=missing_ta)
            df.ta.study(study)

            rename_map = {
                "RSI_14": "RSI",
                "SMA_20": "sma20",
                "SMA_50": "sma50",
                "SMA_150": "sma150",
                "SMA_200": "sma200",
                "ATRr_20": "atr20",
            }
            df.rename(
                columns={k: v for k, v in rename_map.items() if k in df.columns},
                inplace=True,
            )

            return df
        except Exception as e:
            logging.error(f"Dynamic TA calculation failed: {e}")
            return df

    def get_lookback_for_scanner(self, sid: str) -> int:
        deep_memory = [
            "109",
            "110",
            "111",
            "127",
            "126",
            "bottom_hunter",
            "whale_tracker",
            "surpriver_v2",
            "multibagger_early",
        ]
        if sid in deep_memory:
            return 756
        return 252

    def clear_cache(self):
        with self._lock:
            self._price_cache.clear()
            self._funda_cache.clear()

    def get_latest_ias(self, symbol, df=None):
        """Retrieves or calculates the Institutional Activity Score."""
        try:
            from myra_app.ias_manager import IASManager

            ias_mgr = IASManager(db_dir=self.db_dir)
            if df is None:
                # Assuming get_price_df instead of get_ohlcv based on context
                df = self.get_price_df(symbol)
            return ias_mgr.calculate_ias(symbol, df)
        except Exception:
            return 0.0, "ERROR"
