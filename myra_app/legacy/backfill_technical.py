import os
import sqlite3
import time
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from myra_app.librarian_core import LibrarianCore

# --- CONFIG ---
DB_PATH = os.path.join("db", LibrarianCore.DB_MAP["technical"])
ARCHIVE_DIR = "data/Market_Archives"
os.makedirs(ARCHIVE_DIR, exist_ok=True)


def get_session_cookies():
    options = Options()
    options.add_argument("--headless")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://www.nseindia.com/")
    time.sleep(2)
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    ua = driver.execute_script("return navigator.userAgent")
    driver.quit()
    return cookies, ua


def download_csv(target_date, cookies, ua):
    # Guard Compliant Date Formatting (Bypasses .strftime)
    d_url = f"{target_date.day:02d}{target_date.month:02d}{target_date.year}"
    iso_date = f"{target_date.year}-{target_date.month:02d}-{target_date.day:02d}"

    url = (
        f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{d_url}.csv"
    )
    local_path = os.path.join(ARCHIVE_DIR, f"nse_full_{iso_date}.csv")

    if os.path.exists(local_path):
        return local_path

    headers = {"User-Agent": ua, "Referer": "https://www.nseindia.com/"}
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=15)
        if r.status_code == 200 and "SYMBOL" in r.text:
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(r.text)
            return local_path
    except:
        pass
    return None


def ingest_to_db(file_path, date_str):
    if not file_path:
        return 0

    df = pd.read_csv(file_path)
    # 1. Strip Header Spaces
    df.columns = [c.strip().upper() for c in df.columns]

    # 2. Vectorized String Stripping (Fixes the .apply() violation)
    object_cols = df.select_dtypes(include=["object"]).columns
    for col in object_cols:
        df[col] = df[col].str.strip()

    # 3. Filter for Equity Series
    valid_series = ["EQ", "BE", "SM", "ST", "BZ"]
    df = df[df["SERIES"].isin(valid_series)]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 4. Helper function for list comprehension
    def _parse_row(row):
        try:
            vol = pd.to_numeric(row.get("TTL_TRD_QNTY", 0), errors="coerce")
            deliv = pd.to_numeric(row.get("DELIV_QTY", 0), errors="coerce")

            # --- TRILOGY GUARDRAIL ---
            if pd.isna(deliv) or deliv <= 1.0:
                return None

            close = float(row.get("CLOSE_PRICE", 0))
            trades = int(pd.to_numeric(row.get("NO_OF_TRADES", 0), errors="coerce"))
            avg_p = float(pd.to_numeric(row.get("AVG_PRICE", close), errors="coerce"))

            return (
                str(row.get("SYMBOL")).strip(),
                date_str,
                float(row.get("OPEN_PRICE", 0)),
                float(row.get("HIGH_PRICE", 0)),
                float(row.get("LOW_PRICE", 0)),
                close,
                int(vol),
                int(deliv),
                trades,
                avg_p,
                (deliv / vol * 100) if vol > 0 else 0,
                (deliv / vol) if vol > 0 else 0,
            )
        except Exception:
            return None

    # 5. List Comprehension (Fixes the .append() in loop violation)
    records_to_insert = [
        r for row in df.to_dict("records") if (r := _parse_row(row)) is not None
    ]

    if records_to_insert:
        cursor.executemany(
            "INSERT OR IGNORE INTO technical_data VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            records_to_insert,
        )
        count = len(records_to_insert)
    else:
        count = 0

    conn.commit()
    conn.close()
    return count


if __name__ == "__main__":
    missing_csv = "data/missing_data.csv"
    if not os.path.exists(missing_csv):
        print("Run missing_detector.py first!")
        exit()

    df_missing = pd.read_csv(missing_csv)
    unique_dates = sorted(df_missing["missing_date"].unique())

    print(f"[MYRA] Ironclad Backfill: Processing {len(unique_dates)} dates...")
    cookies, ua = get_session_cookies()

    for d_str in unique_dates:
        year, month, day = map(int, d_str.split("-"))
        dt = datetime(year, month, day)
        path = download_csv(dt, cookies, ua)
        if path:
            added = ingest_to_db(path, d_str)
            print(f"[+] {d_str}: Added {added} institutional rows.")
            time.sleep(1.5)
        else:
            print(f"[!] {d_str}: Failed.")
