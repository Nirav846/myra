import os
import sys
import pandas as pd
import sqlite3
from datetime import datetime
from tqdm import tqdm
import time
from io import StringIO
import concurrent.futures

# Fix path
sys.path.append(os.getcwd())

try:
    from myra_app.fetcher import DataFetcher
except ImportError:
    sys.path.append(os.path.join(os.getcwd(), ".."))
    from fetcher import DataFetcher

from tools.symbol_mapper import SymbolMapper

# Use PKNSETools for robust fetching
try:
    from PKNSETools.Benny import NSE
except ImportError:
    NSE = None

class DeepBhavcopyRecovery:
    """
    Advanced Multi-Threaded Recovery Layer with Holiday Awareness.
    """
    def __init__(self):
        self.fetcher = DataFetcher()
        self.mapper = SymbolMapper()
        self.archive_dir = os.path.join("data", "Market_Archives")
        os.makedirs(self.archive_dir, exist_ok=True)
        self.nse_tool = NSE(self.archive_dir) if NSE else None
        self._load_trading_calendar()

    def _load_trading_calendar(self):
        """Loads official trading dates to prevent fetching on holidays."""
        self.valid_dates = set()
        cal_path = os.path.join("data", "trading_calendar_master.csv")
        if os.path.exists(cal_path):
            try:
                df_cal = pd.read_csv(cal_path)
                self.valid_dates = set(df_cal['date'].astype(str).tolist())
            except: pass

    def fetch_date(self, target_date):
        d_str = target_date.strftime("%Y-%m-%d")
        
        # Holiday Awareness: Skip if not a valid trading date
        if self.valid_dates and d_str not in self.valid_dates:
            return None, d_str
            
        # Future Awareness: Skip if date is today or later (unless after market hours)
        now = datetime.now()
        if target_date.date() >= now.date():
            # If today, only allow after 6 PM
            if target_date.date() == now.date() and now.hour < 18:
                return None, d_str
            if target_date.date() > now.date():
                return None, d_str

        local_standard = os.path.join(self.archive_dir, f"nse_full_{d_str}.csv")
        local_pknse = os.path.join(self.archive_dir, f"sec_bhavdata_full_{target_date.strftime('%d%m%Y')}.csv")
        
        # 1. Local Cache Check
        if os.path.exists(local_standard):
            try:
                with open(local_standard, 'r', encoding='utf-8', errors='ignore') as f: return f.read(), d_str
            except: pass
        if os.path.exists(local_pknse):
            try:
                with open(local_pknse, 'r', encoding='utf-8', errors='ignore') as f: return f.read(), d_str
            except: pass

        if not self.nse_tool: return None, d_str

        # 2. Try PKNSETools (Network Fetch)
        try:
            path = self.nse_tool.deliveryBhavcopy(target_date.date() if isinstance(target_date, datetime) else target_date)
            if path and os.path.exists(path):
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    with open(local_standard, 'w', encoding='utf-8') as fs: fs.write(content)
                    return content, d_str
        except: pass
        
        return None, d_str

def backfill_missing_data(missing_csv="data/missing_data.csv", db_path="db/technical.db", target_symbols=None, threads=4):
    print(f"[MYRA] Starting Accelerated Recovery ({threads} threads)...")
    
    if not os.path.exists(missing_csv):
        print(f"[!] {missing_csv} not found.")
        return

    df_missing = pd.read_csv(missing_csv)
    if df_missing.empty: return
    if target_symbols:
        df_missing = df_missing[df_missing['symbol'].isin(target_symbols)]

    recovery = DeepBhavcopyRecovery()
    missing_dates = sorted(df_missing['missing_date'].unique(), reverse=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    stats = {"rows": 0, "errors": 0}

    # Use ThreadPool for faster date fetching
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_date = {executor.submit(recovery.fetch_date, datetime.strptime(d, "%Y-%m-%d")): d for d in missing_dates}
        
        for future in tqdm(concurrent.futures.as_completed(future_to_date), total=len(missing_dates), desc="Recovering"):
            d_str = future_to_date[future]
            try:
                csv_text, date_processed = future.result()
                if csv_text:
                    df_bhav = pd.read_csv(StringIO(csv_text))
                    df_bhav.columns = [c.strip().upper() for c in df_bhav.columns]
                    
                    # Target CURRENT symbols missing for this date
                    targets = set(df_missing[df_missing['missing_date'] == date_processed]['symbol'])
                    
                    records = []
                    for _, row in df_bhav.iterrows():
                        raw_sym = str(row['SYMBOL']).strip().upper()
                        # Resolve raw bhavcopy symbol to its CURRENT name
                        current_name = recovery.mapper.get_current_symbol(raw_sym)
                        
                        # IMPORTANT: If the CURRENT name is in our target list, we save it
                        if current_name in targets:
                            # Use current name for insertion to keep technical.db consistent
                            vol = row.get('TTL_TRD_QNTY', row.get('TOTTRDQTY', 0))
                            close = row.get('CLOSE_PRICE', row.get('CLOSE', 0))
                            deliv = row.get('DELIV_QTY', 0)
                            
                            records.append((
                                current_name, date_processed,
                                float(row.get('OPEN_PRICE', row.get('OPEN', 0))),
                                float(row.get('HIGH_PRICE', row.get('HIGH', 0))),
                                float(row.get('LOW_PRICE', row.get('LOW', 0))),
                                float(close),
                                int(vol),
                                int(deliv),
                                int(row.get('NO_OF_TRADES', row.get('TOTALTRADES', 0))),
                                float(row.get('AVG_PRICE', close)),
                                float(row.get('DELIV_PER', 0)),
                                (float(deliv)/float(vol)) if vol and vol > 0 else 0
                            ))
                    
                    if records:
                        cursor.executemany("INSERT OR IGNORE INTO technical_data VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", records)
                        conn.commit()
                        stats["rows"] += len(records)
                
                time.sleep(0.05)
            except Exception:
                stats["errors"] += 1

    conn.close()
    print(f"\n[+] Recovery Complete. Added {stats['rows']} rows. Errors: {stats['errors']}")

if __name__ == "__main__":
    import sys
    targets = None
    thread_count = 8 # Safe for local cache heavy work
    for arg in sys.argv:
        if "," in arg: targets = arg.split(',')
    backfill_missing_data(target_symbols=targets, threads=thread_count)
