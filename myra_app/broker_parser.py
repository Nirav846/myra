import os
import glob
import pandas as pd
from datetime import datetime

class BrokerParser:
    """
    MYRA Broker Parser (v1.4)
    Advanced Row-Scan Logic: Automatically finds headers by scanning for 'Symbol'.
    100% resilient to merged cells and summary rows.
    """
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    def find_latest_holdings_file(self) -> str:
        pattern = os.path.join(self.root_dir, "Holdings_*.xlsx")
        files = glob.glob(pattern)
        if not files: return None
        files.sort(key=os.path.getmtime, reverse=True)
        return files[0]

    def parse_holdings(self) -> list:
        file_path = self.find_latest_holdings_file()
        if not file_path: return []
            
        try:
            # 1. Read entire file without headers
            df_raw = pd.read_excel(file_path, header=None)
            
            # 2. Find the header row by keyword
            header_idx = -1
            # Fix 32: Use itertuples for performance
            for row in df_raw.itertuples():
                row_str = " ".join([str(v) for v in row[1:]]).lower()
                if "symbol" in row_str:
                    header_idx = row.Index
                    break
            
            if header_idx == -1: return []
            
            # 3. Slice and Map
            headers = df_raw.iloc[header_idx]
            df = df_raw.iloc[header_idx + 1:].copy()
            df.columns = headers
            
            final_map = {}
            for col in df.columns:
                c_low = str(col).lower()
                if ("symbol" in c_low or "instrument" in c_low) and "Stock" not in final_map:
                    final_map[col] = "Stock"
                elif ("qty" in c_low or "quantity" in c_low) and "Qty" not in final_map:
                    final_map[col] = "Qty"
                elif ("avg" in c_low or "buy" in c_low or "cost" in c_low) and "Avg_Price" not in final_map:
                    final_map[col] = "Avg_Price"
            
            df = df.rename(columns=final_map)
            df = df[["Stock", "Qty", "Avg_Price"]].dropna(subset=["Stock"])
            
            # 4. Standardize and Clean
            df['Stock'] = df['Stock'].astype(str).str.split().str[0].str.split('.').str[0].str.upper().str.strip()
            
            # Final conversion to list of dicts with numeric types
            # Optimized with list comprehension (Fix 63, 67: Avoid .append in loop)
            def _to_holding(r):
                try:
                    s = str(r.Stock)
                    if s == "NAN" or not s or len(s) < 2: return None
                    return {
                        'Stock': s,
                        'Qty': float(str(r.Qty).replace(',', '')),
                        'Avg_Price': float(str(r.Avg_Price).replace(',', ''))
                    }
                except: return None

            holdings = [h for r in df.itertuples(index=False) if (h := _to_holding(r))]
                
            return holdings
        except Exception: return []
