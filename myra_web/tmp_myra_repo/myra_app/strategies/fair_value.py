import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Fair Value Buy Opportunities
    Finds high-quality stocks trading near their long-term support (200 SMA).
    """
    if len(df) < 200:
        return {"signal": False}

    # 1. Technical Parameters
    c = df["Close"]
    ma200 = ta.sma(c, length=200)
    latest_c = c.iloc[-1]
    latest_200 = ma200.iloc[-1]

    # 2. Fundamental Parameters
    roe = funda.get("ROE")
    pe = funda.get("PE")
    pb = funda.get("PB")

    # LOGIC:
    # - "Near Value": Price is between 0% and 7% ABOVE the 200 SMA
    is_near_support = latest_200 <= latest_c <= (latest_200 * 1.07)

    # - "Quality": ROE > 15%
    # (Discovery: Allow unknown ROE to pass for Lazy-Fetch)
    is_quality = (roe > 15) if (roe and roe != "NULL" and not pd.isna(roe)) else True

    # - "Fair Price": PE < 30 or PB < 4
    is_fair = (pe < 30) if (pe and pe != "NULL" and not pd.isna(pe)) else True

    if is_near_support and is_quality and is_fair:
        dist_to_ma = round(((latest_c / latest_200) - 1) * 100, 1)
        return {
            "signal": True,
            "metrics": {
                "LTP": round(latest_c, 2),
                "Above_200MA": f"{dist_to_ma}%",
                "ROE": f"{roe}%" if roe else "N/A",
                "PE": pe if pe else "N/A",
                "PB": pb if pb else "N/A",
            },
        }

    return {"signal": False}
