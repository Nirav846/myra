import pandas as pd
import numpy as np

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Institutional Delivery Spikes (Absorption v3)
    Flags massive delivery spikes relative to recent average.
    Highly sensitive to bottom-fishing when used in pipes.
    """
    # FIX: Check for the correct CamelCase column from DataAdapter
    if "DeliveryPct" not in df.columns or len(df) < 50:
        return {"signal": False}

    try:
        # FIX: Use the correct column name here too
        df["del_per"] = pd.to_numeric(df["DeliveryPct"], errors="coerce").fillna(0)

        # We look at 20-day average delivery
        avg_del = df["del_per"].rolling(window=20).mean()
        latest_del = df["del_per"].iloc[-1]
        latest_avg = avg_del.iloc[-1]

        ltp = df["Close"].iloc[-1]
        prev_close = df["Close"].iloc[-2]

        # Check if at bottom (within 10%)
        l1y = funda.get("low_1y", 0)
        is_at_bottom = (ltp <= l1y * 1.10) if l1y > 0 else False

        # 1. THE TRADITIONAL SPIKE (Green Day + Spike)
        is_green_spike = (
            latest_del > (latest_avg * 1.5) and latest_del > 30 and ltp >= prev_close
        )

        # 2. THE ABSORPTION SPIKE (Red Day + Spike at Bottom)
        # If at bottom, we only need 1.8x spike to flag institutional interest.
        absorp_thresh = 1.8 if is_at_bottom else 2.5
        is_red_absorption = (
            latest_del > (latest_avg * absorp_thresh) and latest_del > 35
        )

        if is_green_spike or is_red_absorption:
            status = "GREEN SPIKE" if is_green_spike else "BOTTOM ABSORPTION"
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(ltp, 2),
                    "Spike": status,
                    "Del%": f"{round(latest_del, 1)}%",
                    "AvgDel%": f"{round(latest_avg, 1)}%",
                    "ROE": funda.get("ROE", "N/A"),
                    "MCap": funda.get("MCap", "N/A"),
                },
            }

    except Exception:
        pass
    return {"signal": False}
