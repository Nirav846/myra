from typing import Any, Dict

import numpy as np
import pandas as pd


class Strategy:
    """
    NSE Surpriver v2: Multi-Window Institutional Accumulation & Anomaly Detection.
    Inspired by tradytics/surpriver.
    Detects 'Silent Accumulation' across 5, 10, 15, 20, and 30-day windows.
    """

    def __init__(self, librarian=None):
        self.name = "NSE Surpriver v2"
        self.librarian = librarian
        self.windows = [5, 10, 15, 20, 30]

    def run(self, df: pd.DataFrame, funda: Dict[str, Any]) -> Dict[str, Any]:
        if df.empty or len(df) < 40:
            return {"signal": False}

        try:
            df = df.copy()
            # 1. BASE FEATURES (v2.5 Foundation)
            df["vol_ratio"] = df["Volume"] / df["Volume"].rolling(20).mean()
            df["deliv_ratio"] = df["delivery_qty"] / df["Volume"]
            df["return_1d"] = df["Close"].pct_change()

            df["price_range"] = (df["High"] - df["Low"]) / df["Close"]
            df["range_ma"] = df["price_range"].rolling(20).mean()
            df["tight_range"] = df["price_range"] < df["range_ma"]

            # 2. DAILY ACCUMULATION SCORE (Z-Score Based)
            # BALANCED WEIGHTING (v2.6): 0.55 Deliv + 0.30 Vol + 0.15 Low Price Move
            def zscore(series, window=20):
                std = series.rolling(window).std()
                # Handle zero std to avoid NaNs
                return (series - series.rolling(window).mean()) / std.replace(0, 0.001)

            df["z_vol"] = zscore(df["vol_ratio"])
            df["z_deliv"] = zscore(df["delivery_qty"])

            df["daily_acc_score"] = (
                0.55 * df["z_deliv"].fillna(0)
                + 0.30 * df["z_vol"].fillna(0)
                + 0.15 * (1 - df["return_1d"].abs().fillna(0))
            )

            # 3. MULTI-WINDOW CONSISTENCY ENGINE
            active_windows = 0
            for w in self.windows:
                acc_days = (
                    (df["daily_acc_score"] > 0.3).rolling(w).sum()
                )  # Lowered to 0.3
                vol_mean = df["vol_ratio"].rolling(w).mean()

                # Window Signal: 40% consistency in volume accumulation
                window_signal = (acc_days >= w * 0.4) & (vol_mean > 1.0)
                if window_signal.iloc[-1]:
                    active_windows += 1

            # 4. ABSORPTION DETECTION (Supply Check)
            df["buying_wick"] = (df["Close"] > df["Open"]) & (
                df["Low"] < df["Low"].shift(1).fillna(df["Low"])
            )
            absorption_consistency = 0
            for w in self.windows:
                if (df["buying_wick"].rolling(w).sum() >= w * 0.3).iloc[
                    -1
                ]:  # Lowered to 30%
                    absorption_consistency += 1

            # 5. FINAL ANOMALY SCORE
            consistency_score = active_windows / len(self.windows)
            absorp_score = absorption_consistency / len(self.windows)

            final_anomaly_score = (0.6 * consistency_score) + (0.4 * absorp_score)

            # TRIGGER THRESHOLD (Institutional Elite)
            # 2+ windows AND Anomaly_Score >= 0.51 AND active Delivery Intensity (RDV)
            # We removed AD_Flow as it was too restrictive for turnaround candidates.
            rdv = funda.get("RDV", 0)
            has_signal = (
                (active_windows >= 2) and (final_anomaly_score >= 0.51) and (rdv > 1.2)
            )

            # Pre-Filter: Only Quality Zones
            low_1y = funda.get("low_1y", 0)
            if low_1y > 0:
                near_base = (df["Close"].iloc[-1] / low_1y) < 1.5  # Within 50% of low
                if not near_base:
                    has_signal = False

            # 6. ENRICHMENT FOR UI
            c = df["Close"]
            ret_1y = (
                (c.iloc[-1] / c.iloc[-250])
                if len(c) >= 250
                else (c.iloc[-1] / c.iloc[0])
            )
            ret_6m = (
                (c.iloc[-1] / c.iloc[-125])
                if len(c) >= 125
                else (c.iloc[-1] / c.iloc[0])
            )
            ret_3m = (
                (c.iloc[-1] / c.iloc[-63]) if len(c) >= 63 else (c.iloc[-1] / c.iloc[0])
            )
            rs_score = (ret_1y * 0.4) + (ret_6m * 0.3) + (ret_3m * 0.3)

            tightness = round(df["price_range"].tail(20).mean() * 100, 2)

            return {
                "signal": has_signal,
                "metrics": {
                    "Anomaly_Score": round(final_anomaly_score, 2),
                    "Active_Windows": f"{active_windows}/{len(self.windows)}",
                    "Absorption": f"{round(absorp_score * 100, 1)}%",
                    "Tightness": f"{tightness}%",
                    "RS_Raw": round(rs_score, 3),
                    "SMC": "Basing" if active_windows > 3 else "Accumulation",
                    "Type": "Quant-Anomaly",
                },
            }

        except Exception:
            # print(f"DEBUG: Surpriver Error: {e}")
            return {"signal": False}
