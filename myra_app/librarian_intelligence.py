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
        Fixed: Removed list comprehension to solve 'pd' scoping issues permanently.
        """
        active_symbols = self.get_active_universe()
        if not active_symbols:
            return pd.DataFrame()

        results_list = []
        for sym in active_symbols:
            try:
                # Load from Parquet Lake
                df_node = self.loader.indicators.load_indicators("precomputed", sym)
                
                if df_node is not None and not df_node.empty:
                    if as_of_date:
                        df_node = df_node[df_node.index <= as_of_date]
                    
                    if not df_node.empty:
                        # Append the last row (latest state)
                        results_list.append(df_node.iloc[[-1]])
            except Exception as e:
                logger.debug(f"Failed to load indicator for {sym}: {e}")
                continue

        return pd.concat([res for res in results_list if not res.empty], axis=0).reset_index() if results_list else pd.DataFrame()

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

        # Bypass institutional deal tables: do not fetch or merge large_deals/insider_trades.
        # Delivery-centric resilience: compute IAS purely from technical/delivery columns.
        deals_df = pd.DataFrame()

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
                # Force uniqueness of index to avoid stale/duplicate rows (The Shield)
                df = df.loc[~df.index.duplicated(keep='last')]

                # 2. Skip joining institutional volume (delivery-centric resilience)
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

                # Delivery-centric institutional metrics (compute IAS using technical_data only)
                try:
                    # approximate delivery_pct if only delivery_qty is present
                    if "Delivery_qty" in df.columns and "Volume" in df.columns:
                        df["delivery_pct"] = (df["Delivery_qty"] / df["Volume"].replace(0, np.nan)).fillna(0.0) * 100.0
                    elif "Delivery_pct" in df.columns:
                        df["delivery_pct"] = df["Delivery_pct"].fillna(0.0)
                    else:
                        df["delivery_pct"] = 0.0

                    # Volatility Compression Score (VCP): higher when ATR is small relative to price
                    df["vcp"] = 1.0 - (df["atr20"] / df["sma20"].replace(0, np.nan)).fillna(0.0)
                    df["vcp"] = df["vcp"].clip(lower=0.0, upper=1.0)

                    # Primary metric: delivery_divergence_score (z-score of delivery_pct vs recent window)
                    rolling_mean = df["delivery_pct"].rolling(20, min_periods=1).mean()
                    rolling_std = df["delivery_pct"].rolling(20, min_periods=1).std().replace(0, np.nan).fillna(1.0)
                    df["delivery_divergence_score"] = ((df["delivery_pct"] - rolling_mean) / rolling_std).fillna(0.0)

                    # Factor VCP into the divergence score
                    df["delivery_divergence_score"] = df["delivery_divergence_score"] * df["vcp"]

                    # Compute VWAP (use pandas_ta vwap if available)
                    try:
                        df["vwap"] = ta.vwap(df["High"], df["Low"], df["Close"], df["Volume"])
                    except Exception:
                        # fallback: rolling price-volume average
                        pv = (df["Close"] * df["Volume"]).rolling(20, min_periods=1).sum()
                        v = df["Volume"].rolling(20, min_periods=1).sum().replace(0, np.nan)
                        df["vwap"] = (pv / v).fillna(df["Close"])

                    # Heavy institutional absorption signal: delivery_pct > 50% and Close > VWAP
                    heavy_absorption = ((df["delivery_pct"] > 50.0) & (df["Close"] > df["vwap"])) .astype(float)

                    # Secondary metric: delivery_pct when absorption condition met
                    df["delivery_accumulation_signal"] = df["delivery_pct"] * heavy_absorption

                    # Institutional Accumulation Score (IAS): delivery-weighted with VCP guardrail
                    # Primary weight on delivery_divergence_score, secondary on accumulation signal
                    primary_w = 0.8
                    secondary_w = 0.2
                    df["ias"] = (primary_w * df["delivery_divergence_score"]) + (secondary_w * (df["delivery_accumulation_signal"] / 100.0))
                    # Apply VCP as guardrail to final IAS
                    df["ias"] = df["ias"] * df["vcp"]
                except Exception:
                    # keep robust: set neutral defaults
                    df["delivery_pct"] = df.get("delivery_pct", 0.0)
                    df["vcp"] = df.get("vcp", 0.0)
                    df["delivery_divergence_score"] = df.get("delivery_divergence_score", 0.0)
                    df["delivery_accumulation_signal"] = df.get("delivery_accumulation_signal", 0.0)
                    df["ias"] = df.get("ias", 0.0)

                # 5. SMC Structural Flow (isolate errors so we still save indicators)
                try:
                    from myra_app.engine import SMCManager

                    df_lower = df.rename(columns=lambda x: x.lower())
                    df["fvg"] = SMCManager.calculate_fvg(df_lower)
                    bos, choch = SMCManager.calculate_market_structure(df_lower)
                    df["bos"] = bos
                    df["choch"] = choch
                except Exception as e:
                    logger.debug(f"SMC calc failed for {sym}: {e}")
                    df["fvg"] = 0.0
                    df["bos"] = False
                    df["choch"] = False

                # 6. Save to Lake (Lowercase consistency)
                df.columns = [c.lower() for c in df.columns]
                df["symbol"] = sym
                # Guarantee `ias` exists so downstream consumers always have the column
                if "ias" not in df.columns:
                    df["ias"] = df.get("ias", 0.0)
                self.loader.indicators.save_indicators("precomputed", sym, df)

            except Exception as e:
                logger.debug(f"Skip {sym}: {e}")
                continue

        print("[+] Indicator Lake Updated.")
