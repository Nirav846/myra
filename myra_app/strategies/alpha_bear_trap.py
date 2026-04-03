#!/usr/bin/env python
import pandas as pd

class Strategy:
    """
    Weekly Bear Trap Scanner
    Detects failed breakdowns with institutional absorption.
    """
    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 40: return {"signal": False}
        
        try:
            c = df['Close']
            l = df['Low']
            
            # 1. Identify previous 20-day low
            prev_low_20 = l.iloc[-21:-1].min()
            
            # 2. Check for trap: Today's Low < Prev Low AND Today's Close > Prev Low
            is_trap = l.iloc[-1] < prev_low_20 and c.iloc[-1] > prev_low_20
            
            if not is_trap: return {"signal": False}
            
            # 3. Confirmation: High Delivery (> 45%)
            d_pct = funda.get("delivery_percent", 0)
            if d_pct < 45: return {"signal": False}
            
            return {
                "signal": True,
                "metrics": {
                    "Strategy": "Bear_Trap",
                    "Trap_Depth": f"{round((prev_low_20 - l.iloc[-1])/prev_low_20 * 100, 1)}%",
                    "Absorption": f"{round(d_pct)}%"
                }
            }
        except: return {"signal": False}
