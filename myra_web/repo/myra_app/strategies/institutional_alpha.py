import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Institutional Alpha (CAR-Based)
    This is a placeholder strategy that identifies potential 'Whale' candidates
    for the Intelligence Buffer to deep-dive.
    """
    # We require at least 100 days of history for trend context
    if len(df) < 100:
        return {"signal": False}

    latest = df.iloc[-1]

    # Technical Pre-Filter: Focus on stocks with positive trend or accumulation
    # (Stage 1 or Stage 2 proxy)
    sma50 = df["Close"].rolling(50).mean().iloc[-1]
    sma200 = df["Close"].rolling(200).mean().iloc[-1]

    # We pass anything that isn't a complete disaster (Stage 4)
    # The actual 'Alpha' will be calculated by the InstitutionalManager
    if latest["Close"] > sma200 or latest["Close"] > sma50:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(latest["Close"], 2),
                "Score": 75,  # Base score for technical pass
                "Pattern": "Institutional Setup",
                "Money_Flow": f"₹{round(latest['Volume'] * latest['Close'] / 10000000, 1)}Cr",
            },
        }

    return {"signal": False}
