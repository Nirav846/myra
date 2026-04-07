import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Value Strategy: Low PE + High ROE (Buffett Style)
    """
    # 1. Fundamental Filters
    pe = funda.get("PE")
    roe = funda.get("ROE")
    mcap = funda.get("MCap", 0)

    if pe is None or roe is None:
        return {"signal": False}

    # Logic:
    # - ROE > 15% (High efficiency)
    # - PE < 25 (Reasonable valuation)
    # - MCap > 500 Cr (Exclude tiny illiquid stocks)
    is_value = (roe > 15) and (0 < pe < 25) and (mcap > 500)

    # 2. Technical Confirmation
    # We only want value stocks that are NOT in a free-fall
    # Price should be above 200 SMA
    if len(df) >= 200:
        ma200 = df["Close"].rolling(200).mean().iloc[-1]
        is_uptrend = df["Close"].iloc[-1] > ma200
    else:
        is_uptrend = True  # Not enough data to judge, allow it

    if is_value and is_uptrend:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(df["Close"].iloc[-1], 2),
                "ROE": f"{roe}%",
                "PE": pe,
                "MCap": f"{mcap}Cr",
                "Trend": "Bullish",
            },
        }
    return {"signal": False}
