import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Institutional Value
    Filters for attractive EV/EBITDA, P/S, and P/BV.
    """
    ev_ebitda = funda.get("EV_EBITDA")
    ps = funda.get("PS")
    pb = funda.get("PB")
    roe = funda.get("ROE")

    if ev_ebitda is None or ps is None or roe is None:
        return {"signal": False}

    # Logic:
    # Attractive EV/EBITDA (< 12)
    # Attractive P/S (< 2.0)
    # Profitable (ROE > 10%)
    is_attractive = (0 < ev_ebitda < 12) and (0 < ps < 2.0) and (roe > 10)

    if is_attractive:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(df["Close"].iloc[-1], 2),
                "EV/EBITDA": round(ev_ebitda, 1),
                "P/S": round(ps, 2),
                "P/BV": round(pb, 2) if pb else "N/A",
                "ROE": f"{roe}%",
            },
        }
    return {"signal": False}
