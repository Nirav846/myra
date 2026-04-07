import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Whale Watcher (Institutional Favorites)
    Finds stocks with high institutional holding and bullish momentum.
    """
    inst = funda.get("Inst_Hold")
    insid = funda.get("Insider_Hold")
    mcap = funda.get("MCap", 0)

    # Technical Filter (Bullish Trend)
    ma200 = df["Close"].rolling(200).mean().iloc[-1] if len(df) >= 200 else 0
    is_bullish = df["Close"].iloc[-1] > ma200

    # Market Cap Filter (Liquidity)
    is_liquid = mcap > 1000 if mcap else True  # Allow if mcap is unknown for discovery

    # DISCOVERY LOGIC:
    # If inst data is unknown (None/NULL), we allow it to pass for Lazy-Fetch
    # If inst data exists, it must be > 25%
    is_inst_ok = True
    if inst is not None and inst != "NULL" and not pd.isna(inst):
        is_inst_ok = inst > 25

    if is_bullish and is_liquid and is_inst_ok:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(df["Close"].iloc[-1], 2),
                "Inst%": f"{inst}%" if inst else "N/A",
                "Insider%": f"{insid}%" if insid else "N/A",
                "MCap": f"{mcap}Cr" if mcap else "N/A",
                "ROE": funda.get("ROE", "N/A"),
            },
        }

    return {"signal": False}
