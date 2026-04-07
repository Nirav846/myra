# myra_app/fundamental_manager.py
import sqlite3
import os
import sys
import pandas as pd
from datetime import datetime, date, timedelta
from .data_sources import RateLimiter, SourceManager, normalize


class FundamentalManager:
    """
    MYRA Fundamental Manager - The Orchestration Layer for Fundamentals (v3.0)
    TRILOGY ERA: Orchestrates ingest into Modular SQLite sidecars.
    """

    def __init__(self, db_dir=None, fetcher=None):
        # 2. Implementation: The Absolute Path Anchor
        if db_dir is None:
            # Anchor to project root regardless of where the script is called from
            BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.db_dir = os.path.join(BASE_DIR, "db")
        else:
            self.db_dir = db_dir

        self.fetcher = fetcher
        self.conn = None  # Legacy/Compatibility Connection
        self.limiter = RateLimiter(rate_per_sec=2)
        self.source_manager = SourceManager()

        # Connections for sidecars
        self.meta_db = os.path.join(self.db_dir, "meta.db")
        self.val_db = os.path.join(self.db_dir, "valuation.db")

    def set_connection(self, conn):
        """Legacy support for Librarian."""
        self.conn = conn

    def set_fetcher(self, fetcher):
        """Legacy support for Librarian."""
        self.fetcher = fetcher

    def _get_val_conn(self):
        if self.conn:
            return self.conn
        return sqlite3.connect(self.val_db, timeout=20)

    def _get_meta_conn(self):
        # meta.db is usually separate, but if self.conn is provided and it's a combined DB (legacy), we use it.
        # However, in v3.0, meta.db is its own sidecar.
        return sqlite3.connect(self.meta_db, timeout=20)

    def is_stale(self, symbol, days=90):
        """
        PKScreener Superpower: Smart Stale Detection.
        Checks the sync marker in meta.db (Atomic Standard).
        """
        symbol_clean = symbol.split(".")[0].upper()
        try:
            conn = self._get_meta_conn()
            res = conn.execute(
                "SELECT last_fundamental_update FROM symbols_master WHERE symbol = ?",
                (symbol_clean,),
            ).fetchone()
            conn.close()

            if not res or not res[0] or res[0] == "1900-01-01":
                return True

            last_date_str = res[0]
            try:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
            except ValueError:
                # Handle cases with time if any
                last_date = datetime.strptime(
                    last_date_str.split(" ")[0], "%Y-%m-%d"
                ).date()

            return (date.today() - last_date).days > days
        except Exception:
            return True

    def _fetch_raw_data(self, symbol):
        """[INGESTOR] Pulls raw JSON/CSV from data sources."""
        if not self.fetcher:
            return None

        # DataFetcher handles its own internal prioritisation and returns (data, source_name)
        raw_data, source_name = self.fetcher.fetch_fundamentals(symbol)

        if raw_data:
            # We still normalize here using our unified schema
            data = normalize(raw_data, source_name)
            self.source_manager.mark_success(source_name)
            return data
        else:
            self.source_manager.mark_failure("screener", is_rate_limit=True)
            return None

    def fetch_fundamentals(self, symbol):
        """
        [MAPPER] Pulls data and splits it into quarterly_results and fundamentals summary.
        Renamed to fetch_fundamentals as per v3.0 requirements.
        """
        data = self._fetch_raw_data(symbol)
        if not data:
            return False

        symbol_clean = symbol.split(".")[0].upper()

        # 1. Update Detailed Quarterly Table (MAPPER)
        v_conn = self._get_val_conn()
        try:
            # Modular Schema: Map fields to quarterly_results
            cols = [
                "symbol",
                "report_date",
                "period_end",
                "revenue",
                "net_profit",
                "eps",
                "opm_pct",
            ]
            placeholders = ", ".join(["?" for _ in cols])
            col_names = ", ".join(cols)
            query = f"INSERT OR REPLACE INTO quarterly_results ({col_names}) VALUES ({placeholders})"

            # Optimized with executemany (Fix 115: Avoid execute in loop)
            records = [[symbol_clean] + [row.get(c) for c in cols[1:]] for row in data]
            if records:
                v_conn.executemany(query, records)

            # 2. Update Main Fundamentals Summary Table (MAPPER)
            latest = data[0]
            prev = data[1] if len(data) > 1 else None

            pe = latest.get("stock_pe") or latest.get("pe")
            roe = latest.get("roe")
            eps = latest.get("eps")
            bv = latest.get("book_value")
            mcap = latest.get("market_cap")

            # Calculate Growth Metrics
            profit_growth = 0
            sales_growth = 0
            if prev:
                l_profit = latest.get("net_profit")
                p_profit = prev.get("net_profit")
                if l_profit and p_profit and p_profit != 0:
                    profit_growth = round(
                        ((l_profit - p_profit) / abs(p_profit)) * 100, 2
                    )

                l_rev = latest.get("revenue")
                p_rev = prev.get("revenue")
                if l_rev and p_rev and p_rev != 0:
                    sales_growth = round(((l_rev - p_rev) / p_rev) * 100, 2)

            v_conn.execute(
                """
                INSERT INTO fundamentals (symbol, pe, roe, eps, book_value, market_cap, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    pe = EXCLUDED.pe,
                    roe = EXCLUDED.roe,
                    eps = EXCLUDED.eps,
                    book_value = EXCLUDED.book_value,
                    market_cap = COALESCE(EXCLUDED.market_cap, fundamentals.market_cap),
                    last_updated = EXCLUDED.last_updated
            """,
                (symbol_clean, pe, roe, eps, bv, mcap, date.today().isoformat()),
            )

            v_conn.commit()

            # 3. Update Sync Marker in meta.db [LIBRARIAN]
            m_conn = self._get_meta_conn()
            try:
                m_conn.execute(
                    "UPDATE symbols_master SET last_fundamental_update = ? WHERE symbol = ?",
                    (date.today().isoformat(), symbol_clean),
                )
                m_conn.commit()
            finally:
                m_conn.close()

            return True
        except Exception:
            return False
        finally:
            v_conn.close()

    def get_bulk_f_scores(self, symbols):
        """Calculates F-Scores for multiple symbols using a single query."""
        if not symbols:
            return {}
        symbol_cleans = [s.split(".")[0].upper() for s in symbols]
        try:
            conn = self._get_val_conn()
            placeholders = ",".join(["?"] * len(symbol_cleans))
            query = f"""
                SELECT symbol, report_date, net_profit
                FROM (
                    SELECT symbol, report_date, net_profit,
                           ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY report_date DESC) as rn
                    FROM quarterly_results
                    WHERE symbol IN ({placeholders})
                )
                WHERE rn <= 4
            """
            df = pd.read_sql(query, conn, params=symbol_cleans)
            conn.close()

            scores = {}
            for sym in symbol_cleans:
                sym_df = df[df["symbol"] == sym]
                if len(sym_df) < 2:
                    scores[sym] = 0
                    continue
                score = 0
                latest = sym_df.iloc[0]
                prev = sym_df.iloc[1]
                if latest.get("net_profit", 0) > 0:
                    score += 1
                if latest.get("net_profit", 0) > prev.get("net_profit", 0):
                    score += 1
                scores[sym] = score
            return scores
        except Exception:
            # print("Bulk F-Score Exception:", e)
            return {}

    def calculate_f_score(self, symbol):
        """Piotroski F-Score implementation for Trilogy DB."""
        symbol_clean = symbol.split(".")[0].upper()
        try:
            conn = self._get_val_conn()
            df = pd.read_sql(
                "SELECT * FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC LIMIT 4",
                conn,
                params=[symbol_clean],
            )
            conn.close()

            if len(df) < 2:
                return 0
            score = 0
            latest = df.iloc[0]
            prev = df.iloc[1]

            if latest.get("net_profit", 0) > 0:
                score += 1
            if latest.get("net_profit", 0) > prev.get("net_profit", 0):
                score += 1

            # Ratio checks... (Simplified for v3.0 core)
            return score
        except Exception:
            return 0

    def get_bulk_valuation_metrics(self, symbols):
        """Calculates Graham Number for multiple symbols using a single query."""
        if not symbols:
            return {}
        symbol_cleans = [s.split(".")[0].upper() for s in symbols]
        try:
            conn = self._get_val_conn()
            placeholders = ",".join(["?"] * len(symbol_cleans))
            query = f"""
                SELECT symbol, eps, book_value
                FROM (
                    SELECT symbol, eps, book_value,
                           ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY report_date DESC) as rn
                    FROM quarterly_results
                    WHERE symbol IN ({placeholders})
                )
                WHERE rn = 1
            """
            rows = conn.execute(query, symbol_cleans).fetchall()
            conn.close()

            metrics = {}
            for sym, eps, bv in rows:
                if eps and bv and eps > 0 and bv > 0:
                    graham = (22.5 * eps * bv) ** 0.5
                    metrics[sym] = {"graham_number": round(graham, 2)}
                else:
                    metrics[sym] = {}
            return metrics
        except Exception:
            return {}

    def get_valuation_metrics(self, symbol):
        """Calculates Graham Number from Trilogy DB."""
        symbol_clean = symbol.split(".")[0].upper()
        try:
            conn = self._get_val_conn()
            res = conn.execute(
                "SELECT eps, book_value FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC LIMIT 1",
                (symbol_clean,),
            ).fetchone()
            conn.close()

            if not res or not res[0] or not res[1]:
                return {}
            eps, bv = res
            if eps > 0 and bv > 0:
                graham = (22.5 * eps * bv) ** 0.5
                return {"graham_number": round(graham, 2)}
        except Exception:
            pass
        return {}
