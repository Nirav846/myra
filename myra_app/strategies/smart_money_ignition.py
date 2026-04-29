import numpy as np
import pandas as pd


class Strategy:
    """
    SMC-1: Smart Money Ignition
    Detects the transition from Institutional Accumulation (Phase 1)
    to Momentum Expansion (Phase 2).
    """

    def __init__(self):
        self.name = "Smart Money Ignition"

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        if df.empty or len(df) < 60:
            return {"signal": False}

        # Use precomputed SMC phase from engine/librarian
        smc_phase = funda.get("smc_phase", 0)
        d_poc = funda.get("d_poc", 0)

        # We trigger on Phase 2 (Ignition)
        is_ignition = smc_phase == 2

        if is_ignition:
            ltp = df["Close"].iloc[-1]
            dist_poc = round(((ltp - d_poc) / d_poc * 100), 2) if d_poc > 0 else 0

            return {
                "signal": True,
                "metrics": {
                    "Strategy": "SMC-Ignition",
                    "Phase": "Phase 2 (Ignition)",
                    "D-POC": round(d_poc, 2),
                    "Ignition_Dist": f"{dist_poc}%",
                    "stars": "****",  # High conviction for Phase 2
                },
            }

        return {"signal": False}
