import pandas as pd

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Breakout Strategy: 52-Week High + Volume
    """
    if len(df) < 250: return {"signal": False}
    
    latest = df.iloc[-1]
    
    # 1. 52-Week High Logic
    # We look at the max of the last 250 days (excluding today)
    high_52w = df["High"].iloc[-250:-1].max()
    is_52w_high = latest["Close"] >= high_52w
    
    # 2. 10-Day High Logic (Minor Breakout)
    high_10d = df["High"].iloc[-11:-1].max()
    is_10d_high = latest["Close"] >= high_10d
    
    # 3. Volume Confirmation
    avg_vol = df["Volume"].rolling(window=20).mean().iloc[-1]
    is_vol_breakout = latest["Volume"] > (avg_vol * 2.0)
    
    if (is_52w_high or is_10d_high) and is_vol_breakout:
        return {
            "signal": True,
            "metrics": {
                "LTP": round(latest["Close"], 2),
                "Type": "52W-High" if is_52w_high else "10D-High",
                "Vol_Spike": f"{round(latest['Volume']/avg_vol, 1)}x",
                "ROE": funda.get("ROE", "N/A"),
                "MCap": funda.get("MCap", "N/A")
            }
        }
    return {"signal": False}
