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
    df = df.copy().sort_values(['symbol','date']).reset_index(drop=True)
    
    # --- FVG Detection (vectorized) ---
    df['bullish_fvg'] = (df['Low'] > df.groupby('symbol')['High'].shift(2)).astype(int)
    df['bearish_fvg'] = (df['High'] < df.groupby('symbol')['Low'].shift(2)).astype(int)
    
    # --- FVG boundaries ---
    grp_high2 = df.groupby('symbol')['High'].shift(2)
    grp_low0 = df['Low']
    grp_low2 = df.groupby('symbol')['Low'].shift(2)
    grp_high0 = df['High']
    
    df['fvg_top'] = np.where(df['bullish_fvg']==1, grp_low0,
                    np.where(df['bearish_fvg']==1, grp_high2, np.nan))
    df['fvg_bottom'] = np.where(df['bullish_fvg']==1, grp_high2,
                       np.where(df['bearish_fvg']==1, grp_low0, np.nan))
    
    df['fvg_boundary'] = np.select(
        [df['Close'] > df['fvg_top'], df['Close'] < df['fvg_bottom']],
        [df['fvg_top'], df['fvg_bottom']],
        default=(df['fvg_top'] + df['fvg_bottom']) / 2
    )
    
    # --- FVG freshness (vectorized per symbol) ---
    df['fvg_event'] = ((df['bullish_fvg']==1) | (df['bearish_fvg']==1))
    df['event_idx'] = df.groupby('symbol').cumcount()
    df['last_event_idx'] = df.groupby('symbol')['event_idx'].where(df['fvg_event']).ffill().fillna(-1000)
    df['fvg_freshness'] = df['event_idx'] - df['last_event_idx']
    df.drop(['fvg_event','event_idx','last_event_idx'], axis=1, inplace=True)
    
    # --- Swing Highs/Lows (vectorized rolling max/min) ---
    n = swing_length
    roll_high = df.groupby('symbol')['High'].rolling(2*n+1, center=True, min_periods=1)
    roll_low = df.groupby('symbol')['Low'].rolling(2*n+1, center=True, min_periods=1)
    
    df['swing_high'] = (df['High'] == roll_high.max().reset_index(level=0, drop=True)).astype(float) * df['High']
    df['swing_low'] = (df['Low'] == roll_low.min().reset_index(level=0, drop=True)).astype(float) * df['Low']
    df['swing_high'] = df['swing_high'].replace(0, np.nan).ffill()
    df['swing_low'] = df['swing_low'].replace(0, np.nan).ffill()
    
    # --- Liquidity Distance ---
    df['liquidity_distance'] = np.minimum(
        (df['swing_high'] - df['Close']) / df['Close'],
        (df['Close'] - df['swing_low']) / df['Close']
    ).abs()
    
    # --- Trend Alignment (vectorized SMAs) ---
    sma50 = df.groupby('symbol')['Close'].transform(lambda x: x.rolling(50).mean())
    sma200 = df.groupby('symbol')['Close'].transform(lambda x: x.rolling(200).mean())
    sma20 = df.groupby('symbol')['Close'].transform(lambda x: x.rolling(20).mean())
    
    df['htf_bullish'] = (sma50 > sma200).astype(int)
    df['htf_bearish'] = (sma50 < sma200).astype(int)
    df['mtf_bullish'] = (sma20 > sma50).astype(int)
    df['mtf_bearish'] = (sma20 < sma50).astype(int)
    df['trend_alignment'] = df['htf_bullish'] + df['mtf_bullish'] - df['htf_bearish'] - df['mtf_bearish']
    
    # --- Delivery MA ---
    deliv_col = 'delivery_qty' if 'delivery_qty' in df.columns else 'delivery'
    df['delivery_ma_60'] = df.groupby('symbol')[deliv_col].transform(lambda x: x.rolling(60).mean())
    
    # --- Active bullish FVG ---
    df['has_bullish_fvg'] = ((df['bullish_fvg']==1) & (df['fvg_freshness'] <= 10)).astype(int)
    
    return df
