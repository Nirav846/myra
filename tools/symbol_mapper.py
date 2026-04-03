import os
import pandas as pd
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

class SymbolMapper:
    """
    NSE Symbol Lineage Tracker (v2.0).
    Uses manual maps + symbol_change.csv.
    """
    def __init__(self, csv_path=os.path.join(PROJECT_ROOT, "data", "symbol_change.csv"), manual_path=os.path.join(PROJECT_ROOT, "config", "manual_symbol_map.json")):
        self.forward_map = {} # old -> new
        
        # 1. Load Manual Map (Highest Priority)
        if os.path.exists(manual_path):
            try:
                with open(manual_path, 'r') as f:
                    self.forward_map.update(json.load(f))
            except: pass

        # 2. Load NSE Symbol Change CSV
        if os.path.exists(csv_path):
            try:
                # Load without header to avoid name mismatches
                df = pd.read_csv(csv_path, header=None)
                # Usually: Col 1 = Old, Col 2 = New
                for _, row in df.iterrows():
                    try:
                        old_sym = str(row[1]).strip().upper()
                        new_sym = str(row[2]).strip().upper()
                        if old_sym != "OLD SYMBOL" and old_sym not in self.forward_map:
                            self.forward_map[old_sym] = new_sym
                    except: continue
            except Exception as e:
                print(f"[!] SymbolMapper: Error loading CSV: {e}")

    def get_current_symbol(self, symbol):
        """Recursively finds the latest name for a symbol."""
        if not symbol: return ""
        sym = str(symbol).upper().strip()
        visited = set()
        while sym in self.forward_map and sym not in visited:
            visited.add(sym)
            sym = self.forward_map[sym]
        return sym

if __name__ == "__main__":
    mapper = SymbolMapper()
    print(f"Current for DISHMAN: {mapper.get_current_symbol('DISHMAN')}")
    print(f"Current for MINDTREE: {mapper.get_current_symbol('MINDTREE')}")
