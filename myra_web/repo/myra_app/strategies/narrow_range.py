import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Narrow Range Strategy (NR7 / NR4)
    Identifies stocks with contracting volatility ready for an expansion.
    """
    if len(df) < 10:
        return {"signal": False}

    # Calculate daily range (High - Low)
    df["range"] = df["High"] - df["Low"]

    latest_range = df["range"].iloc[-1]

    # Logic: Smallest range in last 7 days (NR7)
    is_nr7 = latest_range <= df["range"].iloc[-7:].min()
    # Logic: Smallest range in last 4 days (NR4)
    is_nr4 = latest_range <= df["range"].iloc[-4:].min()

    if is_nr7 or is_nr4:
        # Check for price trend (we want it to break out upwards)
        # We look for price near 52W high or above 50 SMA
        ma50 = df["Close"].rolling(50).mean().iloc[-1] if len(df) >= 50 else 0
        is_bullish = df["Close"].iloc[-1] > ma50

        if is_bullish:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(df["Close"].iloc[-1], 2),
                    "Type": "NR7 (Coiled)" if is_nr7 else "NR4",
                    "Range": round(latest_range, 2),
                    "RSI": round(df["Close"].iloc[-1], 1),  # Placeholder for indicator
                    "MCap": funda.get("MCap", "N/A"),
                },
            }

    return {"signal": False}
