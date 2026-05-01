import numpy as np
import pandas as pd


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Institutional Deal Momentum (v1.0)
    Triggers when Large Deals (Bulk/Block) coincide with technical strength.
    """
    try:
        c = df["Close"]
        h = df["High"]

        # 1. Institutional Context
        intensity = funda.get("Inst_Intensity", 0)
        stage = funda.get("Stage", "-")

        # 2. Breakout Context
        cons_high = h.tail(20).max()
        is_breakout = c.iloc[-1] >= cons_high * 0.99

        # 3. Logic: Stage 2 + High Intensity (> 0.5% of MCap)
        if "Stage 2" in stage and intensity >= 0.5 and is_breakout:
            entry = round(c.iloc[-1] * 1.005, 2)
            atr = funda.get("atr20", c.iloc[-1] * 0.03)
            sl = round(c.iloc[-1] - (2.0 * atr), 2)
            risk = entry - sl
            target = round(entry + (3 * risk), 2)

            return {
                "signal": True,
                "tactics": {"entry": entry, "sl": sl, "target": target},
                "metrics": {
                    "Context": "INSTITUTIONAL BREAKOUT",
                    "Inst_Intensity": f"{intensity}%",
                    "Stage": stage,
                },
            }

    except Exception:
        pass
    return {"signal": False}
