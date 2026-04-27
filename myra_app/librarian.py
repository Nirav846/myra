#!/usr/bin/env python
"""
MYRA Librarian - The Data Acquisition Layer (STABLE v3.0 - ATOMIC)
Facade class routing to modular SQLite Sidecars.
EXCLUSIVE GATEKEEPER for all MYRA data.
"""
import os
import pandas as pd
from myra_core.utils.data_validation import enforce_index_contract, validate_dataframe
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
from myra_app.constants import DB_DIR, DATA_DIR, CACHE_DIR


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
        self.db_dir = DB_DIR
        self.data_dir = DATA_DIR
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
            scoring_db_path=os.path.join(DB_DIR, self.DB_MAP["scoring"]),
        )

        if not self.read_only:
            self._create_tables()
            # 1. Migrate the metadata/system tables (e.g., symbols, sectors)
            self._migrate_meta_schema() 

            # 2. Validate/Auto-fix the technical data via the new Registry (v3.3)
            from myra_app.schema_registry import SchemaRegistry
            SchemaRegistry.validate_schema(self._tech_conn, "technical_data")

    def get_market_holidays(self, year):
        import json, os, datetime
        from myra_app.fetcher import DataFetcher

        cache_file = os.path.join(
            CACHE_DIR, f"holidays_{year}.json"
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
            tech_db = os.path.join(DB_DIR, self.DB_MAP["technical"])
            cal_db = os.path.join(DB_DIR, self.DB_MAP["calendar"])
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

    def get_symbols_by_sector(self, sector_name: str) -> list:
        """Returns symbols matching a sector name. Case-insensitive partial match."""
        if not self._val_conn:
            return []
        try:
            cursor = self._val_conn.execute(
                "SELECT symbol FROM fundamentals WHERE sector LIKE ? COLLATE NOCASE",
                (f"%{sector_name}%",)
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Sector lookup failed: {e}")
            return []

    def get_available_sectors(self) -> list[dict]:
        """Returns list of sectors with stock counts from fundamentals table."""
        if not self._val_conn:
            return []
        try:
            cur = self._val_conn.execute(
                "SELECT sector, COUNT(*) as cnt FROM fundamentals WHERE sector IS NOT NULL AND sector != '' "
                "GROUP BY sector ORDER BY sector"
            )
            return [{"sector": row[0], "count": row[1]} for row in cur.fetchall()]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Sector list failed: {e}")
            return []

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
                    # Dynamically select only columns that exist in technical_data
                    try:
                        cols_info = [r[1] for r in self._tech_conn.execute("PRAGMA table_info('technical_data')").fetchall()]
                    except Exception:
                        cols_info = []
                    desired = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "trades", "vwap", "delivery_pct"]
                    select_cols = [c for c in desired if c in cols_info]
                    if not select_cols:
                        delta = pd.DataFrame()
                    else:
                        col_str = ", ".join(select_cols)
                        query = f"SELECT {col_str} FROM technical_data WHERE symbol = ? AND date > ?"
                        delta = pd.read_sql(query, self._tech_conn, params=(clean, cache_max))
                    if not delta.empty:
                        # Enforce binary date unicity and drop bad dates BEFORE setting index
                        delta["date"] = pd.to_datetime(delta["date"], errors="coerce").dt.normalize()
                        delta = delta.dropna(subset=["date"])
                        delta.set_index("date", inplace=True)
                        # Immediately ensure index uniqueness to avoid concat/reindex crash
                        delta = enforce_index_contract(delta)

                        # Schema shield: rename legacy delivery columns to canonical names
                        delta.rename(columns={"delivery_qty": "delivery", "delivery_percent": "delivery_pct"}, inplace=True)
                        # TitleCase core columns for compatibility
                        delta.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume", "vwap": "Vwap", "trades": "Trades"}, inplace=True)
                        # Drop duplicate columns if any
                        delta = delta.loc[:, ~delta.columns.duplicated()]
                        delta["Adj Close"] = delta.get("Close", delta.get("close"))
                        # Merge and update cache
                        df = pd.concat([df, delta])
                        df.index = pd.to_datetime(df.index, errors="coerce").dt.normalize()
                        df = enforce_index_contract(df)


                        df = validate_dataframe(df, context=f"Librarian get_ohlcv: {clean}")

                        self.loader.save_to_parquet(clean, df)
                except Exception:
                    pass

        if not df.empty:
            # Normalization check for legacy Parquet files
            if "delivery" in df.columns and "delivery_qty" not in df.columns:
                df.rename(columns={"delivery": "delivery_qty"}, inplace=True)
            # Harmonize delivery percentage column names
            if "delivery_pct" not in df.columns or df["delivery_pct"].isnull().all():
                if "delivery_percent" in df.columns:
                    df["delivery_pct"] = df["delivery_pct"].fillna(df["delivery_percent"])
                elif "delivery_ratio" in df.columns:
                    df["delivery_pct"] = df["delivery_pct"].fillna(df["delivery_ratio"] * 100)

            # Ensure delivery_qty canonical name exists
            if "delivery_qty" not in df.columns and "delivery" in df.columns:
                df["delivery_qty"] = df["delivery"]

            # Clean redundant columns
            for c in ["delivery_percent", "delivery_ratio", "delivery"]:
                if c in df.columns and "delivery_pct" in df.columns:
                    try:
                        df.drop(columns=[c], inplace=True)
                    except Exception:
                        pass
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

            # Dynamically select only available columns to avoid SQL errors on missing fields
            try:
                cols_info = [r[1] for r in self._tech_conn.execute("PRAGMA table_info('technical_data')").fetchall()]
            except Exception:
                cols_info = []
            desired = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "trades", "vwap", "delivery_pct"]
            select_cols = [c for c in desired if c in cols_info]
            if not select_cols:
                return None
            col_str = ", ".join(select_cols)
            query = f"SELECT {col_str} FROM technical_data WHERE symbol = ?"
            if as_of_date:
                query += " AND date <= ?"
            query += " ORDER BY date ASC"

            res = pd.read_sql(query, self._tech_conn, params=params)

            if not res.empty:
                res["date"] = pd.to_datetime(res["date"])
                res.set_index("date", inplace=True)
                res = enforce_index_contract(res)

                # Schema Shield: rename legacy delivery columns to canonical names
                res.rename(columns={"delivery_qty": "delivery", "delivery_percent": "delivery_pct"}, inplace=True)

                # Ensure TitleCase for core numeric fields for downstream compatibility
                title_map = {}
                for c in ["open", "high", "low", "close", "volume", "vwap", "trades"]:
                    if c in res.columns:
                        title_map[c] = c.capitalize()
                if title_map:
                    res.rename(columns=title_map, inplace=True)

                # Drop duplicate columns that may arise from legacy joins
                res = res.loc[:, ~res.columns.duplicated()]

                # Harmonize delivery_pct if alternate names present
                if "delivery_pct" not in res.columns or res["delivery_pct"].isnull().all():
                    if "delivery_percent" in res.columns:
                        res["delivery_pct"] = res["delivery_percent"].fillna(res.get("delivery_pct"))
                    elif "delivery_ratio" in res.columns:
                        res["delivery_pct"] = res["delivery_ratio"].fillna(res.get("delivery_pct")) * 100

                # Ensure canonical delivery_qty if delivery exists
                if "delivery_qty" not in res.columns and "delivery" in res.columns:
                    res["delivery_qty"] = res["delivery"]

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
                df = df.sort_values("date").drop_duplicates(subset=['date'], keep='last')
                return df
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
