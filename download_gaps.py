import os
import time
import requests
import pandas as pd
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIG ---
BASE_DIR = r"D:\01screener\Myra"
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "Market_Archives")
MISSING_CSV = os.path.join(BASE_DIR, "data", "missing_data.csv")

def get_session_cookies():
    print("[*] Opening Stealth Session to NSE...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.get("https://www.nseindia.com/")
    time.sleep(3) 
    cookies = {c['name']: c['value'] for c in driver.get_cookies()}
    ua = driver.execute_script("return navigator.userAgent")
    driver.quit()
    return cookies, ua

if __name__ == "__main__":
    if not os.path.exists(MISSING_CSV):
        print(f"[!] {MISSING_CSV} not found. Run missing_detector.py first.")
        exit()

    # Get the unique dates we actually need
    df_missing = pd.read_csv(MISSING_CSV)
    required_dates = sorted(df_missing["missing_date"].unique())
    
    # Check what we already have
    existing_files = os.listdir(ARCHIVE_DIR)
    
    # Filter to only get dates that don't have a corresponding nse_full_YYYY-MM-DD.csv
    to_download = []
    for d_str in required_dates:
        expected_file = f"nse_full_{d_str}.csv"
        if expected_file not in existing_files:
            to_download.append(d_str)

    if not to_download:
        print("[+] All dates from missing_data.csv are already in Market_Archives!")
        exit()

    print(f"[MYRA] Targeting {len(to_download)} missing files...")
    cookies, ua = get_session_cookies()
    headers = {"User-Agent": ua, "Referer": "https://www.nseindia.com/"}

    count = 0
    for d_str in to_download:
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        d_url = dt.strftime("%d%m%Y") # DDMMYYYY
        local_path = os.path.join(ARCHIVE_DIR, f"nse_full_{d_str}.csv")
        
        url = f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{d_url}.csv"
        
        try:
            r = requests.get(url, headers=headers, cookies=cookies, timeout=15)
            if r.status_code == 200 and "SYMBOL" in r.text:
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                count += 1
                print(f"   [+] Downloaded: {d_str} ({count}/{len(to_download)})")
                time.sleep(2) # Safety delay to avoid IP block
            else:
                print(f"   [!] Failed {d_str}: Status {r.status_code}")
                # If we get a 403, the session might be dead
                if r.status_code == 403:
                    print("[!!] Blocked by NSE. Stopping to save IP reputation.")
                    break
        except Exception as e:
            print(f"   [!] Error {d_str}: {e}")

    print(f"\n[FINISH] Downloaded {count} new files.")