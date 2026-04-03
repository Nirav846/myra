import pandas as pd
import numpy as np
from myra_app.ml_engine import AEONEngine

class Strategy:
    """
    AEON: Evolutionary SMC Agent (ML-1)
    Provides optimized conviction levels for Entry, Scale-in, and Exit
    based on historical 'Winning Genes'.
    """
    def __init__(self, librarian=None):
        self.name = "AEON Agent Signals"
        self.engine = None
        self.librarian = librarian

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        if df.empty:
            return {"signal": False}

        if not self.engine:
            # PKScreener v2.5 fix: Ensure engine loads correctly
            self.engine = AEONEngine(self.librarian)

        # Get Agent's decision
        try:
            conviction = self.engine.get_conviction(funda.get("symbol"), df, funda=funda)
        except Exception:
            conviction = "N/A"
            
        # Map conviction level to Stars and SMC Phases
        smc_map = {
            "TACTICAL (25%)": "Basing",
            "CORE LOAD (50%)": "Basing",
            "CONVICTION (100%)": "Ignition"
        }
        stars_map = {
            "TACTICAL (25%)": "**",
            "CORE LOAD (50%)": "***",
            "CONVICTION (100%)": "*****"
        }
        
        # HIDE EXIT NOISE: Only return True for actual signals
        has_signal = conviction not in ["EXIT / Stay Out", "N/A", "Unknown"]
        
        # Calculate Floor Gap % (LTP vs D-POC)
        ltp = df["Close"].iloc[-1]
        dpoc = funda.get("d_poc", 0)
        floor_gap = 0
        if dpoc > 0:
            floor_gap = ((ltp - dpoc) / dpoc) * 100

        return {
            "signal": has_signal,
            "metrics": {
                "Strategy": "AEON-SMC",
                "AEON_Conviction": conviction,
                "SMC": smc_map.get(conviction, "-"),
                "Stars": stars_map.get(conviction, "*"),
                "Floor_Gap%": floor_gap,
                "Type": "ML-Agent"
            }
        }
