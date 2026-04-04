#!/usr/bin/env python
"""
MYRA Librarian Sync Layer (TRILOGY ERA)
Handles background orchestration across modular sidecars.
Routes data to technical.db, institutional.db, meta.db, and valuation.db.
"""
import sys
import os
import threading
import duckdb
import pandas as pd
from datetime import date, datetime, timedelta
from tqdm import tqdm
import concurrent.futures

try:
    import yfinance as yf
except ImportError:
    pass

class LibrarianSyncMixin:
    def sync_market_data(self, history_years=1, conn=None, skip_maintenance=False):
        """Unified Sync Orchestrator for Modular Architecture."""
        if getattr(self, '_is_syncing', False):
            return
        
        self._is_syncing = True
        try:
            self.sync_status.update(task="Initializing Sync", completed=0, total=100)
            
            # 1. Fetch Price Archives (Populates technical.db & DuckDB cache)
            # existing_dates should check technical.db
            existing_dates = set()
            if self._tech_conn:
                try:
                    res = self._tech_conn.execute("SELECT DISTINCT date FROM technical_data").fetchall()
                    existing_dates = {datetime.strptime(r[0], "%Y-%m-%d").date() for r in res}
                except Exception: pass

            ts = date.today() - timedelta(days=history_years * 365)
            fe = date.today()
            self._fetch_archives(start_date=ts, end_date=fe, existing_dates=existing_dates)
            
            if skip_maintenance: 
                self.sync_status.update(task="", completed=0, total=0)
                return

            # 2. Sync Benchmarks (meta.db)
            self.sync_status.update(task="Syncing Benchmarks", completed=30, total=100)
            self._sync_benchmark()
            
            # 3. Insider Trades (institutional.db)
            self.sync_status.update(task="Syncing Insider Trades", completed=40, total=100)
            ls_str = self.get_metadata("last_insider_sync") or "1900-01-01 00:00:00"
            ls = datetime.strptime(ls_str, "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - ls).total_seconds() > 14400:
                trades = self.fetcher.fetch_insider_trades(days=30)
                if self._inst_conn:
                    today = date.today()
                    for item in trades:
                        try:
                            val = float(item.get('secVal', 0))
                            qty = float(item.get('secAcq', 0))
                            avg_price = round(val / qty, 2) if qty > 0 else 0
                            
                            val_cr = val / 10000000
                            raw_dt = item.get('intimDt', item.get('date'))
                            try: dt = datetime.strptime(raw_dt, "%d-%b-%Y").date()
                            except ValueError: dt = pd.to_datetime(raw_dt, errors='coerce').date()
                            if pd.isna(dt): continue
                            
                            # ANTI-ANOMALY: Prevent future dates
                            if dt > today: continue
                            
                            # Normalization: Ensure symbols with special characters are normalized
                            raw_sym = str(item.get('symbol', '')).upper().strip()
                            raw_sym = raw_sym.replace(' AND ', '&')
                            if "M_M" in raw_sym or "M M" in raw_sym:
                                raw_sym = raw_sym.replace("M_M", "M&M").replace("M M", "M&M")
                            if "_" in raw_sym:
                                raw_sym = raw_sym.replace("_", "-")

                            self._inst_conn.execute("INSERT OR REPLACE INTO insider_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                                             (raw_sym, item.get('acqName'), item.get('personCategory'),
                                              item.get('tdpTransactionType'), item.get('acqMode'), val_cr, avg_price, str(dt)))
                        except Exception: pass
                    self._inst_conn.commit()
                    self.set_metadata("last_insider_sync", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            
            # 4. Update Masters (meta.db)
            self.sync_status.update(task="Updating Masters", completed=60, total=100)
            self.populate_index_constituents()
            self.update_symbols_master()
            
            # Incremental Sector Sync (New)
            try:
                from myra_app.sector_manager import SectorManager
                sector_mgr = SectorManager(db_path=os.path.join(self.db_dir, "meta.db"))
                sector_mgr.incremental_sync()
            except Exception as e:
                if hasattr(self, 'console'): self.console.print(f"[warning][!] Sector Sync Error: {e}[/]")

            # --- Plan 1: Governance & IAS Hooks ---
            try:
                from myra_app.ias_manager import IASManager
                ias_mgr = IASManager(db_dir=self.db_dir)
                # 1. Daily Incremental (SAST)
                ias_mgr.sync_sast_incremental()
                
                # 4. FII/DII and Announcements (New)
                try:
                    fii_data = self.fetcher.fetch_fii_dii_activity()
                    if fii_data and hasattr(self, 'console'): self.console.print("[dim][IAS] Fetched FII/DII activity.[/dim]")
                    
                    announcements = self.fetcher.fetch_corporate_announcements()
                    if announcements: 
                        if hasattr(self, 'console'): self.console.print(f"[dim][IAS] Fetched {len(announcements.get('data', []))} announcements.[/dim]")
                except: pass

                # 2. Weekly Full Sweep (Saturdays)
                if date.today().weekday() == 5: # 5 = Saturday
                    active_syms = self.get_active_universe()
                    if active_syms:
                        ias_mgr.sync_pledge_full(active_syms)
                
                # 3. Refresh Ranking Cache (Daily)
                ias_mgr.update_ias_cache(self)
            except Exception as e:
                if hasattr(self, 'console'): self.console.print(f"[warning][!] IAS Hook Error: {e}[/]")
            
            # 5. Strategic Intelligence (Transitioning to Parquet in Phase 3)
            self.sync_status.update(task="Computing Indicators", completed=70, total=100)
            self.update_indicator_history()
            
            # 6. Large Deals (institutional.db)
            self.sync_status.update(task="Syncing Large Deals", completed=80, total=100)
            self.sync_large_deals()
            
            # 7. Fundamentals (valuation.db)
            active_syms = self.get_active_universe()
            if active_syms and self._val_conn:
                total_f = len(active_syms)
                for idx, s in enumerate(active_syms):
                    if idx % 50 == 0:
                        self.sync_status.update(task=f"Fundamentals: {s}", completed=idx, total=total_f)
                    if self.fundamental_manager.is_stale(s, days=30):
                        self.fundamental_manager.update_quarterly(s)
            
            # 8. Maintenance
            self.sync_status.update(task="Compacting DB", completed=95, total=100)
            if self._tech_conn: self._tech_conn.execute("VACUUM")
            
            if not skip_maintenance:
                self.run_integrity_check()
                
        except Exception as e:
            if hasattr(self, 'console'):
                self.console.print(f"[bold red][!] Sync Error: {e}[/]")
            else:
                print(f"[!] Sync Error: {e}")
        finally:
            self._is_syncing = False
            self.sync_status.update(task="", completed=0, total=0)

    def sync_large_deals(self):
        if not self._inst_conn or self.read_only: return
        ls_str = self.get_metadata("last_large_deals_sync") or "1900-01-01 00:00:00"
        ls = datetime.strptime(ls_str, "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - ls).total_seconds() > 14400:
            deals = self.fetcher.fetch_large_deals_v2()
            if deals:
                for d in deals:
                    try:
                        # Normalization: Ensure symbols with special characters are normalized
                        raw_sym = str(d.get('symbol', '')).upper().strip()
                        raw_sym = raw_sym.replace(' AND ', '&')
                        if "M_M" in raw_sym or "M M" in raw_sym:
                            raw_sym = raw_sym.replace("M_M", "M&M").replace("M M", "M&M")
                        if "_" in raw_sym:
                            raw_sym = raw_sym.replace("_", "-")

                        self._inst_conn.execute("""
                            INSERT OR REPLACE INTO large_deals VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (raw_sym, d['type'], d['client'], d['buy_sell'], d['qty'], d['price'], str(d['date'])))
                    except Exception: pass
                self._inst_conn.commit()
                self.set_metadata("last_large_deals_sync", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    def populate_index_constituents(self):
        if not self._meta_conn or self.read_only: return
        for idx in ["NIFTY 50", "NIFTY 500", "NIFTY NEXT 50"]:
            try:
                symbols = self.fetcher.fetch_index_constituents(idx)
                for s in symbols: 
                    self._meta_conn.execute("INSERT OR IGNORE INTO index_constituents VALUES (?, ?)", (idx, s.upper()))
                self._meta_conn.commit()
            except Exception: pass

    def update_symbols_master(self):
        """Updates master list from prices in DuckDB or technical.db."""
        if not self._meta_conn or self.read_only: return
        
        # We use DuckDB or Technical.db to find first/last seen
        source_conn = self.conn if self.conn else self._tech_conn
        if not source_conn: return
        
        # Note: logic remains same but targets _meta_conn
        try:
            # Simple version for rebuild: get symbols from technical.db
            res = self._tech_conn.execute("SELECT symbol, MIN(date), MAX(date) FROM technical_data GROUP BY symbol").fetchall()
            for r in res:
                self._meta_conn.execute("INSERT OR IGNORE INTO symbols_master (symbol, first_seen, last_seen, is_active) VALUES (?, ?, ?, 1)", (r[0], r[1], r[2]))
            
            # Update Active Universe (Simplified for Phase 2)
            self._meta_conn.execute("UPDATE symbols_master SET in_nifty500 = (symbol IN (SELECT symbol FROM index_constituents WHERE index_name = 'NIFTY 500'))")
            # in_active_universe logic...
            self._meta_conn.commit()
        except Exception: pass

    def _sync_benchmark(self):
        if not self._meta_conn or self.read_only: return
        for symbol in ["^NSEI", "^INDIAVIX"]:
            try:
                # yf download...
                data = yf.download(symbol, period="1mo", interval="1d", progress=False)
                for d, row in data.iterrows(): 
                    self._meta_conn.execute("INSERT OR REPLACE INTO benchmarks VALUES (?, ?, ?)", (symbol, str(d.date()), float(row['Close'])))
                self._meta_conn.commit()
            except Exception: pass

    def start_background_sync(self, history_years=0.04):
        """Orchestrates continuous background synchronization loop."""
        if self._sync_thread and self._sync_thread.is_alive():
            return self._sync_thread
            
        import time

        def sync_loop():
            # Initial catchup history
            h = history_years if history_years > 0 else 0.04
            while True:
                try:
                    self.sync_market_data(history_years=h, skip_maintenance=True)
                    # For subsequent iterations, 0.01 (~3.6 days) is enough to stay current
                    h = 0.01 
                except Exception as e:
                    if hasattr(self, 'console'):
                        self.console.print(f"[bold red][!] Background Loop Error: {e}[/]")
                
                # Sleep for 2 hours (7200 seconds) between sync cycles
                time.sleep(7200)

        self._sync_thread = threading.Thread(target=sync_loop, name="MYRA_Sync_Daemon")
        self._sync_thread.daemon = True
        self._sync_thread.start()
        return self._sync_thread
