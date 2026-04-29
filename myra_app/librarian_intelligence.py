#!/usr/bin/env python
"""
MYRA Librarian Intelligence Layer (TRILOGY ERA)
Handles all heavy indicator computation using Parquet Lake.
"""

import logging
import os

import numpy as np
import pandas as pd
import pandas_ta as ta

from myra_core.utils.data_validation import enforce_index_contract, validate_dataframe

logger = logging.getLogger(__name__)


def safe_concat(df_list):
    clean = [
        df
        for df in df_list
        if df is not None and not df.empty and not df.isnull().all().all()
    ]
    if not clean:
        return pd.DataFrame()
    # Drop all-NaN columns from each DataFrame BEFORE concat (stops FutureWarning)
    clean = [df.dropna(axis=1, how="all") for df in clean]
    return pd.concat(clean, axis=0, ignore_index=True)


class LibrarianIntelligenceMixin:
    def precompute_indicators(self, as_of_date=None):
        """
        Unified API for retrieving indicators via Parquet Lake.
        FIXED: No duplicate index, safe concat, explicit schema.
        """
        active_symbols = self.get_active_universe()
        if not active_symbols:
            return pd.DataFrame()

        results_list = []

        for sym in active_symbols:
            try:
                df_node = self.loader.indicators.load_indicators("precomputed", sym)

                if df_node is None or df_node.empty:
                    continue

                df_node = enforce_index_contract(df_node, symbol=sym)

                if as_of_date:
                    df_node = df_node[df_node.index <= as_of_date]

                if df_node.empty:
                    continue

                # 🔥 TAKE LAST ROW SAFELY
                row = df_node.iloc[[-1]].copy()

                # 🔥 CRITICAL: move index → column
                row = row.reset_index()

                # Ensure symbol exists
                row["symbol"] = sym

                results_list.append(row)  # noqa: PG-APPEND

            except Exception as e:
                logger.debug(f"Failed to load indicator for {sym}: {e}")
                continue

        df_final = safe_concat(results_list)

        assert "symbol" in df_final.columns
        assert "date" in df_final.columns

        df_final = df_final.set_index(["symbol", "date"], drop=False)

        if not df_final.index.is_unique:
            dupes = df_final[df_final.index.duplicated(keep=False)]
            raise Exception(
                f"Duplicate (symbol, date) detected after aggregation:\n{dupes.tail(10)}"
            )

        return df_final

    def update_indicator_history(self):
        """
        Computes comprehensive indicators for ALL active stocks and saves to Parquet Lake.
        """
        if getattr(self, "read_only", False):
            return

        active_symbols = self.get_active_universe()
        if not active_symbols:
            print("[!] No active symbols found for indicator update.")
            return

        print(
            f"[MYRA] Updating Virtual Indicator Lake for {len(active_symbols)} symbols..."
        )

        total_syms = len(active_symbols)

        for i, sym in enumerate(active_symbols, 1):
            myra_log(i, total_syms, desc="Precomputing")

            try:
                df = self.get_ohlcv(sym)

                if df is None or len(df) < 30:
                    continue

                df.columns = [c.capitalize() for c in df.columns]
                df = df.loc[:, ~df.columns.duplicated()]

                # Normalize index
                df.index = pd.to_datetime(df.index, errors="coerce").normalize()
                df = df[df.index.notna()]

                df.sort_index(inplace=True)
                df = df.loc[~df.index.duplicated(keep="last")]

                # 🔥 dtype safety (fix warning + hidden bugs)
                for col in ["Volume", "Delivery_qty"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                # Contract enforcement
                df = enforce_index_contract(df, symbol=sym)

                df = validate_dataframe(df, context=f"Indicator Engine: {sym}")

                # Core calculations
                df["inst_vol"] = 0.0

                df["sma20"] = ta.sma(df["Close"], length=20)
                df["sma50"] = ta.sma(df["Close"], length=50)
                df["sma200"] = ta.sma(df["Close"], length=200)
                df["rsi"] = ta.rsi(df["Close"], length=14)
                df["atr20"] = ta.atr(df["High"], df["Low"], df["Close"], length=20)

                df["vol_sma50"] = ta.sma(df["Volume"], length=50)

                if "Delivery_qty" in df.columns:
                    df["avg_deliv_20d"] = ta.sma(df["Delivery_qty"], length=20)
                    df["rdv"] = df["Delivery_qty"] / df["avg_deliv_20d"].replace(0, 1)
                else:
                    df["rdv"] = 1.0

                try:
                    if "Delivery_qty" in df.columns and "Volume" in df.columns:
                        df["delivery_pct"] = (
                            df["Delivery_qty"] / df["Volume"].replace(0, np.nan)
                        ).fillna(0.0) * 100.0
                    elif "Delivery_pct" in df.columns:
                        df["delivery_pct"] = df["Delivery_pct"].fillna(0.0)
                    else:
                        df["delivery_pct"] = 0.0

                    df["vcp"] = 1.0 - (
                        df["atr20"] / df["sma20"].replace(0, np.nan)
                    ).fillna(0.0)
                    df["vcp"] = df["vcp"].clip(0.0, 1.0)

                    rolling_mean = df["delivery_pct"].rolling(20, min_periods=1).mean()
                    rolling_std = (
                        df["delivery_pct"]
                        .rolling(20, min_periods=1)
                        .std()
                        .replace(0, np.nan)
                        .fillna(1.0)
                    )

                    df["delivery_divergence_score"] = (
                        (df["delivery_pct"] - rolling_mean) / rolling_std
                    ).fillna(0.0)
                    df["delivery_divergence_score"] *= df["vcp"]

                    try:
                        df["vwap"] = ta.vwap(
                            df["High"], df["Low"], df["Close"], df["Volume"]
                        )
                    except Exception:
                        pv = (df["Close"] * df["Volume"]).rolling(20).sum()
                        v = df["Volume"].rolling(20).sum().replace(0, np.nan)
                        df["vwap"] = (pv / v).fillna(df["Close"])

                    heavy_absorption = (
                        (df["delivery_pct"] > 50.0) & (df["Close"] > df["vwap"])
                    ).astype(float)

                    df["delivery_accumulation_signal"] = (
                        df["delivery_pct"] * heavy_absorption
                    )

                    df["ias"] = 0.8 * df["delivery_divergence_score"] + 0.2 * (
                        df["delivery_accumulation_signal"] / 100.0
                    )

                    df["ias"] *= df["vcp"]

                except Exception:
                    df["ias"] = 0.0

                # SMC block
                try:
                    from myra_app.engine import SMCManager

                    df_lower = df.rename(columns=lambda x: x.lower())
                    df["fvg"] = SMCManager.calculate_fvg(df_lower)
                    bos, choch = SMCManager.calculate_market_structure(df_lower)

                    df["bos"] = bos
                    df["choch"] = choch

                except Exception:
                    df["fvg"] = 0.0
                    df["bos"] = False
                    df["choch"] = False

                # Save
                df.columns = [c.lower() for c in df.columns]
                df["symbol"] = sym

                self.loader.indicators.save_indicators("precomputed", sym, df)

            except Exception as e:
                logger.debug(f"Skip {sym}: {e}")
                continue

        print("[+] Indicator Lake Updated.")
