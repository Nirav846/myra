#!/usr/bin/env python
import pandas as pd

class SupplyAbsorptionScanner:
    """
    Supply Absorption (Quiet Buying) Scanner
    Detects low-volume pullbacks to 150/200 DMA, signaling supply exhaustion.
    """
    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 200: return {"signal": False}
        
        try:
            c = df['Close']
            v = df['Volume']
            sma200 = c.rolling(200).mean().iloc[-1]
            sma150 = c.rolling(150).mean().iloc[-1]
            
            # 1. Price is pulling back to SMA (within 3% range)
            near_ma = abs(c.iloc[-1]/sma200 - 1) < 0.03 or abs(c.iloc[-1]/sma150 - 1) < 0.03
            if not near_ma: return {"signal": False}
            
            # 2. Volume Trend: Last 5 days avg volume < Last 20 days avg volume
            avg_v_5 = v.iloc[-5:].mean()
            avg_v_20 = v.iloc[-20:].mean()
            
            if avg_v_5 > (avg_v_20 * 0.8): return {"signal": False} # Must be quiet
            
            return {
                "signal": True,
                "metrics": {
                    "Strategy": "Supply_Absorp",
                    "Vol_Ratio": round(avg_v_5/avg_v_20, 2),
                    "MA_Floor": "Respected"
                }
            }
        except: return {"signal": False}
