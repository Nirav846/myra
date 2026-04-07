import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    if len(df) < 50:
        return {"signal": False}
    try:
        c = df["Close"]
        # Use available history for RS if 1y is not yet backfilled
        ret_1y = (
            (c.iloc[-1] / c.iloc[-250]) if len(c) >= 250 else (c.iloc[-1] / c.iloc[0])
        )
        ret_9m = (
            (c.iloc[-1] / c.iloc[-190]) if len(c) >= 190 else (c.iloc[-1] / c.iloc[0])
        )
        ret_6m = (
            (c.iloc[-1] / c.iloc[-125]) if len(c) >= 125 else (c.iloc[-1] / c.iloc[0])
        )
        ret_3m = (
            (c.iloc[-1] / c.iloc[-63]) if len(c) >= 63 else (c.iloc[-1] / c.iloc[0])
        )

        rs_score = (ret_1y * 0.4) + (ret_9m * 0.2) + (ret_6m * 0.2) + (ret_3m * 0.2)

        # Trend Check: Use precomputed SMA from funda for data resilience
        s200 = funda.get("sma200", 0)
        s150 = funda.get("sma150", 0)

        if s200 and s200 > 0:
            is_uptrend = c.iloc[-1] > s200
        elif s150 and s150 > 0:
            is_uptrend = c.iloc[-1] > s150
        else:
            # Fallback to 50 SMA if history is very short
            is_uptrend = c.iloc[-1] > df["Close"].rolling(50).mean().iloc[-1]

        if is_uptrend:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(c.iloc[-1], 2),
                    "RS_Raw": round(rs_score, 3),
                    "ROE": funda.get("ROE", "N/A"),
                    "PE": funda.get("PE", "N/A"),
                    "MCap": funda.get("MCap", "N/A"),
                },
            }
    except:
        pass
    return {"signal": False}
