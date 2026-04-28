"""
MYRA SMC Calculator
Computes Smart Money Concepts indicators from OHLCV data:
- Fair Value Gaps (FVG)
- Swing Highs & Lows
- HTF/MTF Trend Alignment
- Liquidity Distance
- FVG Freshness
- Delivery MA
All calculations are vectorised using pandas/numpy for speed.
"""
import pandas as pd
import numpy as np

def calculate_smc_indicators(df: pd.DataFrame, swing_length: int = 5) -> pd.DataFrame:
    """
    Compute all SMC indicators from OHLCV DataFrame.
    Expects columns: Open, High, Low, Close, Volume (CamelCase).
    Returns DataFrame with added SMC columns.
    """
    df = df.copy()
    
    # --- 1. Fair Value Gap (FVG) Detection ---
    # Bullish FVG: 3-candle pattern where candle[0].low > candle[2].high
    df['bullish_fvg'] = 0
    bullish_mask = df['Low'].shift(0) > df['High'].shift(2)
    df.loc[bullish_mask, 'bullish_fvg'] = 1
    
    # Bearish FVG: candle[0].high < candle[2].low
    df['bearish_fvg'] = 0
    bearish_mask = df['High'].shift(0) < df['Low'].shift(2)
    df.loc[bearish_mask, 'bearish_fvg'] = 1
    
    # FVG boundary (top/bottom of the gap)
    df['fvg_top'] = np.where(df['bullish_fvg'] == 1, df['Low'].shift(0), 
                    np.where(df['bearish_fvg'] == 1, df['High'].shift(2), np.nan))
    df['fvg_bottom'] = np.where(df['bullish_fvg'] == 1, df['High'].shift(2),
                       np.where(df['bearish_fvg'] == 1, df['Low'].shift(0), np.nan))
    
    # FVG boundary (single value — the nearest edge)
    df['fvg_boundary'] = np.where(df['Close'] > df['fvg_top'], df['fvg_top'],
                         np.where(df['Close'] < df['fvg_bottom'], df['fvg_bottom'],
                         (df['fvg_top'] + df['fvg_bottom']) / 2))
    
    # FVG freshness (bars since last FVG)
    df['fvg_freshness'] = 0
    last_fvg = -1000
    for i in range(len(df)):
        if df['bullish_fvg'].iloc[i] or df['bearish_fvg'].iloc[i]:
            last_fvg = i
        df.loc[df.index[i], 'fvg_freshness'] = i - last_fvg
    
    # --- 2. Swing Highs & Lows ---
    n = swing_length
    df['swing_high'] = 0.0
    df['swing_low'] = 0.0
    
    for i in range(n, len(df) - n):
        # Swing high: highest high in window
        if df['High'].iloc[i] == df['High'].iloc[i-n:i+n+1].max():
            df.loc[df.index[i], 'swing_high'] = df['High'].iloc[i]
        # Swing low: lowest low in window
        if df['Low'].iloc[i] == df['Low'].iloc[i-n:i+n+1].min():
            df.loc[df.index[i], 'swing_low'] = df['Low'].iloc[i]
    
    # Forward-fill swing points
    df['swing_high'] = df['swing_high'].replace(0, np.nan).ffill()
    df['swing_low'] = df['swing_low'].replace(0, np.nan).ffill()
    
    # --- 3. Liquidity Distance ---
    df['liquidity_distance'] = np.minimum(
        (df['swing_high'] - df['Close']) / df['Close'],
        (df['Close'] - df['swing_low']) / df['Close']
    ).abs()
    
    # --- 4. HTF/MTF Trend Alignment ---
    # HTF (Higher Time Frame): SMA50 vs SMA200
    df['sma50'] = df['Close'].rolling(50).mean()
    df['sma200'] = df['Close'].rolling(200).mean()
    df['htf_bullish'] = (df['sma50'] > df['sma200']).astype(int)
    df['htf_bearish'] = (df['sma50'] < df['sma200']).astype(int)
    
    # MTF (Medium Time Frame): SMA20 vs SMA50
    df['sma20'] = df['Close'].rolling(20).mean()
    df['mtf_bullish'] = (df['sma20'] > df['sma50']).astype(int)
    df['mtf_bearish'] = (df['sma20'] < df['sma50']).astype(int)
    
    # Trend alignment score: 2=both bullish, -2=both bearish, 0=mixed
    df['trend_alignment'] = df['htf_bullish'] + df['mtf_bullish'] - df['htf_bearish'] - df['mtf_bearish']
    
    # --- 5. Delivery Moving Average ---
    if 'delivery_qty' in df.columns or 'delivery' in df.columns:
        deliv_col = 'delivery_qty' if 'delivery_qty' in df.columns else 'delivery'
        df['delivery_ma_60'] = df[deliv_col].rolling(60).mean()
    else:
        df['delivery_ma_60'] = 0
    
    # --- 6. Has active bullish FVG (within last 10 bars) ---
    df['has_bullish_fvg'] = ((df['bullish_fvg'] == 1) & (df['fvg_freshness'] <= 10)).astype(int)
    
    return df
