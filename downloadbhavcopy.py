import os
import time
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd

ARCHIVE_DIR = "data/Market_Archives"
os.makedirs(ARCHIVE_DIR, exist_ok=True)
MANIFEST_PATH = os.path.join(ARCHIVE_DIR, "manifest.csv")

def load_holidays(year):
    """Attempt to get market holidays via Librarian, fallback to empty set."""
    try:
        from myra_app.librarian import Librarian
        lib = Librarian(read_only=True)
        return lib.get_market_holidays(year)
    except Exception:
        return set()

def get_session_cookies():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.get("https://www.nseindia.com/")
    time.sleep(2) 
    cookies = {c['name']: c['value'] for c in driver.get_cookies()}
    ua = driver.execute_script("return navigator.userAgent")
    driver.quit()
    return cookies, ua

if __name__ == "__main__":
    missing_csv = "data/missing_data.csv"
    df_missing = pd.read_csv(missing_csv)
    unique_dates = sorted(df_missing["missing_date"].unique())
    
    print(f"[MYRA] Downloading {len(unique_dates)} files to {ARCHIVE_DIR}...")
    cookies, ua = get_session_cookies()
    headers = {"User-Agent": ua, "Referer": "https://www.nseindia.com/"}

    # load holidays for years involved
    years = sorted({datetime.strptime(d, "%Y-%m-%d").year for d in unique_dates})
    holiday_map = {y: load_holidays(y) for y in years}

    attempted = 0
    skipped_exists = 0
    skipped_holiday = 0
    downloaded = 0
    failed = 0

    for d_str in unique_dates:
        attempted += 1
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        # Skip weekends
        if dt.weekday() >= 5:
            print(f"[-] Skipping weekend {d_str}")
            skipped_holiday += 1
            # log manifest
            with open(MANIFEST_PATH, "a", encoding="utf-8") as m:
                m.write(f"{d_str},SKIPPED_WEEKEND,,\n")
            continue

        # Skip NSE holidays if known
        if d_str in holiday_map.get(dt.year, set()):
            print(f"[-] Skipping holiday {d_str}")
            skipped_holiday += 1
            with open(MANIFEST_PATH, "a", encoding="utf-8") as m:
                m.write(f"{d_str},SKIPPED_HOLIDAY,,\n")
            continue

        d_url = dt.strftime("%d%m%Y")
        local_path = os.path.join(ARCHIVE_DIR, f"nse_full_{d_str}.csv")

        if os.path.exists(local_path):
            skipped_exists += 1
            print(f"[-] Already exists: {d_str}")
            with open(MANIFEST_PATH, "a", encoding="utf-8") as m:
                m.write(f"{d_str},EXISTS,{local_path},\n")
            continue

        url = f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{d_url}.csv"
        try:
            r = requests.get(url, headers=headers, cookies=cookies, timeout=15)
            if r.status_code == 200 and "SYMBOL" in r.text:
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                print(f"[+] Downloaded: {d_str}")
                downloaded += 1
                with open(MANIFEST_PATH, "a", encoding="utf-8") as m:
                    m.write(f"{d_str},DOWNLOADED,{local_path},\n")
                time.sleep(1.5) # Avoid NSE ban
            else:
                print(f"[!] Failed {d_str}: Status {r.status_code}")
                failed += 1
                with open(MANIFEST_PATH, "a", encoding="utf-8") as m:
                    m.write(f"{d_str},FAILED,{url},{r.status_code}\n")
        except Exception as e:
            print(f"[!] Error {d_str}: {e}")
            failed += 1
            with open(MANIFEST_PATH, "a", encoding="utf-8") as m:
                m.write(f"{d_str},ERROR,,{e}\n")

    print(f"Download summary: attempted={attempted} downloaded={downloaded} skipped_exists={skipped_exists} skipped_holiday={skipped_holiday} failed={failed}")