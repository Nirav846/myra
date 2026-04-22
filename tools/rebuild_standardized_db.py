import re
import pandas as pd
from datetime import datetime

def extract_date_from_filename(filename):
    # Pattern 1: nse_full_20042026.csv (DDMMYYYY)
    match1 = re.search(r'(\d{2})(\d{2})(\d{4})', filename)
    if match1:
        return f"{match1.group(3)}-{match1.group(2)}-{match1.group(1)}"
    
    # Pattern 2: nse_full_2021-11-10.csv (YYYY-MM-DD)
    match2 = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if match2:
        return match2.group(1)
    return None

def standardize_bhavcopy(df):
    # 1. Map columns using the Universal Mapper
    mapping = {
        'delivery_qty': 'delivery', 'delivery_percent': 'delivery_pct',
        'delivery_ratio': 'delivery_pct', 'no_of_trades': 'trades',
        'TIMESTAMP': 'date', 'DATE1': 'date'
    }
    df = df.rename(columns=mapping)
    
    # 2. Fix Delivery Scaling (detect 0.45 vs 45.0)
    if 'delivery_pct' in df.columns:
        if df['delivery_pct'].max() <= 1.0:
            df['delivery_pct'] = df['delivery_pct'] * 100
            
    # 3. Ensure TitleCase for core OHLCV for Scanner compatibility
    df = df.rename(columns={'OPEN': 'Open', 'HIGH': 'High', 'LOW': 'Low', 'CLOSE': 'Close'})
    return df