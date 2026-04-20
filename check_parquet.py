import pandas as pd
import os

symbol = "SBIN"
file_path = f"data/indicators/{symbol}.parquet"

if os.path.exists(file_path):
    df = pd.read_parquet(file_path)
    count = len(df)
    print(f"[{symbol}] Parquet Depth: {count} rows")
    if count < 60:
        print(f"[!] WARNING: Insufficient depth for Fusion Engine (Need 60, found {count})")
    
    # Check for required Fusion columns
    required = ['fvg_top', 'fvg_bottom', 'delivery_ma_60']
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[!] MISSING COLUMNS: {missing}")
else:
    print(f"[!] CRITICAL: No Parquet file found for {symbol} at {file_path}")