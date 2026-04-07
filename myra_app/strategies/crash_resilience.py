import pandas as pd
import numpy as np
import pandas_ta as ta


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Crash Resilience (The Underwater Ball v1)
    Finds stocks showing extreme Relative Strength + Delivery Absorption during a crash.
    """
    if len(df) < 250:
        return {"signal": False}

    try:
        # 1. DATA PREP
        c = df["Close"]
        if "delivery_qty" not in df.columns:
            return {"signal": False}
        dq = df["delivery_qty"]

        # 2. DEFINE CRASH WINDOW (Last 10 Trading Days)
        crash_df = df.tail(10)
        baseline_df = df.iloc[-250:-10]  # Last 1 year excluding the crash

        # 3. CALCULATE METRICS
        price_change = (crash_df["Close"].iloc[-1] / crash_df["Close"].iloc[0]) - 1
        avg_crash_deliv = crash_df["delivery_qty"].mean()
        avg_baseline_deliv = baseline_df["delivery_qty"].mean()

        deliv_spike_ratio = (
            avg_crash_deliv / avg_baseline_deliv if avg_baseline_deliv > 0 else 0
        )

        # 4. THE RESILIENCE FILTER
        # - Stock fell less than 2% (while market fell ~5%+)
        # - Delivery volume is > 50% higher than yearly average (Institutional absorption)
        is_resilient = price_change > -0.02
        is_absorbing = deliv_spike_ratio > 1.5

        if is_resilient and is_absorbing:
            # TACTICAL PLANNING
            # Entry above the crash period high
            crash_high = crash_df["High"].max()
            entry = round(crash_high * 1.002, 2)

            # SL at the crash period low (The floor)
            crash_low = crash_df["Low"].min()
            sl = round(crash_low * 0.998, 2)

            risk = entry - sl
            if risk <= 0:
                return {"signal": False}

            target = round(entry + (4 * risk), 2)  # Target 4R

            return {
                "signal": True,
                "tactics": {"entry": entry, "sl": sl, "target": target},
                "metrics": {
                    "LTP": round(c.iloc[-1], 2),
                    "Crash_Perf": f"{round(price_change * 100, 1)}%",
                    "Absorp_Ratio": f"{round(deliv_spike_ratio, 1)}x",
                    "ROE": funda.get("ROE", "N/A"),
                    "MCap": funda.get("MCap", "N/A"),
                },
            }

    except Exception:
        pass

    return {"signal": False}
