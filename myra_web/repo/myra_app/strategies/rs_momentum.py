import pandas as pd
import numpy as np


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Mashrani RS Momentum & Phelps Base (v1)
    - Short-term RS > Long-term RS (Acceleration)
    - Phelps Base: Near 2-Year Highs
    """
    if len(df) < 250:
        return {"signal": False}

    try:
        c = df["Close"]

        # 1. THE PHELPS BASE (2-Year High Consolidation)
        # Check if we are within 5% of the 500-day high
        high_2y = c.tail(500).max()
        is_phelps_base = c.iloc[-1] >= (high_2y * 0.95)

        # 2. THE MASHRANI RS MOMENTUM (Acceleration)
        # We assume the engine has pre-calculated the raw RS scores or we calculate local performance
        # RS = Price / Nifty (we'll use local price performance as a proxy if Nifty is not in df)

        ret_6m = (c.iloc[-1] / c.iloc[-126]) - 1
        ret_1m = (c.iloc[-1] / c.iloc[-21]) - 1

        # Acceleration: 1-month annualized return > 6-month annualized return
        # (Simplified: 1m Return * 6 > 6m Return)
        is_accelerating = (ret_1m * 6) > ret_6m

        # Must be in a structural uptrend
        ma200 = ta.sma(c, length=200).iloc[-1]
        is_uptrend = c.iloc[-1] > ma200

        if is_uptrend and (is_phelps_base or is_accelerating):
            status = "PHELPS BASE" if is_phelps_base else "RS ACCEL"
            if is_phelps_base and is_accelerating:
                status = "ELITE BREAKOUT"

            entry = round(df["High"].iloc[-1] * 1.002, 2)
            atr = ta.atr(df["High"], df["Low"], c, length=20).iloc[-1]
            sl = round(c.iloc[-1] - (1.5 * atr), 2)

            return {
                "signal": True,
                "tactics": {
                    "entry": entry,
                    "sl": sl,
                    "target": round(entry + (4 * (entry - sl)), 2),
                },
                "metrics": {
                    "LTP": round(c.iloc[-1], 2),
                    "Type": status,
                    "RS_1M": f"{round(ret_1m * 100, 1)}%",
                    "RS_6M": f"{round(ret_6m * 100, 1)}%",
                    "ROE": funda.get("ROE", "N/A"),
                },
            }

    except Exception:
        pass
    return {"signal": False}
