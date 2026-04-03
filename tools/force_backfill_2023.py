import os
import sys
import pandas as pd
from datetime import date, timedelta
from myra_app.librarian import Librarian

def backfill():
    # Force Librarian to fetch 3 years of history for specific symbols
    lib = Librarian()
    print("--- [BACKFILL] Starting targeted sync for TRENT & TATAPOWER (2022-2023) ---")
    
    # We need to reach back to Jan 2022 to get enough context for Feb 2023
    start_date = date(2022, 1, 1)
    end_date = date(2024, 12, 31)
    
    # Use Librarian's internal fetch_archives for the range
    # Note: _fetch_archives expects a range of days
    days = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1) if (start_date + timedelta(days=x)).weekday() < 5]
    
    print(f"Total trading days to check: {len(days)}")
    
    # Instead of full sync, we use the specific fetcher to get archival data
    # This will populate the 'prices' table
    lib._fetch_range(days)
    
    print("--- [BACKFILL] Sync Complete. Verifying Data ---")
    res = lib.conn.execute("SELECT symbol, MIN(date), MAX(date), COUNT(*) FROM prices WHERE symbol IN ('TRENT', 'TATAPOWER') GROUP BY symbol").df()
    print(res)
    lib.close()

if __name__ == "__main__":
    backfill()
