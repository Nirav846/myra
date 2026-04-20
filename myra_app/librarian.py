#!/usr/bin/env python
"""
MYRA Librarian - The Data Acquisition Layer (STABLE v3.0 - ATOMIC)
Facade class routing to modular SQLite Sidecars.
EXCLUSIVE GATEKEEPER for all MYRA data.
"""
import os
import pandas as pd
from rich.console import Console

from myra_app.fetcher import DataFetcher
from myra_app.data_loader import StockDataLoader
from myra_app.fundamental_manager import FundamentalManager
from myra_app.index_engine import IndexEngine

from myra_app.librarian_core import LibrarianCore
from myra_app.librarian_schema import LibrarianSchemaMixin
from myra_app.librarian_sync import LibrarianSyncMixin
from myra_app.librarian_intelligence import LibrarianIntelligenceMixin
from myra_app.librarian_ingestor import LibrarianIngestorMixin


class Librarian(
    LibrarianCore,
    LibrarianSchemaMixin,
    LibrarianSyncMixin,
    LibrarianIntelligenceMixin,
    LibrarianIngestorMixin,
):
    """
    Facade class routing queries to the Atomic Trilogy DB stack.
    """

    def __init__(self, read_only=False, console=None, db_path=None):
        self.data_dir = os.path.join(os.getcwd(), "data")
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        super().__init__(read_only=read_only, console=console, db_path=db_path)

        self.fetcher = DataFetcher()
        self.loader = StockDataLoader()
        self.fundamental_manager = FundamentalManager(fetcher=self.fetcher)
        self.index_engine = IndexEngine()

        # Connect is called in LibrarianCore.__init__
        self.fundamental_manager.set_connection(self._val_conn)

        self._funda_cols = None  # Cache for fundamentals columns
        from myra_app.fundamental_ranker import FundamentalRanker

        self.fundamental_ranker = FundamentalRanker(
            self._val_conn,
            scoring_db_path=os.path.join(os.getcwd(), "db", self.DB_MAP["scoring"]),
        )

        if not self.read_only:
            self._create_tables()
            self._migrate_schema()

    def get_market_holidays(self, year):
        import json, os, datetime
        from myra_app.fetcher import DataFetcher

        cache_file = os.path.join(
            os.getcwd(), ".jules", "cache", f"holidays_{year}.json"
        )
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    return set(json.load(f))
            except Exception:
                pass

        fetcher = DataFetcher()
        holidays = set()

        # 1. Primary: NSE
        try:
            r = fetcher.session.get(
                "https://www.nseindia.com/api/holiday-master?type=trading",
                headers={
                    "Referer": "https://www.nseindia.com/",
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=10,
            )
            if getattr(r, "status_code", 0) == 200:
                data = r.json()
                for h in data.get("CM", []):
                    try:
                        d = (
                            datetime.datetime.strptime(h["tradingDate"], "%d-%b-%Y")
                            .date()
                            .isoformat()
                        )
                        if d.startswith(str(year)):
                            holidays.add(d)
                    except:
                        pass
        except Exception:
            pass

        # 2. Fallback: BSE
        if not holidays:
            try:
                import urllib.request

                req = urllib.request.Request(
                    "https://api.bseindia.com/BseIndiaAPI/api/HolidayTrading/w?flag=Trading",
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as res:
                    data = json.loads(res.read().decode()).get("Table", [])
                    for h in data:
                        try:
                            d = (
                                datetime.datetime.strptime(h["TradingDate"], "%d %b %Y")
                                .date()
                                .isoformat()
                            )
                            if d.startswith(str(year)):
                                holidays.add(d)
                        except:
                            pass
            except Exception:
                pass

        # 3. Final Fallback: PKDateUtilities
        if not holidays:
            from myra_core.date_utils import PKDateUtilities

            _, dates = PKDateUtilities.holidayList()
            if dates:
                holidays = {d for d in dates if d.startswith(str(year))}

        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(list(holidays), f)
        return holidays

    def get_expected_trading_day(self, now=None):
        import datetime, pandas as pd

        if not now:
            now = datetime.datetime.now()

        target = now.date()
        is_after_market = now.hour > 18 or (now.hour == 18 and now.minute >= 30)
        if not is_after_market:
            target -= datetime.timedelta(days=1)

        holidays = self.get_market_holidays(target.year)

        while target.weekday() >= 5 or target.isoformat() in holidays:
            target -= datetime.timedelta(days=1)
            if target.year not in [int(d[:4]) for d in holidays]:
                holidays.update(self.get_market_holidays(target.year))

        # If bhavcopy date exists, use it as ground truth if it's earlier than expected
        last_bhav = self.get_max_price_date()
        if last_bhav:
            try:
                lb_dt = datetime.datetime.strptime(last_bhav, "%Y-%m-%d").date()
                if lb_dt > target:
                    return lb_dt
            except Exception:
                pass

        return target

    def run_integrity_check(self):
        """Runs the TechnicalAudit tool to verify modular DB health."""
        from myra_app.technical_audit import TechnicalAudit

        try:
            tech_db = os.path.join(os.getcwd(), "db", self.DB_MAP["technical"])
            cal_db = os.path.join(os.getcwd(), "db", self.DB_MAP["calendar"])
            audit = TechnicalAudit(tech_db=tech_db, cal_db=cal_db)
            audit.run_audit()
        except Exception as e:
            if hasattr(self, "console"):
                self.console.print(
                    f"[warning][!] Integrity Check Failed: {e}[/warning]"
                )
            else:
                print(f"[warning][!] Integrity Check Failed: {e}")

    # --- Read Operations (Facade Methods) ---

    def get_max_price_date(self):
        if not self._tech_conn:
            return None
        res = self._tech_conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
        return res[0] if res else None

    def get_all_symbols(self):
        if not self._meta_conn:
            return []
        res = self._meta_conn.execute(
            "SELECT DISTINCT symbol FROM symbols_master"
        ).fetchall()
        return [r[0] for r in res]

    def get_index_symbols(self, index_name="NIFTY 50"):
        if not self._meta_conn:
            return []
        res = self._meta_conn.execute(
            "SELECT symbol FROM index_constituents WHERE index_name = ?", (index_name,)
        ).fetchall()
        return [r[0] for r in res]

    def get_active_universe(self):
        if not self._meta_conn:
            return []
        res = self._meta_conn.execute(
            "SELECT symbol FROM symbols_master WHERE in_active_universe = 1"
        ).fetchall()
        return [r[0] for r in res]

    def get_market_regime(self):
        # Market regime will transition to Parquet Indicators in Phase 3
        return "NEUTRAL (Modular Transition)"

    def get_ohlcv(self, symbol, as_of_date=None):
        clean = symbol.split(".")[0].upper()
        df = self.loader.load_from_parquet(clean)

        # 1. Smart Cache Validation (Fix: Staleness Loop)
        db_max = self.get_max_price_date()
        if not df.empty and db_max:
            cache_max = df.index.max().date().isoformat()
            if cache_max < db_max:
                # Cache is stale. Fetch delta from DB.
                try:
                    query = "SELECT * FROM technical_data WHERE symbol = ? AND date > ?"
                    delta = pd.read_sql(
                        query, self._tech_conn, params=(clean, cache_max)
                    )
                    if not delta.empty:
                        delta["date"] = pd.to_datetime(delta["date"])
                        delta.set_index("date", inplace=True)
                        delta.rename(
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
                        delta["Adj Close"] = delta["Close"]
                        # Merge and update cache
                        df = pd.concat([df, delta])
                        df = df[~df.index.duplicated(keep="last")].sort_index()
                        self.loader.save_to_parquet(clean, df)
                except Exception:
                    pass

        if not df.empty:
            # Normalization check for legacy Parquet files
            if "delivery" in df.columns and "delivery_qty" not in df.columns:
                df.rename(columns={"delivery": "delivery_qty"}, inplace=True)
            if as_of_date:
                df = df[df.index <= as_of_date]
            return df

        if not self._tech_conn:
            return None

        try:
            query = "SELECT * FROM technical_data WHERE symbol = ?"
            params = [clean]
            if as_of_date:
                query += " AND date <= ?"
                params.append(as_of_date)
            query += " ORDER BY date ASC"

            res = pd.read_sql(query, self._tech_conn, params=params)

            if not res.empty:
                res["date"] = pd.to_datetime(res["date"])
                res.set_index("date", inplace=True)
                res = res[~res.index.duplicated(keep="last")]
                # Standardize for Intelligence Layer (lowercase_snake_case)
                res.rename(
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
                res["Adj Close"] = res["Close"]
                self.loader.save_to_parquet(clean, res)
                return res
        except Exception:
            pass
        return None

    def get_delivery_data(self, symbol, days=60):
        """Fetches historical price and delivery data for SMC-1 profiling."""
        if not self._tech_conn:
            return pd.DataFrame()
        clean = symbol.split(".")[0].upper()
        try:
            query = """
                SELECT date, open, high, low, close, volume, delivery as delivery_qty, delivery_pct as delivery_percent
                FROM technical_data 
                WHERE symbol = ? 
                ORDER BY date DESC 
                LIMIT ?
            """
            df = pd.read_sql(query, self._tech_conn, params=(clean, days))
            if not df.empty:
                df = df.drop_duplicates(subset=['date'], keep='last')
                return df.sort_values("date")
        except Exception:
            pass
        return pd.DataFrame()

    def get_sector_stats(self):
        if not self._val_conn:
            return {}
        try:
            df = pd.read_sql("SELECT * FROM fundamentals", self._val_conn)
            stats = {}
            for sector, group in df.groupby("sector"):
                # Use local variable to avoid chained indexing detection (Fix 231)
                sector_data = {}
                for c in ["pe", "roe"]:
                    if c in group.columns:
                        sector_data[c] = {
                            "mean": group[c].mean(),
                            "std": group[c].std() or 1,
                        }
                stats[sector] = sector_data
            return stats
        except Exception:
            return {}

    def get_fundamentals(self, symbol):
        if not self._val_conn:
            return {}
        clean = symbol.split(".")[0].upper()
        try:
            res = self._val_conn.execute(
                "SELECT * FROM fundamentals WHERE symbol = ?", (clean,)
            ).fetchone()
            if res:
                # Get column names for dict mapping (Cached)
                if getattr(self, "_funda_cols", None) is None:
                    cursor = self._val_conn.execute("PRAGMA table_info('fundamentals')")
                    self._funda_cols = [
                        row[1] for row in cursor.fetchall()
                    ]  # row[1] is name
                    # Simple fallback if fetchall fails to give names
                    if not self._funda_cols:
                        self._funda_cols = [
                            "symbol",
                            "pe",
                            "roe",
                            "eps",
                            "book_value",
                            "market_cap",
                            "sector",
                            "last_updated",
                        ]

                d = dict(zip(self._funda_cols, res))

                # FALLBACK: If sector is missing in valuation.db, check meta.db (Fix: Sector Unknown Loop)
                if not d.get("sector") and self._meta_conn:
                    try:
                        m_res = self._meta_conn.execute(
                            "SELECT sector FROM symbols_master WHERE symbol = ?",
                            (clean,),
                        ).fetchone()
                        if m_res and m_res[0]:
                            d["sector"] = m_res[0]
                    except Exception:
                        pass

                # Return standardized casing for compatibility, but include all raw data
                out = {k.title(): v for k, v in d.items()}
                out.update(d)  # Keep original snake_case too
                return out
        except Exception:
            pass

        # FINAL FALLBACK: Even if no fundamentals exist, try to get at least the Sector from meta.db
        if self._meta_conn:
            try:
                m_res = self._meta_conn.execute(
                    "SELECT sector FROM symbols_master WHERE symbol = ?", (clean,)
                ).fetchone()
                if m_res and m_res[0]:
                    return {"Sector": m_res[0], "sector": m_res[0]}
            except Exception:
                pass

        return {}
