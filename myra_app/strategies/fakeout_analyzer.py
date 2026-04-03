import pandas as pd
import numpy as np

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Morning Fakeout Radar (v1.0)
    Detects 'Bull Traps' (Gap Up + Sell Off) and 'Bear Traps' (Gap Down + Recovery).
    """
    if len(df) < 5: return {"signal": False}
    
    try:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        ltp = latest["Close"]
        opening = latest["Open"]
        prev_close = prev["Close"]
        
        # 1. Calculate Gap and Intraday movement
        gap_per = round(((opening - prev_close) / prev_close) * 100, 2)
        intraday_per = round(((ltp - opening) / opening) * 100, 2)
        
        status = None
        # BULL TRAP: Gap Up > 1% but Price closes > 1% below Open
        if gap_per >= 1.0 and intraday_per <= -1.0:
            status = "[bold red]BULL TRAP[/bold red]"
            
        # BEAR TRAP: Gap Down < -1% but Price closes > 1% above Open
        elif gap_per <= -1.0 and intraday_per >= 1.0:
            status = "[bold green]BEAR TRAP[/bold green]"
            
        if status:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(ltp, 2),
                    "Type": status,
                    "Gap%": f"{gap_per}%",
                    "Intraday%": f"{intraday_per}%",
                    "Vol_Vibe": "High" if latest["Volume"] > (df["Volume"].tail(20).mean() * 1.5) else "Normal"
                }
            }
            
    except Exception: pass
    return {"signal": False}
