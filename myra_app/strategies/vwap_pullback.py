import pandas as pd
import pandas_ta as ta
import numpy as np

def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Institutional VWAP Pullback
    Finds stocks trading at or below their anchored VWAP bands (Deep Value).
    """
    if len(df) < 30: return {"signal": False}
    
    try:
        # 1. Calculate VWAP (Approximate for Daily data)
        # Typical Price = (H + L + C) / 3
        tp = (df["High"] + df["Low"] + df["Close"]) / 3
        df["VWAP"] = (tp * df["Volume"]).rolling(window=20).sum() / df["Volume"].rolling(window=20).sum()
        
        # 2. Calculate Standard Deviation Bands
        # We use the standard deviation of price relative to VWAP
        df["VWAP_Std"] = df["Close"].rolling(window=20).std()
        df["Lower_Band_1"] = df["VWAP"] - (1.0 * df["VWAP_Std"])
        df["Lower_Band_2"] = df["VWAP"] - (2.0 * df["VWAP_Std"])
        
        latest = df.iloc[-1]
        
        # 3. Fundamental Filter
        roe = funda.get("ROE")
        is_quality = (roe > 15) if (roe and roe != "NULL" and not pd.isna(roe)) else True
        
        # LOGIC:
        # - Price is currently in the "Value Zone" (Between Lower Band 1 and 2)
        # - Technical support: Price > 200 SMA (Bullish Trend)
        ma200 = df["Close"].rolling(200).mean().iloc[-1] if len(df) >= 200 else 0
        
        is_in_value_zone = (latest["Close"] <= latest["Lower_Band_1"])
        is_trending = latest["Close"] > ma200
        
        if is_in_value_zone and is_trending and is_quality:
            dist_to_vwap = round(((latest["Close"] / latest["VWAP"]) - 1) * 100, 1)
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(latest["Close"], 2),
                    "VWAP_Gap": f"{dist_to_vwap}%",
                    "Zone": "Deep Value" if latest["Close"] <= latest["Lower_Band_2"] else "Institutional Buy",
                    "ROE": f"{roe}%" if roe else "N/A",
                    "MCap": funda.get("MCap", "N/A")
                }
            }
            
    except Exception:
        pass
        
    return {"signal": False}
