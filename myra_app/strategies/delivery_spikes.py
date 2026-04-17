import pandas as pd
import numpy as np

def run(df: pd.DataFrame, funda: dict) -> dict:
    if "DeliveryPct" not in df.columns or len(df) < 50:
        return {"signal": False}

    try:
        df["del_per"] = pd.to_numeric(df["DeliveryPct"], errors="coerce").fillna(0)
        
        # FIX: If the database stores 45% as 0.45, auto-convert it to 45.0
        if df["del_per"].max() > 0 and df["del_per"].max() <= 1.0:
            df["del_per"] = df["del_per"] * 100

        avg_del = df["del_per"].rolling(window=20).mean()
        latest_del = df["del_per"].iloc[-1]
        latest_avg = avg_del.iloc[-1]

        ltp = df["Close"].iloc[-1]
        prev_close = df["Close"].iloc[-2]

        # ==========================================
        # TEST MODE: Lowered thresholds to catch normal volume
        # We only look for a tiny 1.1x bump instead of a massive 1.5x spike
        # ==========================================
        is_green_spike = (
            latest_del > (latest_avg * 1.1) and latest_del > 10 and ltp >= prev_close
        )

        if is_green_spike:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(ltp, 2),
                    "Spike": "TEST MODE SPIKE",
                    "Del%": f"{round(latest_del, 1)}%",
                    "AvgDel%": f"{round(latest_avg, 1)}%",
                },
            }

    except Exception:
        pass
    return {"signal": False}
