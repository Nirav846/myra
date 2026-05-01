import numpy as np
import pandas as pd


class Strategy:
    def __init__(self):
        self.name = "Delivery Spikes"

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        if "delivery_percent" not in df.columns or len(df) < 50:
            return {"signal": False}

        try:
            df["del_per"] = pd.to_numeric(
                df["delivery_percent"], errors="coerce"
            ).fillna(0)

            # THE DECIMAL FIX (Restored)
            if df["del_per"].max() > 0 and df["del_per"].max() <= 1.0:
                df["del_per"] = df["del_per"] * 100

            avg_del = df["del_per"].rolling(window=20).mean()
            latest_del = df["del_per"].iloc[-1]
            latest_avg = avg_del.iloc[-1]
            ltp = df["close"].iloc[-1]
            prev_close = df["close"].iloc[-2]

            l1y = funda.get("low_1y", 0)
            is_at_bottom = (ltp <= l1y * 1.10) if l1y > 0 else False

            is_green_spike = latest_del > (latest_avg * 1.5) and latest_del > 30
            absorp_thresh = 1.5 if is_at_bottom else 2.0
            is_red_absorption = (
                latest_del > (latest_avg * absorp_thresh) and latest_del > 35
            )

            is_dip_accumulation = (
                latest_del > (latest_avg * 1.8)
                and latest_del > 40
                and ltp < prev_close
                and ltp > prev_close * 0.95
            )

            if is_green_spike or is_red_absorption or is_dip_accumulation:
                return {
                    "signal": True,
                    "metrics": {
                        "LTP": round(ltp, 2),
                        "Spike": (
                            "GREEN SPIKE"
                            if is_green_spike
                            else (
                                "BOTTOM ABSORPTION"
                                if is_red_absorption
                                else "DIP ACCUMULATION"
                            )
                        ),
                        "Del%": f"{round(latest_del, 1)}%",
                        "AvgDel%": f"{round(latest_avg, 1)}%",
                    },
                }
        except Exception:
            pass
        return {"signal": False}
