#!/usr/bin/env python
"""
MYRA Librarian Intelligence Layer (TRILOGY ERA)
Handles all heavy indicator computation using Parquet Lake.
"""
import os
import logging
import pandas as pd
import numpy as np
import pandas_ta as ta
from myra_core.utils.myra_log import myra_log

logger = logging.getLogger(__name__)

class LibrarianIntelligenceMixin:
    def precompute_indicators(self, as_of_date=None):
        """
        Unified API for retrieving indicators via Parquet Lake.
        Fix: Removed local variable shadowing for 'pd'.
        """
        active_symbols = self.get_active_universe()
        if not active_symbols:
            return pd.DataFrame()

        results = []
        for symbol_name in active_symbols:
            try:
                # Load from Parquet Lake
                df_node = self.loader.indicators.load_indicators("precomputed", symbol_name)
                
                if df_node is not None and not df_node.empty:
                    if as_of_date:
                        # Filter for historical backtesting
                        df_node = df_node[df_node.index <= as_of_date]
                    
                    if not df_node.empty:
                        # Grab the latest state for the Turbo DataFrame
                        results.append(df_node.iloc[[-1]])
            except Exception as e:
                logger.debug(f"Indicator load failed for {symbol_name}: {e}")
                continue

        if not results:
            return pd.DataFrame()

        # Fix for FutureWarning: Ensure no empty DFs are passed to concat
        valid_results = [r for r in results if not r.empty]
        if not valid_results:
            return pd.DataFrame()

        # Consolidate into the 'Turbo Load' DataFrame
        final_df = pd.concat(valid_results, axis=0, ignore_index=False)
        return final_df.reset_index()

    def update_indicator_history(self):
        """
        Computes comprehensive indicators for ALL active stocks and saves to Parquet Lake.
        Includes technicals, VSA, and SMC (CHoCH, BOS, FVG).
        """
        if getattr(self, 'read_only', False):
            return

        active_symbols = self.get_active_universe()
        if not active_symbols:
            print("[!] No active symbols found for indicator update.")
            return

        # Load Institutional Deals for joining
        deals_df = pd.DataFrame()
        if hasattr(self, '_inst_conn') and self._inst_conn:
            try:
                deals_df = pd.read_sql(
                    "SELECT symbol, date, SUM(qty) as inst_vol FROM large_deals GROUP BY symbol, date",
                    self._inst_conn,
                )
                if not deals_df.empty:
                    deals_df["date"] = pd.to_datetime(deals_df["date"])
            except Exception as e:
                logger.error(f"Deal fetch failed: {e}")

        print(f"[MYRA] Updating Virtual Indicator Lake for {len(active_symbols)} symbols...")

        total_syms = len(active_symbols)
        for i, sym in enumerate(active_symbols, 1):
            myra_log(i, total_syms, desc="Precomputing")
            try:
                # 1. Load Price History (Targeting the DataAdapter we fixed)
                df = self.get_ohlcv(sym)
                if df is None or len(df) < 30:
                    continue

                # Standardize casing for ta-lib
                df.columns = [c.capitalize() for c in df.columns]
                df.index = pd.to_datetime(df.index)
                df.sort_index(inplace=True)

                # 2. Join Institutional Volume
                if not deals_df.empty:
                    sym_deals = deals_df[deals_df["symbol"] == sym].copy()
                    if not sym_deals.empty:
                        sym_deals.set_index("date", inplace=True)
                        df = df.join(sym_deals[["inst_vol"]], how="left")
                        df["inst_vol"] = df["inst_vol"].fillna(0.0)
                    else:
                        df["inst_vol"] = 0.0
                else:
                    df["inst_vol"] = 0.0

                # 3. Compute Core Technicals
                df["sma20"] = ta.sma(df["Close"], length=20)
                df["sma50"] = ta.sma(df["Close"], length=50)
                df["sma200"] = ta.sma(df["Close"], length=200)
                df["rsi"] = ta.rsi(df["Close"], length=14)
                df["atr20"] = ta.atr(df["High"], df["Low"], df["Close"], length=20)

                # 4. Institutional Footprints (VSA)
                df["vol_sma50"] = ta.sma(df["Volume"], length=50)
                if "Delivery_qty" in df.columns:
                    df["avg_deliv_20d"] = ta.sma(df["Delivery_qty"], length=20)
                    df["rdv"] = df["Delivery_qty"] / df["avg_deliv_20d"].replace(0, 1)
                else:
                    df["rdv"] = 1.0

                # 5. SMC Structural Flow
                from myra_app.engine import SMCManager
                df_lower = df.rename(columns=lambda x: x.lower())
                df["fvg"] = SMCManager.calculate_fvg(df_lower)
                bos, choch = SMCManager.calculate_market_structure(df_lower)
                df["bos"] = bos
                df["choch"] = choch

                # 6. Save to Lake (Lowercase consistency)
                df.columns = [c.lower() for c in df.columns]
                df["symbol"] = sym
                self.loader.indicators.save_indicators("precomputed", sym, df)

            except Exception as e:
                logger.debug(f"Skip {sym}: {e}")
                continue

        print("[+] Indicator Lake Updated.")
