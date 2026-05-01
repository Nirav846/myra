import json
import requests
from datetime import datetime


def test_endpoints():
    print("🚀 Initiating MYRA Historical Depth Audit (2 Years Back)...")

    try:
        with open("myra_sources.json", "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("❌ Error: myra_sources.json not found.")
        return

    # March 2024 Trading Dates (2 Years Back)
    test_dates = [
        datetime(2024, 3, 11),
        datetime(2024, 3, 12),
        datetime(2024, 3, 13),
        datetime(2024, 3, 14),
    ]

    headers = data.get("headers", {}).get("standard_myra", {})

    # Performance Guard Compliant (Fix 27-32)
    MONTHS = [
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
    ]
    for t_date in test_dates:
        d, m, y = t_date.day, t_date.month, t_date.year
        ds = f"{d:02d}{m:02d}{y}"
        ds_leg = f"{d:02d}{MONTHS[m-1]}{y}"
        year_str = str(y)
        mon_str = MONTHS[m - 1]

        print(f"\n--- Testing Date: {t_date.date().isoformat()} ---")

        # 1. Test Primary (Archives Full)
        primary_url = (
            f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv"
        )
        try:
            r = requests.get(primary_url, headers=headers, timeout=5)
            status = "✅ ALIVE" if r.status_code == 200 else f"❌ DEAD ({r.status_code})"
            print(f"Primary (sec_bhavdata_full): {status}")
        except Exception:
            print("Primary: ❌ FAILED")

        # 2. Test Fallback Price (Classic ZIP)
        zip_url = f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{year_str}/{mon_str}/cm{ds_leg}bhav.csv.zip"
        try:
            r = requests.get(zip_url, headers=headers, timeout=5)
            status = "✅ ALIVE" if r.status_code == 200 else f"❌ DEAD ({r.status_code})"
            print(f"Fallback (Classic ZIP):     {status}")
        except Exception:
            print("Fallback ZIP: ❌ FAILED")

        # 3. Test Fallback Delivery (MTO DAT)
        mto_url = f"https://archives.nseindia.com/archives/equities/mto/MTO_{ds}.DAT"
        try:
            r = requests.get(mto_url, headers=headers, timeout=5)
            status = "✅ ALIVE" if r.status_code == 200 else f"❌ DEAD ({r.status_code})"
            print(f"Fallback (MTO Delivery):    {status}")
        except Exception:
            print("Fallback MTO: ❌ FAILED")

    print("\n" + "=" * 50)
    print(f"📊 DIAGNOSTIC RESULTS: {len(alive)} Alive | {len(dead)} Dead")
    print("=" * 50)
    print("🛑 DEAD/BLOCKED SOURCES TO PURGE OR FIX:")
    for d in dead:
        print(f" - {d}")


if __name__ == "__main__":
    test_endpoints()
