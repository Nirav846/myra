import sqlite3
import pandas as pd
import os
import sys
from datetime import datetime, timedelta

# Fix path
sys.path.append(os.getcwd())
try:
    from myra_app.librarian import Librarian
except ImportError:
    sys.path.append(os.path.join(os.getcwd(), ".."))
    from librarian import Librarian

from tools.symbol_mapper import SymbolMapper

def detect_missing_candles(tech_db=None, calendar_csv=None, output_csv=None, lookback_days=1000):
    """
    True Gap Detection based on ACTUAL presence in database.
    Only flags dates where we have NO data for an active symbol, 
    starting from the EARLIEST date that symbol appears in our DB.
    """
    tech_db = tech_db if tech_db else os.path.join(os.getcwd(), "db", "technical.db")
    meta_db = os.path.join(os.getcwd(), "db", "meta.db")
    calendar_csv = calendar_csv if calendar_csv else os.path.join(os.getcwd(), "data", "trading_calendar_master.csv")
    output_csv = output_csv if output_csv else os.path.join(os.getcwd(), "data", "missing_data.csv")
    
    print("[MYRA] Initializing Empirical Gap Detection...")
    
    mapper = SymbolMapper()
    
    # 1. Get Active Symbols
    conn_meta = sqlite3.connect(meta_db)
    active_symbols = [r[0] for r in conn_meta.execute("SELECT symbol FROM symbols_master WHERE is_active = 1 AND in_active_universe = 1").fetchall()]
    conn_meta.close()
    
    if not active_symbols:
        print("[!] No active symbols found.")
        return

    # 2. Get Trading Days from Calendar
    df_cal = pd.read_csv(calendar_csv)
    # Filter for last 2 years roughly (730 days) as per user mandate
    # Or use provided lookback
    lookback_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    trading_days = sorted(df_cal[df_cal['date'] >= lookback_date]['date'].tolist())
    trading_days_set = set(trading_days)

    # 3. Get Empirical Bounds from Technical DB
    conn_tech = sqlite3.connect(tech_db)
    print("[*] Calculating empirical bounds for symbols...")
    bounds = pd.read_sql("SELECT symbol, MIN(date) as first, MAX(date) as last FROM technical_data GROUP BY symbol", conn_tech)
    
    # Get all records for gap set operations
    existing = pd.read_sql("SELECT symbol, date FROM technical_data WHERE date >= ?", conn_tech, params=(lookback_date,))
    conn_tech.close()
    
    bounds_map = {row['symbol']: row['first'] for _, row in bounds.iterrows()}
    existing_map = {}
    if not existing.empty:
        for sym, group in existing.groupby('symbol'):
            existing_map[sym] = set(group['date'])

    # 4. Gap Detection
    missing_records = []
    for sym in active_symbols:
        # Determine the effective start date for this symbol
        # If we have data, we start from our earliest record.
        # If we have NO data, we start from lookback_date.
        effective_start = bounds_map.get(sym, lookback_date)
        
        # Valid days for this symbol are from its first appearance to today
        valid_days = [d for d in trading_days if d >= effective_start]
        
        sym_dates = existing_map.get(sym, set())
        missing = set(valid_days) - sym_dates
        
        for m_date in missing:
            missing_records.append({'symbol': sym, 'missing_date': m_date})
            
    # 5. Final Report
    df_missing = pd.DataFrame(missing_records)
    if not df_missing.empty:
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        df_missing.to_csv(output_csv, index=False)
        print(f"[+] Found {len(df_missing)} EMPIRICAL missing candles. Saved to {output_csv}.")
    else:
        print("[+] SUCCESS: technical.db is FULLY POPULATED for all active symbols!")

if __name__ == "__main__":
    # Use 730 days (2 years) as default lookback for full population check
    detect_missing_candles(lookback_days=730)
