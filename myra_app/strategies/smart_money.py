import pandas as pd
import numpy as np
import pandas_ta as ta

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Smart Money Accumulation (Divergence v2.2)
    Finds Institutional Accumulation via Delivery growth and ATR Squeeze.
    """
    if len(df) < 120: return {"signal": False}
    
    try:
        c = df["Close"]
        if "delivery_qty" not in df.columns: return {"signal": False}
        
        # Ensure delivery data is clean for rolling calculations
        dq = df["delivery_qty"].fillna(0)
        
        # New Metrics from Engine/Librarian
        rdv = funda.get("RDV", 0)
        squeeze = funda.get("Squeeze", False)
        
        # Institutional Absorption (RDV > 1.2 or 60-Day Growth)
        deliv_ma50 = dq.rolling(window=50).mean()
        d_current = deliv_ma50.iloc[-1]
        d_prev = deliv_ma50.iloc[-60] if len(deliv_ma50) > 60 else None
        
        d_change = (d_current / d_prev) - 1 if d_current and d_prev and d_prev != 0 else 0
        if np.isnan(d_change): d_change = 0
        
        # Trend context
        s200 = funda.get("sma200", 0)
        is_in_uptrend = c.iloc[-1] > s200 if s200 > 0 else True
        stage = funda.get("Stage", "Stage 4")
        
        # 1. SMART SQUEEZE (The New primary trigger)
        # Price is compressed (ATR Range) and Delivery is high streak
        if squeeze:
            if "Stage 1" in stage or "Stage 2" in stage:
                return _generate_result(df, "SMART SQUEEZE", d_change, funda)
            elif "Stage 4" in stage:
                res = _generate_result(df, "DANGEROUS SQUEEZE", d_change, funda)
                res["metrics"]["block_stars"] = True
                return res

        # 2. RDV SHOCK
        # Heavy delivery relative to average
        if rdv > 2.0 and is_in_uptrend:
            return _generate_result(df, "DELIVERY SHOCK", d_change, funda)

        # 3. BOTTOM ABSORPTION (Aggressive buying at bottoms)
        l1y = funda.get("low_1y", 0)
        is_at_bottom = (c.iloc[-1] <= l1y * 1.10) if l1y > 0 else False
        if is_at_bottom and (rdv > 1.5 or d_change > 0.40):
            return _generate_result(df, "BOTTOM ABSORPTION", d_change, funda)

        # 4. HIGH CONVICTION (Composite SM Score)
        sm_score = funda.get("smart_money_score", 0)
        if sm_score > 0.8 and is_in_uptrend:
            return _generate_result(df, "HIGH CONVICTION", d_change, funda)
            
    except Exception: pass
    return {"signal": False}

def _generate_result(df, ctx, d_change, funda):
    c = df["Close"]
    cons_high = df["High"].tail(20).max()
    entry = round(cons_high * 1.005, 2)
    
    # Use pre-calculated ATR14 from DuckDB/Turbo-SQL for performance
    atr = funda.get("ATR14", c.iloc[-1] * 0.05)
    sl = round(c.iloc[-1] - (2.0 * (atr if atr > 0 else (c.iloc[-1]*0.05))), 2)
    
    risk = entry - sl
    target = round(entry + (4 * risk), 2)
    
    return {
        "signal": True,
        "tactics": {"entry": entry, "sl": sl, "target": target},
        "metrics": {
            "LTP": round(c.iloc[-1], 2),
            "Deliv_Grow": f"{round(d_change * 100, 1)}%",
            "Context": ctx,
            "SM_Score": round(funda.get("smart_money_score", 0), 2)
        }
    }
