#!/usr/bin/env python
"""
MYRA Librarian Intelligence Layer (TRILOGY ERA)
Handles all heavy indicator computation using Parquet Lake.
"""
import os
import logging

logger = logging.getLogger(__name__)
import pandas as pd
import numpy as np
import pandas_ta as ta
from tqdm import tqdm


class LibrarianIntelligenceMixin:
    def precompute_indicators(self, as_of_date=None):
        """
        Unified API for retrieving indicators.
        In the new modular stack, this reads from the Parquet Lake.
        """
        # We target the 'precomputed' strategy ID for standard indicators
        # Since we need a multi-symbol DataFrame for Turbo Load, we'll
        # collect the latest row for every active symbol.

        active_symbols = self.get_active_universe()
        if not active_symbols:
            return pd.DataFrame()

        # Optimized with list comprehension (Fix 34: Avoid .append in loop)
        def _get_latest_indicator(sym):
            df = self.loader.indicators.load_indicators("precomputed", sym)
            if not df.empty:
                if as_of_date:
                    df = df[df.index <= as_of_date]
                if not df.empty:
                    return df.iloc[[-1]]
            return None

        results = [
            res
            for sym in active_symbols
            if (res := _get_latest_indicator(sym)) is not None
        ]

        if not results:
            return pd.DataFrame()
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
        if self._inst_conn:
            try:
                # Group deals by symbol and date
                deals_df = pd.read_sql(
                    "SELECT symbol, date, SUM(qty) as inst_vol FROM large_deals GROUP BY symbol, date",
                    self._inst_conn,
                )
                if not deals_df.empty:
                    # Make sure date is datetime
                    deals_df["date"] = pd.to_datetime(deals_df["date"])
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                pass

        print(
            f"[MYRA] Updating Virtual Indicator Lake for {len(active_symbols)} symbols..."
        )

        for sym in tqdm(active_symbols, desc="Precomputing"):
            try:
                # 1. Load Price History (uses technical.db or Parquet)
                df = self.get_ohlcv(sym)
                if df is None or len(df) < 30:
                    continue

                # Normalize column names for computation
                df.columns = [c.capitalize() for c in df.columns]

                # TRUTH LAYER: Strict Mode Check
                # Drop rows where mandatory columns (Close, Volume) are NaN before proceeding.
                initial_len = len(df)
                # 'Symbol' might not be a column if it's index or implied, but we can check if it exists or check the critical ones
                mandatory_cols = [c for c in ["Close", "Volume"] if c in df.columns]
                df.dropna(subset=mandatory_cols, inplace=True)
                if len(df) < initial_len:
                    logger.warning(
                        f"Materiality Warning: Dropped {initial_len - len(df)} rows due to missing Close/Volume for {sym}"
                    )

                # Ensure index is datetime for exact date matching
                df.index = pd.to_datetime(df.index)
                df.sort_index(inplace=True)

                # Join Institutional Deals explicitly on EXACT dates
                if not deals_df.empty:
                    sym_deals = deals_df[deals_df["symbol"] == sym].copy()
                    if not sym_deals.empty:
                        sym_deals.set_index("date", inplace=True)
                        # Left join institutional volume to the price dataframe
                        df = df.join(sym_deals[["inst_vol"]], how="left")
                        df["inst_vol"] = df["inst_vol"].fillna(0.0)
                    else:
                        df["inst_vol"] = 0.0
                else:
                    df["inst_vol"] = 0.0

                # 2. Basic Technicals
                df["sma20"] = ta.sma(df["Close"], length=20)
                df["sma50"] = ta.sma(df["Close"], length=50)
                df["sma150"] = ta.sma(df["Close"], length=150)
                df["sma200"] = ta.sma(df["Close"], length=200)
                df["rsi"] = ta.rsi(df["Close"], length=14)

                # 3. Volatility & Risk
                df["atr20"] = ta.atr(df["High"], df["Low"], df["Close"], length=20)
                df["atr5"] = ta.atr(df["High"], df["Low"], df["Close"], length=5)
                df["atr14"] = ta.atr(df["High"], df["Low"], df["Close"], length=14)
                df["std20"] = df["Close"].rolling(20).std()
                df["low_1y"] = df["Low"].rolling(252).min()
                df["high_1y"] = df["High"].rolling(252).max()
                df["drawdown"] = (df["high_1y"] - df["Close"]) / df["high_1y"].replace(
                    0, 1
                )

                # TRUTH LAYER: Add a validation gate to reject any join where 'Institutional Volume'
                # exceeds the 'Total Traded Volume' (Volume) for that day.
                if "inst_vol" in df.columns and "Volume" in df.columns:
                    # Identify corrupted rows where inst_vol > Total Traded Volume
                    corrupted_mask = df["inst_vol"].fillna(0) > df["Volume"].fillna(0)
                    # Nullify/reject institutional volume for those specific days
                    df.loc[corrupted_mask, "inst_vol"] = 0.0

                # 4. Institutional Footprints (VSA)
                df["vol_sma50"] = ta.sma(df["Volume"], length=50)
                if "Delivery_qty" in df.columns:
                    df["deliv_sma50"] = ta.sma(df["Delivery_qty"], length=50)
                    df["avg_delivery_20d"] = ta.sma(df["Delivery_qty"], length=20)
                    df["rdv"] = df["Delivery_qty"] / df["avg_delivery_20d"].replace(
                        0, 1
                    )
                    df["money_flow_cr"] = (
                        df["Delivery_qty"] * df["Close"]
                    ) / 10000000.0
                else:
                    df["rdv"] = 1.0
                    df["money_flow_cr"] = 0.0

                df["avg_volume_20d"] = ta.sma(df["Volume"], length=20)

                # VSA Metrics
                spread = df["High"] - df["Low"]
                avg_spread = spread.rolling(50).mean()
                df["rel_spread"] = spread / avg_spread.replace(0, 1)
                df["rel_vol"] = df["Volume"] / df["vol_sma50"].replace(0, 1)
                df["closing_pos"] = (df["Close"] - df["Low"]) / spread.replace(0, 1)

                # 5. SMC-1: D-POC & Phases
                df["d_poc"] = df["Close"].rolling(60).mean()
                df["smc_phase"] = 0
                cond2 = (
                    (df["Close"] > df["d_poc"] * 1.03)
                    & (df["Close"] >= df["high_1y"].shift(1))
                    & (df["Volume"] > df["avg_volume_20d"] * 1.5)
                )
                df.loc[cond2, "smc_phase"] = 2

                # 6. SMC-2: Market Structure (CHoCH, BOS, FVG)
                from myra_app.engine import SMCManager

                # FVG
                df["fvg"] = SMCManager.calculate_fvg(
                    df.rename(columns=lambda x: x.lower())
                )

                # BOS & CHoCH
                bos, choch = SMCManager.calculate_market_structure(
                    df.rename(columns=lambda x: x.lower())
                )
                df["bos"] = bos
                df["choch"] = choch

                # 7. Save to Lake
                # Standardize columns to lowercase for Lake consistency (Project Mandate #4)
                df.columns = [c.lower() for c in df.columns]
                df["symbol"] = sym
                self.loader.indicators.save_indicators("precomputed", sym, df)

            except Exception:
                continue

        print("[+] Indicator Lake Updated.")
