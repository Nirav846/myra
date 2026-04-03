import sqlite3
import os
import sys

# Fix path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
from tools.symbol_mapper import SymbolMapper

def unify_database_symbols():
    db_path = os.path.join("db", "technical.db")
    if not os.path.exists(db_path):
        print("[!] DB missing.")
        return

    mapper = SymbolMapper()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("[MYRA] Unifying symbol lineage in technical.db...")
    
    # 1. Get all unique symbols in the DB
    unique_symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM technical_data").fetchall()]
    print(f"[*] Found {len(unique_symbols)} unique raw symbols.")

    updates = 0
    for sym in unique_symbols:
        current = mapper.get_current_symbol(sym)
        if current != sym:
            # We have a candidate for unification
            print(f"[*] Mapping {sym} -> {current}...")
            
            # Use INSERT OR REPLACE to move data to the current symbol name
            # then delete the old one.
            try:
                # 1. Copy to new name
                cursor.execute(f"""
                    INSERT OR REPLACE INTO technical_data 
                    (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                    SELECT '{current}', date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio
                    FROM technical_data WHERE symbol = '{sym}'
                """)
                
                # 2. Delete old name
                cursor.execute(f"DELETE FROM technical_data WHERE symbol = '{sym}'")
                updates += 1
            except Exception as e:
                print(f"    [!] Error mapping {sym}: {e}")

    conn.commit()
    conn.close()
    print(f"[+] Unification Complete. {updates} symbols updated.")

if __name__ == "__main__":
    unify_database_symbols()
