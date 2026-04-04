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

class Librarian(LibrarianCore, LibrarianSchemaMixin, LibrarianSyncMixin, LibrarianIntelligenceMixin, LibrarianIngestorMixin):
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
        
        from myra_app.fundamental_ranker import FundamentalRanker
        self.fundamental_ranker = FundamentalRanker(self._val_conn, scoring_db_path=os.path.join(os.getcwd(), "db", "scoring.db"))
        
        if not self.read_only:
            self._create_tables()
            self._migrate_schema()

    def run_integrity_check(self):
        """Runs the TechnicalAudit tool to verify modular DB health."""
        from myra_app.technical_audit import TechnicalAudit
        try:
            tech_db = os.path.join(os.getcwd(), "db", "technical.db")
            cal_db = os.path.join(os.getcwd(), "db", "calendar.db")
            audit = TechnicalAudit(tech_db=tech_db, cal_db=cal_db)
            audit.run_audit()
        except Exception as e:
            if hasattr(self, 'console'):
                self.console.print(f"[warning][!] Integrity Check Failed: {e}[/warning]")
            else:
                print(f"[warning][!] Integrity Check Failed: {e}")

    # --- Read Operations (Facade Methods) ---
    
    def get_max_price_date(self):
        if not self._tech_conn: return None
        res = self._tech_conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
        return res[0] if res else None

    def get_max_insider_date(self):
        if not self._inst_conn: return None
        try:
            res = self._inst_conn.execute("SELECT MAX(date) FROM insider_trades").fetchone()
            return res[0] if res else None
        except Exception: return None

    def get_all_symbols(self):
        if not self._meta_conn: return []
        res = self._meta_conn.execute("SELECT DISTINCT symbol FROM symbols_master").fetchall()
        return [r[0] for r in res]

    def get_index_symbols(self, index_name="NIFTY 50"):
        if not self._meta_conn: return []
        res = self._meta_conn.execute("SELECT symbol FROM index_constituents WHERE index_name = ?", (index_name,)).fetchall()
        return [r[0] for r in res]

    def get_active_universe(self):
        if not self._meta_conn: return []
        res = self._meta_conn.execute("SELECT symbol FROM symbols_master WHERE in_active_universe = 1").fetchall()
        return [r[0] for r in res]

    def get_market_regime(self):
        # Market regime will transition to Parquet Indicators in Phase 3
        return "NEUTRAL (Modular Transition)"

    def get_ohlcv(self, symbol, as_of_date=None):
        df = self.loader.load_from_parquet(symbol)
        if not df.empty: 
            # Normalization check for legacy Parquet files
            if 'delivery' in df.columns and 'delivery_qty' not in df.columns:
                df.rename(columns={'delivery': 'delivery_qty'}, inplace=True)
            if as_of_date:
                df = df[df.index <= as_of_date]
            return df
        
        if not self._tech_conn: return None
        clean = symbol.split('.')[0].upper()
        try:
            query = "SELECT * FROM technical_data WHERE symbol = ?"
            params = [clean]
            if as_of_date:
                query += " AND date <= ?"
                params.append(as_of_date)
            query += " ORDER BY date ASC"
            
            res = pd.read_sql(query, self._tech_conn, params=params)
            
            if not res.empty:
                res['date'] = pd.to_datetime(res['date']); res.set_index('date', inplace=True)
                # Standardize for Intelligence Layer (lowercase_snake_case)
                res.rename(columns={
                    'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume',
                    'delivery': 'delivery_qty', 'delivery_pct': 'delivery_percent'
                }, inplace=True)
                res['Adj Close'] = res['Close']
                self.loader.save_to_parquet(symbol, res); return res
        except Exception: pass
        return None

    def get_delivery_data(self, symbol, days=60):
        """Fetches historical price and delivery data for SMC-1 profiling."""
        if not self._tech_conn: return pd.DataFrame()
        clean = symbol.split('.')[0].upper()
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
                return df.sort_values('date')
        except Exception: pass
        return pd.DataFrame()

    def get_sector_stats(self):
        if not self._val_conn: return {}
        try:
            df = pd.read_sql("SELECT * FROM fundamentals", self._val_conn)
            stats = {}
            for sector, group in df.groupby("sector"):
                stats[sector] = {}
                for c in ["pe", "roe"]:
                    if c in group.columns:
                        stats[sector][c] = {"mean": group[c].mean(), "std": group[c].std() or 1}
            return stats
        except Exception: return {}

    def get_fundamentals(self, symbol):
        if not self._val_conn: return {}
        clean = symbol.split('.')[0].upper()
        try:
            res = self._val_conn.execute("SELECT * FROM fundamentals WHERE symbol = ?", (clean,)).fetchone()
            if res:
                # Get column names for dict mapping
                cursor = self._val_conn.execute("PRAGMA table_info('fundamentals')")
                cols = [row[1] for row in cursor.fetchall()] # row[1] is name
                # Simple fallback if fetchall fails to give names
                if not cols: cols = ['symbol', 'pe', 'roe', 'eps', 'book_value', 'market_cap', 'sector', 'last_updated']

                d = dict(zip(cols, res))
                # Return standardized casing for compatibility, but include all raw data
                out = {k.title(): v for k, v in d.items()}
                out.update(d) # Keep original snake_case too
                return out
        except Exception: pass
        return {}
