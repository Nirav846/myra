#!/usr/bin/env python
import numpy as np
import pandas as pd


class VCPScanner:
    """
    Volatility Contraction Pattern (VCP) Scanner
    Targets symbols exiting a 3-6 month tight base.
    """

    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 100:
            return {"signal": False}

        try:
            c = df["Close"]
            h = df["High"]
            l = df["Low"]
            v = df["Volume"]

            # 1. Price above 200 SMA (Long-term Uptrend)
            sma200 = df["Close"].rolling(200).mean().iloc[-1]
            if c.iloc[-1] < sma200:
                return {"signal": False}

            # 2. Tightness: Last 10 days within 5% range
            max_10 = c.iloc[-10:].max()
            min_10 = c.iloc[-10:].min()
            tightness = (max_10 / min_10) - 1
            if tightness > 0.06:
                return {"signal": False}

            # 3. Volume Dry-up: Last 3 days volume < 70% of 20d Avg
            avg_v = v.rolling(20).mean().iloc[-1]
            v_dry = (v.iloc[-3:] < (avg_v * 0.7)).all()
            if not v_dry:
                return {"signal": False}

            # 4. ATR Contraction: ATR(5) < ATR(20) * 0.8
            # Simple range as ATR proxy
            tr = h - l
            atr5 = tr.iloc[-5:].mean()
            atr20 = tr.iloc[-20:].mean()
            if atr5 > (atr20 * 0.85):
                return {"signal": False}

            return {
                "signal": True,
                "metrics": {
                    "Strategy": "VCP_Base",
                    "Tightness": f"{round(tightness*100, 1)}%",
                    "ATR_Cont": round(atr5 / atr20, 2),
                },
            }
        except:
            return {"signal": False}
