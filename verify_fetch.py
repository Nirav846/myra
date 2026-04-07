import sys
import logging

sys.path.insert(0, ".")
from myra_app.fetcher import DataFetcher

logging.basicConfig(level=logging.INFO)

f = DataFetcher()
print("Verifying fetch_insider_trades...")
trades = f.fetch_insider_trades(days=30)
print(f"Total trades fetched: {len(trades)}")
if trades:
    print("Sample data:", trades[0])

print("\nVerifying fetch_bhavcopy_with_retry...")
from datetime import datetime, timedelta

dt = datetime.now() - timedelta(days=2)
while dt.weekday() >= 5:
    dt -= timedelta(days=1)
data, source = f.fetch_bhavcopy_with_retry(dt)
if data:
    print(f"Bhavcopy fetched successfully from {source}")
    print(f"Data length (chars): {len(data)}")
else:
    print("Failed to fetch bhavcopy")
