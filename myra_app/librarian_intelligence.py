#!/usr/bin/env python
"""
MYRA Librarian Intelligence Layer (TRILOGY ERA)
Handles all heavy indicator computation using Parquet Lake.
"""
import os
import logging
import pandas as pd
import numpy as np
import pandas_ta as ta  # Required for technical indicators
from myra_core.utils.myra_log import myra_log

logger = logging.getLogger(__name__)

class LibrarianIntelligenceMixin:
    def precompute_indicators(self, as_of_date=None):
        """
        Unified API for retrieving indicators.
        In the new modular stack, this reads from the Parquet Lake.
        """
        active_symbols = self.get_active_universe()
        if not active_symbols:
            return pd.DataFrame()

        def _get_latest_indicator(sym):
            # Using self.loader to access Parquet Lake
            df = self.loader.indicators.load_indicators("precomputed", sym)
            if df is not None and not df.empty:
                if as_of_date:
                    # Filter data up to the requested date
                    df = df[df.index <= as_of_date]
                if not df.empty:
                    return df.iloc[[-1]]
            return None

        # Fix 34: Efficient list comprehension for results
        results = [
            res
            for sym in active_symbols
            if (res := _get_latest_indicator(sym)) is not None
        ]

        if not results:
            return pd.DataFrame()
        
        # Consolidate all symbol rows into one "Turbo" DataFrame
        return pd.concat(results).reset_index()

    def update_indicator_history(self):
        """
        Computes comprehensive indicators for ALL active stocks and saves to Parquet Lake.
        Includes technicals, VSA, and SMC (CHoCH, BOS, FVG).
        """
        if self.read_only:
            return

        active_symbols = self.get_active_universe()
        if not active_symbols:
            print("[!] No active symbols found for indicator update.")
            return

        # 0. Load Institutional Deals for joining
        deals_df = pd.DataFrame()
        if hasattr(self, '_inst_conn') and self._inst_conn:
            try:
                # Group deals by symbol and date
                deals_df = pd.read_sql(
                    "SELECT symbol, date, SUM(qty) as inst_vol FROM large_deals GROUP BY symbol, date",
                    self._inst_conn,
                )
                if not deals_df.empty:
                    deals_df["date"] = pd.to_datetime(deals_df["date"])
            except Exception as e:
                logger.error(f"Failed to load large deals: {e}")

        print(f"[MYRA] Updating Virtual Indicator Lake for {len(active_symbols)} symbols...")

        total_syms = len(active_symbols)
        for i, sym in enumerate(active_symbols, 1):
            myra_log(i, total_syms, desc="Precomputing")
            try:
                # 1. Load Price History
                df = self.get_ohlcv(sym)
                if df is None or len(df) < 30:
                    continue

                # Normalize to CamelCase for ta-lib consistency
                df.columns = [c.capitalize() for c in df.columns]

                # TRUTH LAYER: Remove bad data
                df.dropna(subset=["Close", "Volume"], inplace=True)
                df.index = pd.to_datetime(df.index)
                df.sort_index(inplace=True)

                # Join Institutional Volume
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

                # Reject corrupted institutional volume (if inst_vol > total volume)
                if "inst_vol" in df.columns:
                    corrupted_mask = df["inst_vol"] > df["Volume"]
                    df.loc[corrupted_mask, "inst_vol"] = 0.0

                # 2. Basic Technicals
                df["sma20"] = ta.sma(df["Close"], length=20)
                df["sma50"] = ta.sma(df["Close"], length=50)
                df["sma150"] = ta.sma(df["Close"], length=150)
                df["sma200"] = ta.sma(df["Close"], length=200)
                df["rsi"] = ta.rsi(df["Close"], length=14)

                # 3. Volatility & Risk
                df["atr20"] = ta.atr(df["High"], df["Low"], df["Close"], length=20)
                df["atr5"] = ta.atr(df["High"], df["Low"], df["Close"], length=5)
                df["std20"] = df["Close"].rolling(20).std()
                df["low_1y"] = df["Low"].rolling(252).min()
                df["high_1y"] = df["High"].rolling(252).max()

                # 4. Institutional Footprints (VSA)
                df["vol_sma50"] = ta.sma(df["Volume"], length=50)
                if "Delivery_qty" in df.columns:
                    df["avg_delivery_20d"] = ta.sma(df["Delivery_qty"], length=20)
                    df["rdv"] = df["Delivery_qty"] / df["avg_delivery_20d"].replace(0, 1)
                else:
                    df["rdv"] = 1.0

                # 5. SMC/Market Structure
                from myra_app.engine import SMCManager
                
                # FVG, BOS & CHoCH Logic
                df_lower = df.rename(columns=lambda x: x.lower())
                df["fvg"] = SMCManager.calculate_fvg(df_lower)
                bos, choch = SMCManager.calculate_market_structure(df_lower)
                df["bos"] = bos
                df["choch"] = choch

                # 7. Save to Parquet Lake (Lowercase for consistency)
                df.columns = [c.lower() for c in df.columns]
                df["symbol"] = sym
                self.loader.indicators.save_indicators("precomputed", sym, df)

            except Exception as e:
                logger.debug(f"Skipping {sym}: {e}")
                continue

        print("[+] Indicator Lake Updated.")
