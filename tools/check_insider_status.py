import sqlite3
import os
import sys

# Fix path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

def check_sync_status():
    meta_db = os.path.join(PROJECT_ROOT, "db", "meta.db")
    inst_db = os.path.join(PROJECT_ROOT, "db", "institutional.db")
    
    conn_m = sqlite3.connect(meta_db)
    ls = conn_m.execute("SELECT value FROM metadata WHERE key = 'last_insider_sync'").fetchone()
    print(f"Metadata Last Sync: {ls[0] if ls else 'NEVER'}")
    conn_m.close()
    
    conn_i = sqlite3.connect(inst_db)
    md = conn_i.execute("SELECT MAX(date), COUNT(*) FROM insider_trades").fetchone()
    print(f"Database Max Date:  {md[0]}")
    print(f"Total Records:      {md[1]}")
    conn_i.close()

if __name__ == "__main__":
    check_sync_status()
