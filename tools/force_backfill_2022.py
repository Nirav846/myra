import os
import sys
import pandas as pd
from datetime import date, timedelta, datetime
from myra_app.librarian import Librarian
from PKDevTools.classes.PKDateUtilities import PKDateUtilities
from rich.console import Console

def backfill_2022():
    console = Console()
    lib = Librarian(console=console)
    console.print("[bold cyan]--- [DATABASE EXPANSION] Target: Missing 2022 Data ---[/bold cyan]")
    
    start_date = date(2022, 1, 1)
    end_date = date(2022, 12, 31)
    
    # Generate all potential trading days (Monday-Friday)
    total_days = (end_date - start_date).days
    all_days = [start_date + timedelta(days=x) for x in range(total_days + 1)]
    trading_days = [d for d in all_days if d.weekday() < 5]
    
    # Check what we already have to avoid redundant downloads
    existing_dates = set()
    try:
        res = lib.conn.execute("SELECT DISTINCT date FROM prices WHERE date >= '2022-01-01' AND date <= '2022-12-31'").fetchall()
        existing_dates = {r[0] for r in res}
        console.print(f"[info][*] Found {len(existing_dates)} days for 2022 already in database.[/info]")
    except Exception as e:
        console.print(f"[warning][!] Error checking existing dates: {e}[/warning]")
    
    target_days = [d for d in trading_days if d not in existing_dates]
    target_days.sort() 
    
    if not target_days:
        console.print("[success][✔] No missing days in 2022. Database is already complete for 2022.[/success]")
        lib.close()
        return

    console.print(f"[info][*] Target days to fetch for 2022: {len(target_days)}[/info]")
    
    try:
        # Process the target days using Librarian's internal fetch mechanism
        lib._fetch_range(target_days)
        
        console.print("\n[success][✔] 2022 BACKFILL COMPLETE. Synchronizing Indicators...[/success]")
        lib.update_indicator_history()
        
        # Final Verification
        stats = lib.conn.execute("SELECT COUNT(DISTINCT date) FROM prices WHERE date >= '2022-01-01' AND date <= '2022-12-31'").fetchone()
        console.print(f"[bold yellow][!] 2022 Data: {stats[0]} trading days now in database.[/bold yellow]")
        
    except Exception as e:
        console.print(f"[error][!] Backfill Failed: {e}[/error]")
    finally:
        lib.close()

if __name__ == "__main__":
    try:
        backfill_2022()
    except KeyboardInterrupt:
        print("\n[!] Backfill interrupted by user.")
        sys.exit(0)
