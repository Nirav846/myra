import pandas as pd
import pandas_ta as ta


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Bollinger Band Squeeze Strategy
    Identifies low volatility periods ready for a breakout.
    """
    if len(df) < 50:
        return {"signal": False}

    # Calculate Bollinger Bands
    bb = ta.bbands(df["Close"], length=20, std=2)
    if bb is None:
        return {"signal": False}

    df["BB_Upper"] = bb["BBU_20_2.0"]
    df["BB_Lower"] = bb["BBL_20_2.0"]
    df["BB_Width"] = (df["BB_Upper"] - df["BB_Lower"]) / df["Close"]

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # Logic: Width is at its lowest in 20 days (Squeeze)
    # and Price is crossing above the upper band
    is_squeeze = latest["BB_Width"] <= df["BB_Width"].rolling(20).min().iloc[-1]
    is_breakout = (
        latest["Close"] > latest["BB_Upper"] and prev["Close"] <= prev["BB_Upper"]
    )

    if is_breakout:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(latest["Close"], 2),
                "BB_Width": f"{round(latest['BB_Width']*100, 2)}%",
                "RSI": round(ta.rsi(df["Close"]).iloc[-1], 1),
                "MCap": funda.get("MCap", "N/A"),
            },
        }
    return {"signal": False}
