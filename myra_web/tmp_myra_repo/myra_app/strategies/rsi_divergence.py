import numpy as np
import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: RSI Bullish Divergence
    Detects when Price makes a Lower Low but RSI makes a Higher Low.
    """
    if len(df) < 50:
        return {"signal": False}

    try:
        # 1. Indicators
        df["RSI"] = ta.rsi(df["Close"], length=14)
        close = df["Close"]
        rsi = df["RSI"]

        # 2. Find local troughs (Low points)
        # We look for a V-shape in the last 2-15 days
        def is_trough(series, idx):
            if idx < 2 or idx > len(series) - 3:
                return False
            return (
                series.iloc[idx] < series.iloc[idx - 1]
                and series.iloc[idx] < series.iloc[idx + 1]
            )

        # Optimized with list comprehension
        troughs = [i for i in range(len(df) - 20, len(df) - 1) if is_trough(close, i)]

        if len(troughs) < 2:
            return {"signal": False}

        # 3. Analyze the last two significant troughs
        t2 = troughs[-1]  # Most recent trough
        t1 = troughs[-2]  # Previous trough

        # LOGIC:
        # Price at T2 is lower than T1 (Lower Low)
        price_lower = close.iloc[t2] < close.iloc[t1]

        # RSI at T2 is higher than T1 (Higher Low - Divergence!)
        rsi_higher = rsi.iloc[t2] > rsi.iloc[t1]

        # Confirmation: RSI must be emerging from oversold territory (< 40)
        is_emerging = rsi.iloc[t2] < 45

        # Current price action: Price should be moving up from the recent trough
        is_reversing = close.iloc[-1] > close.iloc[t2]

        if price_lower and rsi_higher and is_emerging and is_reversing:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(close.iloc[-1], 2),
                    "Pattern": "Bullish Divergence",
                    "RSI_Now": round(rsi.iloc[-1], 1),
                    "RSI_Trough": round(rsi.iloc[t2], 1),
                    "ROE": funda.get("ROE", "N/A"),
                    "MCap": funda.get("MCap", "N/A"),
                },
            }

    except Exception:
        pass

    return {"signal": False}
