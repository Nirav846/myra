import os
import pandas as pd
import sqlite3

# Absolute paths to ensure it works regardless of where you run it
BASE_DIR = r"D:\01screener\Myra"
DB_PATH = os.path.join(BASE_DIR, "db", "myra_technical.db")
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "Market_Archives")

def ingest_all_offline():
    if not os.path.exists(ARCHIVE_DIR):
        print(f"[!] Archive directory missing: {ARCHIVE_DIR}")
        return

    # Process only the files we just standardized
    files = [f for f in os.listdir(ARCHIVE_DIR) if f.startswith("nse_full_")]
    
    if not files:
        print("[!] No standardized files found to ingest.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total_added = 0

    print(f"[MYRA] Ingesting {len(files)} days of institutional data into 19-column schema...")

    for filename in files:
        try:
            # Extract date from filename (nse_full_YYYY-MM-DD.csv)
            date_str = filename.replace("nse_full_", "").replace(".csv", "")
            file_path = os.path.join(ARCHIVE_DIR, filename)
            
            df = pd.read_csv(file_path)
            
            # 1. Strip Header Spaces
            df.columns = [c.strip().upper() for c in df.columns]
            
            # 2. Strip Data Spaces (Handles ' EQ' and ' 102')
            df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)
            
            # 3. Filter for Equities
            df = df[df['SERIES'].isin(['EQ', 'BE', 'SM', 'ST', 'BZ'])]
            
            records = []
            for _, row in df.iterrows():
                vol = pd.to_numeric(row.get("TTL_TRD_QNTY", 0), errors='coerce')
                deliv = pd.to_numeric(row.get("DELIV_QTY", 0), errors='coerce')
                
                # --- THE TRILOGY GUARDRAIL ---
                if pd.isna(deliv) or deliv <= 1.0: 
                    continue
                
                close = float(row.get("CLOSE_PRICE", 0))
                
                # We prepare exactly 12 values
                records.append((
                    str(row.get("SYMBOL")).strip().upper(),
                    date_str,
                    float(row.get("OPEN_PRICE", 0)),
                    float(row.get("HIGH_PRICE", 0)),
                    float(row.get("LOW_PRICE", 0)),
                    close,
                    int(vol), int(deliv),
                    int(pd.to_numeric(row.get("NO_OF_TRADES", 0), errors='coerce')),
                    float(pd.to_numeric(row.get("AVG_PRICE", close), errors='coerce')),
                    (deliv/vol*100) if vol > 0 else 0,
                    (deliv/vol) if vol > 0 else 0
                ))

            if records:
                # TARGETED INSERT: Explicitly naming the 12 columns we want to fill.
                # SQLite will allow the other 7 columns to remain NULL or default.
                sql = """
                INSERT OR REPLACE INTO technical_data 
                (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                cursor.executemany(sql, records)
                conn.commit()
                total_added += len(records)
                print(f"[+] {date_str}: Processed {len(records)} institutional rows.")
        except Exception as e:
            print(f"[!] Error processing {filename}: {e}")

    conn.close()
    print(f"\n[SUCCESS] Added {total_added} institutional rows to myra_technical.db")

if __name__ == "__main__":
    ingest_all_offline()