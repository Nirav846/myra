import pandas as pd
import numpy as np


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Insider Conviction Tracker (Velocity v2)
    Flags stocks where insiders are ramping up buying aggression.
    """
    # 1. VELOCITY METRICS
    # AV_Latest: Buying in last 5 days
    # AV_Accel: 3 (High), 2 (Moderate), 1 (Passive)
    latest_buy = funda.get("AV_Latest", 0)
    accel_score = funda.get("AV_Accel", 0)
    total_60d = funda.get("AV_Total", 0)

    # 2. SIGNAL LOGIC
    # We trigger if there's any active buying (Accel > 0)
    # But we want to prioritize Acceleration.
    has_insider_activity = accel_score > 0 or latest_buy > 0.5
    high_conviction_req = funda.get("require_high_conviction", False)

    # If high conviction is required, insider activity is strictly required
    if high_conviction_req and not has_insider_activity:
        return {"signal": False}

    # 3. TECHNICAL STABILITY
    # Price must be above 200 SMA
    ma200 = df["Close"].rolling(200).mean().iloc[-1] if len(df) >= 200 else 0
    is_above_200 = df["Close"].iloc[-1] >= ma200

    # 4. TACTICAL PLANNING
    # Only return signal if we have insider activity OR strong technical setup (above 200 SMA and not requiring high conviction)
    if has_insider_activity or (not high_conviction_req and is_above_200):
        # Even if lacking insider activity, fallback requires it to be technically strong
        if not is_above_200:
            return {"signal": False}

        entry = round(df["High"].iloc[-1] * 1.002, 2)
        sl = round(df["Low"].iloc[-1] * 0.99, 2)
        risk = entry - sl
        target = round(entry + (5 * risk), 2)

        # UI Gauge: Fast Forward Emojis
        gauge = "⏩" * accel_score if accel_score > 0 else "⚪"

        return {
            "signal": True,
            "tactics": {"entry": entry, "sl": sl, "target": target},
            "metrics": {
                "LTP": round(df["Close"].iloc[-1], 2),
                "Latest_Buy": f"₹{round(latest_buy, 1)} Cr",
                "AV_Gauge": gauge,
                "Total_60d": f"₹{round(total_60d, 1)} Cr",
                "Stage": funda.get("Stage", "-"),
                "ROE": funda.get("ROE", "N/A"),
                "Conviction": "High" if has_insider_activity else "Technical",
            },
        }

    return {"signal": False}
