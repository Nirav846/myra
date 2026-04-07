import pandas as pd
import pandas_ta as ta


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Momentum Strategy: MACD Crossover + RSI Strength
    """
    if len(df) < 50:
        return {"signal": False}

    # Calculate MACD
    macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
    if macd is None:
        return {"signal": False}

    df["MACD"] = macd["MACD_12_26_9"]
    df["MACD_Signal"] = macd["MACDs_12_26_9"]
    df["RSI"] = ta.rsi(df["Close"], length=14)

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # Logic: MACD Line crossed above Signal Line AND RSI is strong (> 60)
    is_macd_cross = (latest["MACD"] > latest["MACD_Signal"]) and (
        prev["MACD"] <= prev["MACD_Signal"]
    )
    is_strong = latest["RSI"] > 60

    if is_macd_cross and is_strong:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(latest["Close"], 2),
                "RSI": round(latest["RSI"], 1),
                "MACD": round(latest["MACD"], 2),
                "ROE": funda.get("ROE", "N/A"),
                "MCap": funda.get("MCap", "N/A"),
            },
        }
    return {"signal": False}
