import pandas as pd
import numpy as np


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Delivery Z-Score (Institutional Outlier Detection)
    Uses Z-Score to find statistically significant spikes in delivery percentage.
    """
    # 1. Validation: Ensure we have delivery data and enough history
    if "delivery_percent" not in df.columns or len(df) < 60:
        return {"signal": False}

    try:
        # 2. Convert and Clean
        df["del_per"] = pd.to_numeric(df["delivery_percent"], errors="coerce").fillna(0)

        # 3. Statistical Profiling (50-day lookback)
        window = 50
        rolling_mean = df["del_per"].rolling(window=window).mean()
        rolling_std = df["del_per"].rolling(window=window).std()

        latest_del = df["del_per"].iloc[-1]
        latest_mean = rolling_mean.iloc[-1]
        latest_std = rolling_std.iloc[-1]

        if latest_std == 0:
            return {"signal": False}

        # 4. Z-Score Calculation
        z_score = (latest_del - latest_mean) / latest_std

        # LOGIC:
        # - Statistical Outlier: Z-Score > 2.0 (Top 2.5% of historical days)
        # - Bullish Support: Price must be >= previous close (No distribution)
        # - Minimum floor: Delivery % must be at least 30% absolute

        is_outlier = z_score > 2.0
        is_bullish = df["Close"].iloc[-1] >= df["Close"].iloc[-2]
        is_significant = latest_del > 30

        if is_outlier and is_bullish and is_significant:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(df["Close"].iloc[-1], 2),
                    "Z-Score": round(z_score, 2),
                    "Del%": f"{round(latest_del, 1)}%",
                    "Avg_Del%": f"{round(latest_mean, 1)}%",
                    "ROE": funda.get("ROE", "N/A"),
                    "MCap": funda.get("MCap", "N/A"),
                },
            }

    except Exception:
        pass

    return {"signal": False}
