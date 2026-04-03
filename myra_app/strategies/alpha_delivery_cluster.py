#!/usr/bin/env python
import pandas as pd

class DeliveryClusterScanner:
    """
    Delivery Cluster Accumulation Scanner
    Detects symbols with sustained high delivery (>55%) over 2 weeks in a tight range.
    """
    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 20: return {"signal": False}
        
        try:
            # 1. Delivery Check: > 55% for at least 6 out of last 10 days
            d_pct = df['delivery_pct'].iloc[-10:] if 'delivery_pct' in df.columns else pd.Series([0]*10)
            high_del_days = (d_pct > 55).sum()
            
            if high_del_days < 6: return {"signal": False}
            
            # 2. Price Range Check: Last 10 days within 5% range (Quiet Buying)
            c = df['Close'].iloc[-10:]
            p_range = (c.max() / c.min()) - 1
            if p_range > 0.05: return {"signal": False}
            
            return {
                "signal": True,
                "metrics": {
                    "Strategy": "Deliv_Cluster",
                    "High_Days": f"{high_del_days}/10",
                    "Tightness": f"{round(p_range*100, 1)}%"
                }
            }
        except: return {"signal": False}
