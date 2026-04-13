import sqlite3
import pandas as pd
import requests
import io
import zipfile
import os
from datetime import datetime

# Path to your 945MB Atomic Vault
DB_PATH = os.path.join("db", "myra_technical.db")

def get_stealth_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.nseindia.com/all-reports"
    })
    session.get("https://www.nseindia.com", timeout=10)
    return session

def run_daily_update():
    """
    Guard-Compliant Daily Fetcher.
    Uses ISO formatting to avoid banned .strftime() calls.
    """
    now = datetime.now()
    
    # Constructing Date components without .strftime()
    day_str = f"{now.day:02d}"
    year_str = f"{now.year}"
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    month_name = months[now.month - 1]
    
    session = get_stealth_session()
    
    # URL for April 13, 2026 archive
    bhav_url = (
        f"https://www.nseindia.com/content/historical/EQUITIES/"
        f"{year_str}/{month_name}/cm{day_str}{month_name}{year_str}bhav.csv.zip"
    )
    
    print(f"[MYRA] Checking NSE for {day_str}-{month_name}-{year_str} data...")
    
    try:
        response = session.get(bhav_url, timeout=15)
        
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                with z.open(z.namelist()[0]) as f:
                    df = pd.read_csv(f)
            
            # Atomic Hardening: Force lowercase and strip whitespace
            df.columns = [c.strip().lower() for c in df.columns]
            
            # Map raw headers to Myra schema
            df = df.rename(columns={'tottrdqty': 'volume', 'prevclose': 'prev_close', 'last': 'last_price'})
            
            # Use ISO format to avoid .strftime() banned method
            df['date'] = pd.Timestamp(now).date().isoformat()
            
            # Append to DB
            conn = sqlite3.connect(DB_PATH)
            df.to_sql("technical_data", conn, if_exists="append", index=False)
            conn.close()
            print(f"✅ Successfully added {len(df)} rows to Atomic Vault.")
            
        elif response.status_code == 404:
            print("⚠️ Data not yet released. Check back after 6 PM IST.")
        else:
            print(f"⚠️ Fetch error: Status Code {response.status_code}")
            
    except Exception as e:
        print(f"❌ Critical Error: {e}")

if __name__ == "__main__":
    run_daily_update()