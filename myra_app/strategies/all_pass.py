import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: All Pass (Unbiased Breadth)
    Always returns signal=True so the Engine can count EVERY stock's Stage.
    """
    if len(df) < 150:
        return {"signal": False}

    return {"signal": True, "metrics": {"LTP": round(df["Close"].iloc[-1], 2)}}
