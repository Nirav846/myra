#!/usr/bin/env python
import pandas as pd

class Stage2Scanner:
    """
    Stage 2 Trend Continuation Scanner
    Targets symbols with long-term structural uptrends (Monthly HH/HL).
    """
    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 252: return {"signal": False}
        
        try:
            # 1. Base Filter: Stage 2
            if funda.get("Stage") != "Stage 2": return {"signal": False}
            
            # 2. Structural HH/HL (using 20-day chunks as 'Monthly' proxy)
            c = df['Close']
            high_curr = c.iloc[-20:].max()
            high_prev = c.iloc[-40:-20].max()
            low_curr = df['Low'].iloc[-20:].min()
            low_prev = df['Low'].iloc[-40:-20].min()
            
            is_hh_hl = high_curr > high_prev and low_curr > low_prev
            
            if not is_hh_hl: return {"signal": False}
            
            # 3. Volume confirmation: Avg Volume last 20 days > Avg Volume 252 days
            v = df['Volume']
            if v.iloc[-20:].mean() < v.iloc[-252:].mean(): return {"signal": False}
            
            return {
                "signal": True,
                "metrics": {
                    "Strategy": "Stage2_Cont",
                    "HH_HL": "Yes",
                    "Relative_Vol": round(v.iloc[-20:].mean() / v.iloc[-252:].mean(), 2)
                }
            }
        except: return {"signal": False}
