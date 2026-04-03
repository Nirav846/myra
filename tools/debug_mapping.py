import os
import sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
from myra_app.librarian import Librarian

def debug_mapping():
    lib = Librarian(read_only=True)
    sym = "ONGC"
    
    print(f"[*] Debugging Mapping for {sym}...")
    
    # 1. Check get_ohlcv output
    df = lib.get_ohlcv(sym)
    print(f"    - OHLCV Columns: {df.columns.tolist()}")
    print(f"    - Delivery Sum: {df['delivery_qty'].sum() if 'delivery_qty' in df.columns else 'MISSING'}")
    
    # 2. Check Parquet indicators
    ind = lib.loader.indicators.load_indicators("precomputed", sym)
    print(f"    - Parquet Columns: {ind.columns.tolist()}")
    if not ind.empty:
        print(f"    - Latest Money Flow: {ind['money_flow_cr'].iloc[-1]}")
        print(f"    - Latest SMA150: {ind['sma150'].iloc[-1]}")
    else:
        print("    - [!] Parquet file empty or missing.")
    
    lib.close()

if __name__ == "__main__":
    debug_mapping()
