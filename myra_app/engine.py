#!/usr/bin/env python
"""
MYRA Engine - The Processing Coordinator (UNIVERSAL SQL v12)
Unified SQL precompute for both standard and piped (silent) scans.
"""
import multiprocessing
import importlib
import warnings
import signal
import sys
import os
import threading
import time
import pandas as pd
import numpy as np
from datetime import date, datetime
from typing import List, Dict, Any
from myra_app.data_adapter import DataAdapter
from myra_core.utils.myra_log import myra_log
from rich.progress import Progress

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings(
    "ignore",
    message=".*deprecated now, and have no effect.*",
    category=DeprecationWarning,
)


def run_with_hard_timeout(func, args, timeout=30):
    """
    Isolated Process Wrapper (Fix 4).
    Forcibly kills workers that hang at the C-extension level (scrapling/curl_cffi).
    DEPRECATED: Use multiprocessing.Pool for high-throughput scans.
    """
    import multiprocessing as mp

    q = mp.Queue()

    def wrapper(q, *args):
        try:
            res = func(*args)
            q.put(res)
        except Exception:
            q.put(None)

    p = mp.Process(target=wrapper, args=(q, *args))
    p.start()
    p.join(timeout)

    if p.is_alive():
        p.terminate()
        p.join()
        return None

    return q.get() if not q.empty() else None


class ScanWatchdog(threading.Thread):
    """
    Progress Watchdog (Fix 7).
    Detects if the scanner has stopped making progress for too long.
    """

    def __init__(self, timeout=60):
        super().__init__(daemon=True)
        self.last_hit = time.time()
        self.timeout = timeout
        self._running = True

    def poke(self):
        self.last_hit = time.time()

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            if time.time() - self.last_hit > self.timeout:
                print(
                    f"\n[CRITICAL] SCANNER STUCK FOR {self.timeout}s! Emergency abort."
                )
                os._exit(1)
            time.sleep(10)


from myra_app.worker_pool import run_workers


