import pandas as pd
import numpy as np

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Volume Spread Analysis (VSA) Momentum (v2.0)
    Finds Institutional Absorption using Spread/Volume relationship weighted by Delivery.
    """
    if len(df) < 50: return {"signal": False}
    
    try:
        # VSA Metrics (Precomputed in Engine/Librarian)
        rel_spread = funda.get("rel_spread", 1.0)
        rel_vol = funda.get("rel_vol", 1.0)
        closing_pos = funda.get("closing_pos", 0.5)
        del_pct = funda.get("delivery_percent", 0)
        vsa_intensity = funda.get("VSA_Intensity", 0)
        
        # 1. Effort vs Result (Bullish Case - Absorption)
        # High Volume + Narrow Spread + High Delivery = Institutional Absorption
        is_absorption = (rel_vol > 1.5) and (rel_spread < 0.8) and (closing_pos > 0.7) and (del_pct > 60)
        
        # 2. Bullish Strength (Wide Spread on High Volume/Intensity)
        is_strength = (vsa_intensity > 150) and (closing_pos > 0.8)
        
        # 3. Stopping Volume
        is_stopping = (rel_vol > 2.0) and (closing_pos > 0.5) and (funda.get("drawdown", 0) > 0.15)
        
        # 4. No Supply Test
        # Low Volume + Narrow Spread = Lack of selling pressure
        is_no_supply = (rel_vol < 0.7) and (rel_spread < 0.7) and (closing_pos > 0.5)

        vibe = "-"
        if is_absorption: vibe = "Absorption"
        elif is_strength: vibe = "Strength"
        elif is_stopping: vibe = "Stopping Vol"
        elif is_no_supply: vibe = "No Supply"
        
        if vibe != "-":
            c = df["Close"].iloc[-1]
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(c, 2),
                    "VSA_Vibe": vibe,
                    "Rel_Vol": f"{round(rel_vol, 1)}x",
                    "VSA_Int": round(vsa_intensity, 1),
                    "Del%": f"{round(del_pct)}%",
                    "Closing": f"{round(closing_pos * 100)}%"
                }
            }
            
    except Exception: pass
    return {"signal": False}
