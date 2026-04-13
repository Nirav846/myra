import os
import pandas as pd
import sqlite3
import time
from datetime import datetime
from io import StringIO
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIG ---
DB_PATH = "db/myra_technical.db"
ARCHIVE_DIR = "data/Market_Archives"
os.makedirs(ARCHIVE_DIR, exist_ok=True)


<<<<<<< HEAD
def get_session_cookies():
    options = Options()
    options.add_argument("--headless")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
=======
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
                self.valid_dates = set(df_cal["date"].astype(str).tolist())
            except:
                pass

    def fetch_date(self, target_date):
        # Performance Guard Compliant (Fix 48)
        d_str = (
            target_date.date().isoformat()
            if hasattr(target_date, "date")
            else str(target_date)
        )

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
        # Fix 64: Manual formatting to avoid .strftime
        ds_leg = f"{target_date.day:02d}{target_date.month:02d}{target_date.year}"
        local_pknse = os.path.join(self.archive_dir, f"sec_bhavdata_full_{ds_leg}.csv")

        # 1. Local Cache Check
        if os.path.exists(local_standard):
            try:
                with open(local_standard, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(), d_str
            except:
                pass
        if os.path.exists(local_pknse):
            try:
                with open(local_pknse, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(), d_str
            except:
                pass

        if not self.nse_tool:
            return None, d_str

        # 2. Try PKNSETools (Network Fetch)
        try:
            path = self.nse_tool.deliveryBhavcopy(
                target_date.date() if isinstance(target_date, datetime) else target_date
            )
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    with open(local_standard, "w", encoding="utf-8") as fs:
                        fs.write(content)
                    return content, d_str
        except:
            pass

        return None, d_str


def backfill_missing_data(
    missing_csv="data/missing_data.csv",
    db_path="db/technical.db",
    target_symbols=None,
    threads=4,
):
    print(f"[MYRA] Starting Accelerated Recovery ({threads} threads)...")

    if not os.path.exists(missing_csv):
        print(f"[!] {missing_csv} not found.")
        return

    df_missing = pd.read_csv(missing_csv)
    if df_missing.empty:
        return
    if target_symbols:
        df_missing = df_missing[df_missing["symbol"].isin(target_symbols)]

    recovery = DeepBhavcopyRecovery()
    missing_dates = sorted(df_missing["missing_date"].unique(), reverse=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    stats = {"rows": 0, "errors": 0}

    # Use ThreadPool for faster date fetching
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_date = {
            executor.submit(recovery.fetch_date, datetime.strptime(d, "%Y-%m-%d")): d
            for d in missing_dates
        }

        total_dates = len(missing_dates)
        for i, future in enumerate(concurrent.futures.as_completed(future_to_date)):
            myra_log(i + 1, total_dates, desc="Recovering")

            try:
                csv_text, date_processed = future.result()
                if csv_text:
                    df_bhav = pd.read_csv(StringIO(csv_text))
                    df_bhav.columns = [c.strip().upper() for c in df_bhav.columns]

                    # Target CURRENT symbols missing for this date
                    # Fix 122: Use .loc for safety/performance
                    targets = set(
                        df_missing.loc[
                            df_missing["missing_date"] == date_processed, "symbol"
                        ]
                    )

                    # Optimized with list comprehension (Fix 138: Avoid .append in loop)
                    def _to_record(row_dict):
                        raw_sym = str(row_dict.get("SYMBOL", "")).strip().upper()
                        current_name = recovery.mapper.get_current_symbol(raw_sym)
                        if current_name in targets:
                            vol = row_dict.get(
                                "TTL_TRD_QNTY", row_dict.get("TOTTRDQTY", 0)
                            )
                            close = row_dict.get(
                                "CLOSE_PRICE", row_dict.get("CLOSE", 0)
                            )
                            deliv = row_dict.get("DELIV_QTY", 0)
                            return (
                                current_name,
                                date_processed,
                                float(
                                    row_dict.get("OPEN_PRICE", row_dict.get("OPEN", 0))
                                ),
                                float(
                                    row_dict.get("HIGH_PRICE", row_dict.get("HIGH", 0))
                                ),
                                float(
                                    row_dict.get("LOW_PRICE", row_dict.get("LOW", 0))
                                ),
                                float(close),
                                int(vol),
                                int(deliv),
                                int(
                                    row_dict.get(
                                        "NO_OF_TRADES", row_dict.get("TOTALTRADES", 0)
                                    )
                                ),
                                float(row_dict.get("AVG_PRICE", close)),
                                float(row_dict.get("DELIV_PER", 0)),
                                (float(deliv) / float(vol)) if vol and vol > 0 else 0,
                            )
                        return None

                    records = [
                        r
                        for row in df_bhav.to_dict('records')
                        if (r := _to_record(row))
                    ]

                    if records:
                        cursor.executemany(
                            "INSERT OR IGNORE INTO technical_data VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            records,
                        )
                        conn.commit()
                        stats["rows"] += len(records)

                time.sleep(0.05)
            except Exception:
                stats["errors"] += 1

    conn.close()
    print(
        f"\n[+] Recovery Complete. Added {stats['rows']} rows. Errors: {stats['errors']}"
>>>>>>> e50917457afa95bc0fb2a4e406368302eb759dc8
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
    d_url = target_date.strftime("%d%m%Y")
    iso_date = target_date.strftime("%Y-%m-%d")
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

    # 2. Strip Data Spaces (Crucial: Fixes ' GS' and ' 102')
    df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)

    # 3. Filter for Equity Series
    valid_series = ["EQ", "BE", "SM", "ST", "BZ"]
    df = df[df["SERIES"].isin(valid_series)]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    count = 0

    for _, row in df.iterrows():
        try:
            # 4. Clean numeric strings (converts ' 102' -> 102.0)
            vol = pd.to_numeric(row.get("TTL_TRD_QNTY", 0), errors="coerce")
            deliv = pd.to_numeric(row.get("DELIV_QTY", 0), errors="coerce")

            # --- TRILOGY GUARDRAIL ---
            if pd.isna(deliv) or deliv <= 1.0:
                continue

            close = float(row.get("CLOSE_PRICE", 0))
            trades = int(pd.to_numeric(row.get("NO_OF_TRADES", 0), errors="coerce"))
            avg_p = float(pd.to_numeric(row.get("AVG_PRICE", close), errors="coerce"))

            record = (
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

            cursor.execute(
                "INSERT OR IGNORE INTO technical_data VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                record,
            )
            count += 1
        except Exception:
            continue

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
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        path = download_csv(dt, cookies, ua)
        if path:
            added = ingest_to_db(path, d_str)
            print(f"[+] {d_str}: Added {added} institutional rows.")
            time.sleep(1.5)
        else:
            print(f"[!] {d_str}: Failed.")
