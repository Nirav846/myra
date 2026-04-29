#!/usr/bin/env python
import numpy as np
import pandas as pd


class LiquidityVacuumScanner:
    """
    Liquidity Vacuum Move Scanner
    Detects breakouts from low-volume nodes (Price Imbalance).
    """

    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 60:
            return {"signal": False}

        try:
            # 1. Identify "Low Volume Zone" (Relative to 60-day average)
            # Price spent time in a range with very low relative volume
            v = df["Volume"]
            c = df["Close"]

            avg_vol_60 = v.iloc[-60:].mean()

            # Look for a 5-day tight period where volume was < 60% of average
            tight_vol = (v.iloc[-10:-5] < (avg_vol_60 * 0.6)).all()

            # 2. Breakout: Current price > Max(last 10 days) AND Volume > 1.5x Avg
            is_breakout = c.iloc[-1] > c.iloc[-10:-1].max()
            is_volume_confirmed = v.iloc[-1] > (avg_vol_60 * 1.5)

            if tight_vol and is_breakout and is_volume_confirmed:
                return {
                    "signal": True,
                    "metrics": {
                        "Strategy": "Liq_Vacuum",
                        "Pre_Vol_Dry": "Extreme",
                        "Breakout_Vol": round(v.iloc[-1] / avg_vol_60, 1),
                    },
                }
        except:
            pass
        return {"signal": False}