class Engine:
    def __init__(self, librarian):
        self.librarian = librarian

    def calculate_accuracy(
        self, symbol: str, strategy_name: str, df: pd.DataFrame = None, funda: dict = {}
    ) -> str:
        if df is None or df.empty:
            try:
                adapter = DataAdapter(librarian=self.librarian)
                df = adapter.get_price_df(symbol, lookback_days=756)
            except Exception:
                pass

        if df is None or df.empty or len(df) < 50:
            return "N/A"

        try:
            success = 0
            count = 0
            # Removed the 101 restriction here too
            is_primitive = str(strategy_name).isdigit() or ("|" in str(strategy_name))
            
            if is_primitive:
                strat_mod = importlib.import_module("myra_app.scanners.primitives")
            else:
                # Splits BEFORE replacing spaces to avoid trailing underscores
                clean_base = str(strategy_name).split("(")[0].strip()
                safe_name = clean_base.lower().replace(" ", "_").replace("-", "_")
                strat_mod = importlib.import_module(f"myra_app.strategies.{safe_name}")

            max_idx = len(df) - 10
            for i in range(max_idx, max(20, max_idx - 60), -1):
                if count >= 10:
                    break

                hist_df = df.iloc[:i]
                trigger = False
                if is_primitive:
                    if "|" in str(strategy_name):
                        sids = str(strategy_name).split("|")
                        trigger = any(
                            strat_mod.run_scanner(hist_df, s.strip(), funda=funda)
                            for s in sids
                        )
                    else:
                        trigger = strat_mod.run_scanner(
                            hist_df, str(strategy_name), funda=funda
                        )
                elif hasattr(strat_mod, "run"):
                    res = strat_mod.run(hist_df, funda)
                    trigger = res.get("signal", False)

                if trigger:
                    count += 1
                    entry = hist_df["Close"].iloc[-1]
                    future = df["Close"].iloc[i : i + 10]
                    if not future.empty:
                        cost_factor = 0.003
                        exit_price = future.max()
                        net_return = (exit_price / entry) - 1 - cost_factor
                        if net_return >= 0.03:
                            success += 1

            if count == 0:
                return "New"
            return f"{round((success/count)*100)}%"
        except Exception:
            return "N/A"

    def _is_vix_stable(self, lib) -> bool:
        try:
            if not lib._meta_conn:
                return True
            sql = """
                WITH vix_data AS (
                    SELECT date, close,
                           AVG(close) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as sma20
                    FROM benchmarks
                    WHERE symbol = ?
                )
                SELECT (close < sma20) as is_stable
                FROM vix_data
                ORDER BY date DESC
                LIMIT 1
            """
            res = lib._meta_conn.execute(sql, ("^INDIAVIX",)).fetchone()
            return bool(res[0]) if res else True
        except Exception:
            return True

    def run_scan(
        self,
        symbols: List[str],
        strategy_name: str,
        as_of_date: str = None,
        silent: bool = False,
    ):
        import time

        start_time = time.time()
        lib = self.librarian
        if not lib._tech_conn:
            lib.connect()

        from myra_core.utils.date_utils import to_date
        target_date = to_date(as_of_date) if as_of_date else date.today()
        from myra_app.fetcher import DataFetcher
        fetcher = DataFetcher()

        if fetcher._is_holiday(target_date):
            if not silent:
                print(f"[MYRA] {target_date} is a Market Holiday. Attempting Snapshot...")
            try:
                from myra_app.results_manager import ResultsManager
                rm = ResultsManager()
                snapshot = rm.load_last_snapshot(strategy_name)
                if snapshot:
                    if not silent:
                        print(f"[MYRA] Success: Loaded Last Known Good Snapshot.")
                    return snapshot, {"status": "HOLIDAY_SNAPSHOT"}
            except Exception:
                pass

            if not as_of_date:
                last_trading_day = lib.get_expected_trading_day(datetime.now())
                if not silent:
                    print(f"[MYRA] No Snapshot found. Targeting last trading day: {last_trading_day}")
                as_of_date = last_trading_day.date().isoformat() if hasattr(last_trading_day, 'date') else last_trading_day.isoformat()
            else:
                return [], {"status": "HOLIDAY_NO_DATA"}

        if not silent:
            if len(symbols) < 10:
                print(f"[MYRA] Quick Scan Mode for {len(symbols)} symbols")

        try:
            from myra_app.universe_loader import load_universe
            cache_df, regime, mood, vix_stable, funda_map, insider_map, deal_map = load_universe(
                lib, symbols, as_of_date, silent=silent
            )
            if cache_df.empty and len(symbols) >= 10:
                return [], {}

            target_symbols = (
                [s.split(".")[0].upper() for s in symbols]
                if symbols
                else [s.split(".")[0].upper() for s in lib.get_active_universe()]
            )

            try:
                from myra_app.librarian_core import LibrarianCore
                import sqlite3
                import os
                
                meta_conn = sqlite3.connect(
                    os.path.join(os.getcwd(), "db", LibrarianCore.DB_MAP["meta"]),
                    timeout=10,
                    check_same_thread=False,
                )
                meta_df = pd.read_sql("SELECT symbol, instrument_type FROM symbols_master", meta_conn)
                meta_conn.close()
                equity_symbols = set(meta_df.loc[meta_df['instrument_type'] == 'EQUITY', 'symbol'].str.upper())
                target_symbols = [s for s in target_symbols if s in equity_symbols]
            except Exception:
                pass

            payloads = [
                (s, strategy_name, as_of_date, funda_map.get(s, {"symbol": s}), precomputed.get(s))
                for s in target_symbols
            ]

        except Exception as e:
            if not silent:
                print(f"[!] Turbo Load failed: {e}")
            return [], {}

        # VECTORIZED PRE-COMPUTATION: compute indicators for ALL symbols once
        from myra_app.feature_enrichment import enrich_features
        import polars as pl

        # Read only the symbols needed for this scan
        if target_symbols:
            placeholders = ','.join(['?'] * len(target_symbols))
            query = f"SELECT * FROM technical_data WHERE symbol IN ({placeholders}) ORDER BY symbol, date"
            raw_df = pl.read_database(query, lib._tech_conn, params=target_symbols)
            
            if not raw_df.is_empty():
                # Use the existing Polars enrichment pipeline (fast, vectorized)
                nifty_df = pl.read_database(
                    "SELECT date, close FROM technical_data WHERE symbol LIKE '%NIFTY 50%'",
                    lib._tech_conn,
                )
                enriched = enrich_features(raw_df, nifty_df)
                
                # Convert to pandas and build a per-symbol lookup dict
                pdf = enriched.to_pandas()
                precomputed = {
                    symbol: group.set_index('date') if 'date' in group.columns else group
                    for symbol, group in pdf.groupby('symbol')
                }
            else:
                precomputed = {}
        else:
            precomputed = {}

        num_stocks = len(payloads)
        if not silent:
            print(f"[MYRA] Analyzing {num_stocks} stocks with Institutional Resilience...")

        watchdog = ScanWatchdog(timeout=120)
        watchdog.start()

        try:
            results = run_workers(payloads, strategy_name, lib.db_path, silent=silent, watchdog=watchdog)
        except KeyboardInterrupt:
            if not silent:
                print("\n[!] Scan interrupted by user.")
            watchdog.stop()
            raise KeyboardInterrupt
        except Exception as e:
            if not silent:
                print(f"[!] Engine Error: {e}")
        finally:
            watchdog.stop()

        valid_results = len(results)
        if num_stocks > 20 and valid_results == 0:
            if not silent:
                print(f"\n[CRITICAL] Data Integrity Failure: 0/{num_stocks} results produced. Aborting.")
            return [], {"error": "CATASTROPHIC_PIPELINE_FAILURE"}

        # Observability: report failed/skipped symbols
        failed = [r for r in results if isinstance(r, dict) and r.get("status") == "FAILED"]
        skipped = [r for r in results if isinstance(r, dict) and r.get("status") == "SKIPPED"]
        if failed:
            print(f"\n❌ Failed Symbols ({len(failed)}):")
            for f in failed[:10]:
                print(f"   • {f['symbol']}: {f.get('error', 'unknown')}")
        if skipped and not silent:
            print(f"\n⚠️  Skipped Symbols: {len(skipped)}")

        # Clean results to only contain valid signal dicts
        results = [r for r in results if isinstance(r, dict) and r.get("signal") or r.get("Stock")]

        if not silent:
            elapsed = time.time() - start_time
            print(f"[MYRA] Scan completed in {elapsed:.2f}s ({num_stocks} stocks, {elapsed/max(1,num_stocks):.3f}s/stock)")

        try:
            lineage_path = self.fetcher.lineage.save()
            if not silent:
                print(f"[MYRA] Data Lineage saved to {lineage_path}")
        except Exception:
            pass

        return results, {}


from myra_app.smc_manager import SMCManager
