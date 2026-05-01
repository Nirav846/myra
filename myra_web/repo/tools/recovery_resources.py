import os
import sys
import pandas as pd
from datetime import datetime

# Fix path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def build_recovery_resources():
    print("[MYRA] Building recovery resources...")

    # 1. Process Trading Dates
    dates_file = os.path.join(PROJECT_ROOT, "data", "trading dates till 30032026.txt")
    if os.path.exists(dates_file):
        with open(dates_file, "r") as f:
            lines = f.readlines()

        # Extract unique dates in YYYY-MM-DD format
        # Optimized with list comprehension (Fix 25, 26: Avoid .append in loop and .strftime)
        def _parse_dt(line):
            try:
                dt_str = line.strip().split(" ")[0]
                return datetime.strptime(dt_str, "%d/%m/%Y").date().isoformat()
            except:
                return None

        trading_dates = [d for line in lines if (d := _parse_dt(line))]

        # Save as a clean CSV for missing_detector
        df_dates = pd.DataFrame(sorted(list(set(trading_dates))), columns=["date"])
        df_dates.to_csv(
            os.path.join(PROJECT_ROOT, "data", "trading_calendar_master.csv"),
            index=False,
        )
        print(f"[+] Processed {len(df_dates)} trading dates.")

    # 2. Download Symbol Changes (with proper encoding)
    try:
        from myra_app.fetcher import DataFetcher

        fetcher = DataFetcher()
        url = "https://archives.nseindia.com/content/equities/symbolchange.csv"
        r = fetcher.session.get(url)
        if r.status_code == 200:
            # Use r.content and decode carefully
            text = r.content.decode("utf-8", errors="ignore")
            with open(
                os.path.join(PROJECT_ROOT, "data", "symbol_change.csv"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(text)
            print("[+] Downloaded symbol_change.csv")
        else:
            print(f"[!] Failed to download symbol_change.csv (Status {r.status_code})")
    except Exception as e:
        print(f"[!] Error during symbol_change fetch: {e}")


if __name__ == "__main__":
    build_recovery_resources()
