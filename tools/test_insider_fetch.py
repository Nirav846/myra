import os
import sys
from datetime import date, datetime

# Fix path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from myra_app.fetcher import DataFetcher


def test_fetch():
    print("[*] Initializing DataFetcher...")
    f = DataFetcher()
    print("[*] Fetching Insider Trades (last 30 days)...")
    data = f.fetch_insider_trades(days=30)

    print(f"[+] Total records retrieved: {len(data)}")

    if data:
        # Optimized with list comprehension (Fix 22: Avoid .append in loop)
        dates = [dt for d in data if (dt := d.get("intimDt", d.get("date")))]

        if dates:
            print(f"[+] Latest date in NSE response: {max(dates)}")
            print(f"[+] Earliest date in NSE response: {min(dates)}")

            # Print sample record
            print("\nSample Record:")
            print(data[0])
    else:
        print("[!] No data returned from NSE.")


if __name__ == "__main__":
    test_fetch()
