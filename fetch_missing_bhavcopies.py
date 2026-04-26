"""
MYRA Missing Bhavcopy Fetcher
Fetches missing dates from tilak999/NSE-Data-bank and saves them
to D:\\01screener\\Myra\\data\\Market_Archives\\ in nse_full_YYYY-MM-DD.csv format.

Run from project root:
    python fetch_missing_bhavcopies.py
"""
import os
import time
import requests
from datetime import date, datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
ARCHIVE_DIR = r"D:\01screener\Myra\data\Market_Archives"

# Correct URL pattern: sec_bhavdata_full_DDMMYYYY.csv
DATA_URL     = "https://raw.githubusercontent.com/tilak999/NSE-Data-bank/main/data/sec_bhavdata_full_{dmy}.csv"
HISTORIC_URL = "https://raw.githubusercontent.com/tilak999/NSE-Data-bank/main/historic_data/sec_bhavdata_full_{dmy}.csv"

START_DATE = date(2021, 10, 1)
END_DATE   = date.today()

NSE_HOLIDAYS = {
    # 2021
    "2021-11-04", "2021-11-05",
    # 2022
    "2022-01-26", "2022-03-18", "2022-04-14", "2022-04-15",
    "2022-04-18", "2022-05-03", "2022-08-09", "2022-08-15",
    "2022-10-02", "2022-10-05", "2022-10-24", "2022-10-26",
    "2022-11-08", "2022-12-25",
    # 2023
    "2023-01-26", "2023-03-07", "2023-03-30", "2023-04-04",
    "2023-04-07", "2023-04-14", "2023-04-21", "2023-05-01",
    "2023-06-28", "2023-08-15", "2023-09-19", "2023-10-02",
    "2023-10-24", "2023-11-14", "2023-11-27", "2023-12-25",
    # 2024
    "2024-01-22", "2024-03-25", "2024-03-29", "2024-04-11",
    "2024-04-14", "2024-04-17", "2024-04-21", "2024-05-23",
    "2024-06-17", "2024-07-17", "2024-08-15", "2024-10-02",
    "2024-11-01", "2024-11-15", "2024-11-20", "2024-12-25",
    # 2025
    "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10",
    "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15",
    "2025-08-27", "2025-10-02", "2025-10-20", "2025-10-21",
    "2025-10-22", "2025-11-05", "2025-12-25",
    # 2026
    "2026-01-26", "2026-03-02", "2026-03-20", "2026-04-02",
    "2026-04-10", "2026-04-14", "2026-05-01",
}
# ──────────────────────────────────────────────────────────────────────────────


def get_existing_dates():
    """Returns set of dates already downloaded as YYYY-MM-DD strings."""
    existing = set()
    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR)
        return existing
    for f in os.listdir(ARCHIVE_DIR):
        if not f.endswith(".csv"):
            continue
        name = f.replace("nse_full_", "").replace(".csv", "")
        if len(name) == 10 and "-" in name:
            existing.add(name)
        elif len(name) == 8 and name.isdigit():
            try:
                d = datetime.strptime(name, "%d%m%Y").strftime("%Y-%m-%d")
                existing.add(d)
            except ValueError:
                pass
    return existing


def get_missing_dates(existing):
    missing = []
    current = START_DATE
    while current <= END_DATE:
        ds = current.isoformat()
        if (
            current.weekday() < 5
            and ds not in NSE_HOLIDAYS
            and ds not in existing
        ):
            missing.append(ds)
        current += timedelta(days=1)
    return sorted(missing)


def fetch_date(date_str, session):
    """
    Converts YYYY-MM-DD to DDMMYYYY and tries data/ then historic_data/.
    Returns CSV text if found, None otherwise.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dmy = dt.strftime("%d%m%Y")  # e.g. 01092021

    for url_template in [DATA_URL, HISTORIC_URL]:
        url = url_template.format(dmy=dmy)
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200 and "SYMBOL" in r.text.upper():
                return r.text
        except requests.RequestException:
            pass
    return None


def main():
    print("\n[MYRA] Missing Bhavcopy Fetcher")
    print(f"  Archive : {ARCHIVE_DIR}")
    print(f"  Range   : {START_DATE} to {END_DATE}\n")

    existing = get_existing_dates()
    print(f"  Already have : {len(existing)} files")

    missing = get_missing_dates(existing)
    print(f"  Missing      : {len(missing)} trading days\n")

    if not missing:
        print("[OK] Nothing to download. You are up to date!")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    downloaded = 0
    not_found  = 0

    for i, date_str in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {date_str} ... ", end="", flush=True)

        csv_text = fetch_date(date_str, session)

        if csv_text:
            out_path = os.path.join(ARCHIVE_DIR, f"nse_full_{date_str}.csv")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(csv_text)
            print("Downloaded")
            downloaded += 1
            time.sleep(0.3)
        else:
            print("Not found (holiday or repo gap)")
            not_found += 1

    print(f"\n[MYRA] Done.")
    print(f"  Downloaded  : {downloaded}")
    print(f"  Not found   : {not_found} (genuine holidays or repo gaps)")
    print(f"\nNext: run python myra_app/ingest_bhavcopy.py to load into DB")


if __name__ == "__main__":
    main()