
import numpy as np
import pandas as pd
import os
import duckdb

def calculate_fvg(df):
    """
    Fair Value Gap (FVG)
    Bullish: Low[i] > High[i-2]
    Bearish: High[i] < Low[i-2]
    """
    df = df.copy()
    df['fvg'] = 0
    df['fvg_top'] = np.nan
    df['fvg_bottom'] = np.nan
    
    for i in range(2, len(df)):
        # Bullish FVG
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            df.loc[df.index[i], 'fvg'] = 1
            df.loc[df.index[i], 'fvg_top'] = df['low'].iloc[i]
            df.loc[df.index[i], 'fvg_bottom'] = df['high'].iloc[i-2]
        # Bearish FVG
        elif df['high'].iloc[i] < df['low'].iloc[i-2]:
            df.loc[df.index[i], 'fvg'] = -1
            df.loc[df.index[i], 'fvg_top'] = df['low'].iloc[i-2]
            df.loc[df.index[i], 'fvg_bottom'] = df['high'].iloc[i]
            
    return df

def calculate_structure(df, window=5):
    """
    Market Structure: BOS and CHoCH
    BOS: Break of Structure (Continuation)
    CHoCH: Change of Character (Reversal)
    """
    df = df.copy()
    df['swing_high'] = df['high'].rolling(window=window*2+1, center=True).max()
    df['swing_low'] = df['low'].rolling(window=window*2+1, center=True).min()
    
    df['bos'] = 0
    df['choch'] = 0
    
    last_high = np.nan
    last_low = np.nan
    trend = 0 # 1 for up, -1 for down
    
    for i in range(len(df)):
        if not np.isnan(df['swing_high'].iloc[i]) and df['swing_high'].iloc[i] == df['high'].iloc[i]:
            last_high = df['high'].iloc[i]
        if not np.isnan(df['swing_low'].iloc[i]) and df['swing_low'].iloc[i] == df['low'].iloc[i]:
            last_low = df['low'].iloc[i]
            
        if trend == 1: # Uptrend
            if df['close'].iloc[i] > last_high:
                df.loc[df.index[i], 'bos'] = 1
            elif df['close'].iloc[i] < last_low:
                df.loc[df.index[i], 'choch'] = -1
                trend = -1
        elif trend == -1: # Downtrend
            if df['close'].iloc[i] < last_low:
                df.loc[df.index[i], 'bos'] = -1
            elif df['close'].iloc[i] > last_high:
                df.loc[df.index[i], 'choch'] = 1
                trend = 1
        else: # Initial trend detection
            if not np.isnan(last_high) and df['close'].iloc[i] > last_high: trend = 1
            if not np.isnan(last_low) and df['close'].iloc[i] < last_low: trend = -1
            
    return df

def test_smc():
    print("[*] SMC Sandbox: Testing new indicators...")
    
    db_path = "results/Data/myra_market_data.db"
    if not os.path.exists(db_path):
        print("[!] DB not found, using dummy data.")
        data = {
            'open': [100, 110, 105, 120, 115, 130, 125, 140, 135, 150],
            'high': [105, 115, 110, 125, 120, 135, 130, 145, 140, 155],
            'low': [95, 105, 100, 115, 110, 125, 120, 135, 130, 145],
            'close': [102, 112, 107, 122, 117, 132, 127, 142, 137, 152],
            'volume': [1000]*10
        }
        df = pd.DataFrame(data)
    else:
        conn = duckdb.connect(db_path, read_only=True)
        df = conn.execute("SELECT open, high, low, close, volume FROM prices LIMIT 100").df()
        conn.close()
        print(f"[✔] Loaded {len(df)} rows from DB.")

    df = calculate_fvg(df)
    df = calculate_structure(df)
    
    fvg_count = len(df[df['fvg'] != 0])
    bos_count = len(df[df['bos'] != 0])
    choch_count = len(df[df['choch'] != 0])
    
    print(f"[✔] Found {fvg_count} Fair Value Gaps (FVG)")
    print(f"[✔] Found {bos_count} Break of Structure (BOS)")
    print(f"[✔] Found {choch_count} Change of Character (CHoCH)")
    
    if fvg_count > 0:
        print("\n[*] Sample FVG Detection:")
        print(df[df['fvg'] != 0][['fvg', 'fvg_top', 'fvg_bottom']].head())

if __name__ == "__main__":
    test_smc()
