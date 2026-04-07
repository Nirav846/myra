import pandas as pd
import pandas_ta as ta


def run(df: pd.DataFrame, funda: dict) -> dict:
    if len(df) < 200:
        return {"signal": False}
    df["SMA50"] = ta.sma(df["Close"], length=50)
    df["SMA200"] = ta.sma(df["Close"], length=200)
    df["RSI"] = ta.rsi(df["Close"], length=14)
    latest = df.iloc[-1]
    is_bullish = (
        latest["Close"] > latest["SMA200"]
        and latest["SMA50"] > latest["SMA200"]
        and 40 <= latest["RSI"] <= 75
    )
    if is_bullish:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(latest["Close"], 2),
                "RSI": round(latest["RSI"], 1),
                "SMA200": round(latest["SMA200"], 2),
                "ROE": funda.get("ROE", "N/A"),
                "PE": funda.get("PE", "N/A"),
            },
        }
    return {"signal": False}
