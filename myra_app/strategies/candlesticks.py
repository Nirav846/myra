import pandas as pd
import numpy as np
import pandas_ta as ta


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: High-Conviction Reversal Patterns (v2)
    Uses precise price action rules for:
    - Hammer / Pin Bar
    - Bullish Engulfing
    - Morning Star
    """
    if len(df) < 5:
        return {"signal": False}

    try:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        p2 = df.iloc[-3]

        o, h, l, c = latest["Open"], latest["High"], latest["Low"], latest["Close"]
        po, ph, pl, pc = prev["Open"], prev["High"], prev["Low"], prev["Close"]

        body = abs(c - o)
        range_tot = h - l
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        pattern = None

        # 1. HAMMER / PIN BAR
        # Small body, long lower wick (2x body), little to no upper wick
        if lower_wick >= (2 * body) and upper_wick <= (0.1 * range_tot) and body > 0:
            pattern = "Hammer"

        # 2. BULLISH ENGULFING
        # Previous day was red, current day is green and completely engulfs the previous body
        elif c > o and pc < po and c > po and o < pc:
            pattern = "Engulfing"

        # 3. MORNING STAR (3-candle pattern)
        elif (
            c > o
            and p2["Close"] < p2["Open"]
            and body > (abs(p2["Close"] - p2["Open"]) * 0.5)
        ):
            # Middle candle is small (star)
            if abs(pc - po) < (abs(p2["Close"] - p2["Open"]) * 0.3):
                pattern = "Morning Star"

        if pattern:
            # TACTICS
            entry = round(h * 1.002, 2)
            sl = round(l * 0.99, 2)
            risk = entry - sl
            target = round(entry + (3 * risk), 2)

            return {
                "signal": True,
                "tactics": {"entry": entry, "sl": sl, "target": target},
                "metrics": {
                    "LTP": round(c, 2),
                    "Pattern": pattern,
                    "Strength": "High" if pattern == "Engulfing" else "Moderate",
                    "Context": funda.get("Stage", "-"),
                },
            }

    except Exception:
        pass
    return {"signal": False}
