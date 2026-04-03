#!/usr/bin/env python
import pandas as pd

class EarningsDriftScanner:
    """
    Post-Earnings Alpha Drift Scanner
    Detects structural gap-ups that hold, signaling institutional re-rating.
    """
    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 30: return {"signal": False}
        
        try:
            c = df['Close']
            o = df['Open']
            v = df['Volume']
            
            # 1. Detect significant Gap Up (> 3%) in last 10 days
            gaps = (o / c.shift(1)) - 1
            recent_gaps = gaps.iloc[-10:]
            
            # Find the largest gap day
            gap_day_idx = recent_gaps.idxmax()
            max_gap = recent_gaps.max()
            
            if max_gap < 0.03: return {"signal": False}
            
            # 2. Check if Gap holds (Current price > Gap Low)
            gap_low = df.loc[gap_day_idx, 'Low']
            if c.iloc[-1] < gap_low: return {"signal": False}
            
            # 3. Confirmation: Volume on gap day was > 2x Avg
            avg_v = v.rolling(20).mean()
            gap_vol = v.loc[gap_day_idx]
            if gap_vol < (avg_v.loc[gap_day_idx] * 2.0): return {"signal": False}
            
            return {
                "signal": True,
                "metrics": {
                    "Strategy": "Earnings_Drift",
                    "Gap_Pct": f"{round(max_gap * 100, 1)}%",
                    "Holding": "Strong"
                }
            }
        except: return {"signal": False}
