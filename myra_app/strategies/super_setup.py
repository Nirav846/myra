import pandas as pd
import pandas_ta as ta

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Super-Scan (Growth + Momentum)
    Tailored Tactics: Entry above high, SL below 50 SMA, 3R Target.
    """
    if len(df) < 50: return {"signal": False}
    
    # 1. Technical Indicators
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    ma50 = ta.sma(c, length=50)
    rsi = ta.rsi(c, length=14)
    atr = ta.atr(h, l, c, length=20)
    
    latest_c = c.iloc[-1]
    latest_h = h.iloc[-1]
    latest_50 = ma50.iloc[-1] if not ma50.isna().iloc[-1] else latest_c # Fallback
    latest_rsi = rsi.iloc[-1]
    latest_atr = atr.iloc[-1]
    
    # 2. RS Math
    ret_1y = (c.iloc[-1] / c.iloc[-250]) if len(c) >= 250 else (c.iloc[-1] / c.iloc[0])
    ret_3m = (c.iloc[-1] / c.iloc[-63])  if len(c) >= 63  else (c.iloc[-1] / c.iloc[0])
    rs_score = (ret_1y * 0.7) + (ret_3m * 0.3)
    
    avg_vol = df["Volume"].rolling(window=20).mean().iloc[-1]
    latest_vol = df["Volume"].iloc[-1]
    
    # 3. Trend Logic: Use precomputed for resilience
    s200 = funda.get("sma200", 0)
    s150 = funda.get("sma150", 0)
    
    if s200 and s200 > 0:
        latest_200 = s200
    elif s150 and s150 > 0:
        latest_200 = s150
    else:
        latest_200 = latest_50 # Extreme fallback
        
    is_trending = (latest_c > latest_50 > latest_200)
    
    # Check for Insider Conviction (From Pipe/Engine)
    insider_buy = funda.get("AV_Latest", 0) + funda.get("AV_Total", 0)
    has_insider = insider_buy > 0
    
    # Relax momentum/volume rules if insiders are actively buying
    if has_insider:
        is_strong = (rs_score > 0.9) and (45 < latest_rsi < 85)
        is_accumulating = latest_vol > (avg_vol * 1.0) # Normal volume is fine if insiders are buying
    else:
        is_strong = (rs_score > 1.1) and (50 < latest_rsi < 75)
        is_accumulating = latest_vol > (avg_vol * 1.5)
    
    roe = funda.get("ROE")
    is_quality = (roe > 15) if (roe and roe != "NULL" and not pd.isna(roe)) else True

    
    if is_trending and is_strong and is_accumulating and is_quality:
        # TACTICAL PLANNING
        entry = round(latest_h * 1.002, 2) # Buy above today's high
        sl = round(min(latest_50, latest_c - (1.5 * latest_atr)), 2) # SL at 50 SMA or 1.5 ATR
        risk = entry - sl
        target = round(entry + (3 * risk), 2)
        
        return {
            "signal": True,
            "tactics": {"entry": entry, "sl": sl, "target": target},
            "metrics": {
                "LTP": round(latest_c, 2),
                "RS_Raw": round(rs_score, 3),
                "RSI": round(latest_rsi, 1),
                "Vol_Spike": f"{round(latest_vol/avg_vol, 1)}x",
                "ROE": f"{roe}%" if roe else "N/A",
                "MCap": funda.get("MCap", "N/A")
            }
        }
        
    return {"signal": False}
