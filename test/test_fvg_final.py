import pandas as pd
import numpy as np
import os
import sys

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.engine import SMCManager

def test_fvg_final():
    lib = Librarian(read_only=True)
    # We test ECLERX because it's the top hit in your scan
    symbol = "ECLERX"
    print(f"\n--- DEEP FVG AUDIT for {symbol} ---")
    
    # Force full price history
    df = lib.conn.execute("SELECT * FROM prices WHERE symbol='ECLERX' ORDER BY date ASC").df()
    print(f"[*] History Depth: {len(df)} bars.")
    
    res = SMCManager.get_fvg_buy_zone(df)
    if res:
        print(f"[✔] FVG FOUND: {res}")
    else:
        print("[!] No Unmitigated Bullish FVG found below price in 3 years.")
        
        # Manual scan to see why
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        ltp = closes[-1]
        
        found_any = 0
        for i in range(len(df)-1, max(2, len(df)-756), -1):
            if lows[i] > highs[i-2]:
                found_any += 1
                zone = (highs[i-2], lows[i])
                if ltp < zone[0]:
                    print(f"  [*] FVG @ idx {i} ({zone[0]:.2f}-{zone[1]:.2f}) is ABOVE price (LTP {ltp:.2f}). Ignored.")
                else:
                    # Check mitigation
                    for j in range(i+1, len(df)):
                        if closes[j] < (zone[0] * 0.995):
                            # print(f"  [*] FVG @ idx {i} was MITIGATED at idx {j}.")
                            break
                    else:
                        print(f"  [✔] VIRGIN FVG FOUND @ idx {i} ({zone[0]:.2f}-{zone[1]:.2f})!")

    lib.close()

if __name__ == "__main__":
    test_fvg_final()
