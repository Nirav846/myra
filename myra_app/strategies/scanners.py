import pandas as pd
import numpy as np
import pandas_ta as ta

def run_primitive(df: pd.DataFrame, scanner_id: str) -> bool:
    """
    Modular Technical Primitives (Low-Level Scanners)
    Returns simple True/False for fast filtering.
    """
    if len(df) < 50: return False
    
    try:
        c = df["Close"]
        
        # 101: RSI Oversold (< 30)
        if scanner_id == "101":
            rsi = ta.rsi(c, length=14).iloc[-1]
            return rsi < 30
            
        # 102: RSI Neutral-Bullish (40-60)
        elif scanner_id == "102":
            rsi = ta.rsi(c, length=14).iloc[-1]
            return 40 <= rsi <= 60
            
        # 103: MACD Bullish Crossover
        elif scanner_id == "103":
            macd = ta.macd(c)
            return macd["MACDh_12_26_9"].iloc[-1] > 0 and macd["MACDh_12_26_9"].iloc[-2] <= 0
            
        # 104: Golden Cross (50 SMA > 200 SMA)
        elif scanner_id == "104":
            ma50 = ta.sma(c, length=50).iloc[-1]
            ma200 = ta.sma(c, length=200).iloc[-1]
            return ma50 > ma200
            
        # 105: Volume Surge (2x Avg)
        elif scanner_id == "105":
            avg_vol = df["Volume"].rolling(window=20).mean().iloc[-1]
            return df["Volume"].iloc[-1] > (avg_vol * 2.0)
            
        # 106: Price Above 20 SMA
        elif scanner_id == "106":
            ma20 = ta.sma(c, length=20).iloc[-1]
            return c.iloc[-1] > ma20
            
        # 107: Bollinger Band Squeeze (Width < 5%)
        elif scanner_id == "107":
            bb = ta.bbands(c, length=20)
            width = (bb["BBU_20_2.0"].iloc[-1] - bb["BBL_20_2.0"].iloc[-1]) / bb["BBM_20_2.0"].iloc[-1]
            return width < 0.05
            
        # 108: Higher Highs & Higher Lows (3-day)
        elif scanner_id == "108":
            return (df["High"].iloc[-1] > df["High"].iloc[-2] > df["High"].iloc[-3]) and \
                   (df["Low"].iloc[-1] > df["Low"].iloc[-2] > df["Low"].iloc[-3])

    except Exception: pass
    return False
