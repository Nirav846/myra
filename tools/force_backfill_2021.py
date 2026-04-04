import sys
from datetime import date, timedelta
from myra_app.librarian import Librarian

def backfill_2021():
    lib = Librarian()
    print("--- [DATABASE EXPANSION] Starting Backfill from 2021-01-01 ---")
    
    start_date = date(2021, 1, 1)
    end_date = date.today()
    
    # Generate all potential trading days (Monday-Friday)
    total_days = (end_date - start_date).days
    all_days = [start_date + timedelta(days=x) for x in range(total_days + 1)]
    trading_days = [d for d in all_days if d.weekday() < 5]
    
    # Check what we already have to avoid redundant downloads
    existing_dates = set()
    try:
        res = lib.conn.execute("SELECT DISTINCT date FROM prices WHERE date >= '2021-01-01'").fetchall()
        existing_dates = {r[0] for r in res}
        print(f"[*] Found {len(existing_dates)} days already in database.")
    except Exception as e:
        print(f"[!] Error checking existing dates: {e}")
    
    target_days = [d for d in trading_days if d not in existing_dates]
    target_days.sort() # Process chronologically
    
    if not target_days:
        print("[!] No new days to backfill. Database is already up to date from 2021.")
        lib.close()
        return

    print(f"[*] Target days to fetch: {len(target_days)}")
    
    # Process in yearly chunks to avoid massive memory pressure
    # and to provide incremental progress updates.
    current_year = 0
    batch = []
    
    for d in target_days:
        if d.year != current_year:
            if batch:
                print(f"[*] Processing Batch for Year {current_year} ({len(batch)} days)...")
                lib._fetch_range(batch)
                batch = []
            current_year = d.year
        batch.append(d)
        
    # Final batch
    if batch:
        print(f"[*] Processing Final Batch for Year {current_year} ({len(batch)} days)...")
        lib._fetch_range(batch)

    print("\n--- [BACKFILL COMPLETE] Synchronizing Indicators ---")
    lib.update_indicator_history()
    
    # Final Verification
    stats = lib.conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM prices").fetchone()
    print(f"Database Range: {stats[0]} to {stats[1]}")
    print(f"Total Rows: {stats[2]}")
    
    lib.close()

if __name__ == "__main__":
    try:
        backfill_2021()
    except KeyboardInterrupt:
        print("\n[!] Backfill interrupted by user.")
        sys.exit(0)
