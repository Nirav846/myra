import sqlite3
import pandas as pd
import requests
import io
import zipfile
from datetime import datetime

# Path Configuration
DB_PATH = "db/myra_technical.db"

def get_stealth_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.nseindia.com/"
    })
    # Hit main page first to get cookies
    session.get("https://www.nseindia.com", timeout=10)
    return session

def run_daily_update():
    # It is April 13, 2026. Use this format for NSE URLS
    date_obj = datetime.now()
    date_str = date_obj.strftime("%d%m%Y") # 13042026
    day = date_obj.strftime("%d")
    month_name = date_obj.strftime("%b").upper() # APR
    year = date_obj.strftime("%Y")

    session = get_stealth_session()
    
    # 1. Fetch Bhavcopy
    bhav_url = f"https://www.nseindia.com/content/historical/EQUITIES/{year}/{month_name}/cm{day}{month_name}{year}bhav.csv.zip"
    
    try:
        r = session.get(bhav_url)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = pd.read_csv(z.open(z.namelist()[0]))
        
        # 2. Hardening Logic: Convert to lowercase Atomic Schema
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.rename(columns={'tottrdqty': 'volume', 'prevclose': 'prev_close'})
        
        # 3. Append to 945MB Vault
        conn = sqlite3.connect(DB_PATH)
        df.to_sql("technical_data", conn, if_exists="append", index=False)
        conn.close()
        
        print(f"Successfully added {len(df)} rows for {date_str} to Atomic Vault.")
    except Exception as e:
        print(f"Fetch failed: {e}. Data likely not ready yet (Check after 6 PM IST).")

if __name__ == "__main__":
    run_daily_update()