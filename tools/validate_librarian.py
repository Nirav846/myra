import os
import sys
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
import pandas as pd

def validate_modular_librarian():
    print("[MYRA] Validating Modular Librarian v3.0...")
    lib = Librarian(read_only=True)
    
    # 1. Test Meta DB
    symbols = lib.get_all_symbols()
    print(f"[*] Meta DB: Found {len(symbols)} symbols.")
    
    active = lib.get_active_universe()
    print(f"[*] Meta DB: Active Universe size: {len(active)}")
    
    # 2. Test Technical DB
    if active:
        test_sym = active[0]
        ohlcv = lib.get_ohlcv(test_sym)
        if ohlcv is not None and not ohlcv.empty:
            print(f"[*] Tech DB: Successfully retrieved {len(ohlcv)} rows for {test_sym}.")
        else:
            print(f"[!] Tech DB: Failed to retrieve data for {test_sym}.")
            
    # 3. Test Institutional DB
    max_insider = lib.get_max_insider_date()
    print(f"[*] Institutional DB: Max insider date: {max_insider}")
    
    # 4. Test Valuation DB
    if active:
        funda = lib.get_fundamentals(active[0])
        print(f"[*] Valuation DB: Fundamentals for {active[0]}: {funda}")

    print("[+] Validation Complete.")
    lib.close()

if __name__ == "__main__":
    validate_modular_librarian()
