import os
import sys
import argparse
from datetime import date, timedelta
from rich.console import Console

# Fix path to allow importing from root
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from myra_app.librarian import Librarian

def backfill_year(year: int):
    console = Console()
    lib = Librarian(console=console)
    console.print(f"[bold cyan]--- [DATABASE EXPANSION] Target: Missing {year} Data ---[/bold cyan]")

    start_date = date(year, 1, 1)

    # If the requested year is the current year, bound end_date by today
    if year == date.today().year:
        end_date = date.today()
    else:
        end_date = date(year, 12, 31)

    # Generate all potential trading days (Monday-Friday)
    total_days = (end_date - start_date).days
    all_days = [start_date + timedelta(days=x) for x in range(total_days + 1)]
    trading_days = [d for d in all_days if d.weekday() < 5]

    # Check what we already have to avoid redundant downloads
    existing_dates = set()
    try:
        # Use simple date strings
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        res = lib.conn.execute(
            f"SELECT DISTINCT date FROM prices WHERE date >= '{start_str}' AND date <= '{end_str}'"
        ).fetchall()
        existing_dates = {r[0] for r in res}
        console.print(f"[info][*] Found {len(existing_dates)} days for {year} already in database.[/info]")
    except Exception:
        pass

    # Ensure existing dates match the date objects formatting or parse them
    # the existing_dates from sqlite usually return strings like "2021-01-01"
    # convert target days to string to check in existing_dates
    target_days = []
    for d in trading_days:
        if d.strftime("%Y-%m-%d") not in existing_dates:
            target_days.append(d)

    target_days.sort()

    if not target_days:
        console.print(f"[success][✔] No missing days in {year}. Database is already complete for {year}.[/success]")
        lib.close()
        return

    console.print(f"[info][*] Target days to fetch for {year}: {len(target_days)}[/info]")

    try:
        # Process the target days using Librarian's internal fetch mechanism
        lib._fetch_range(target_days)

        console.print(f"\n[success][✔] {year} BACKFILL COMPLETE. Synchronizing Indicators...[/success]")
        lib.update_indicator_history()

        # Final Verification
        stats = lib.conn.execute(
            f"SELECT COUNT(DISTINCT date) FROM prices WHERE date >= '{start_str}' AND date <= '{end_str}'"
        ).fetchone()
        console.print(f"[bold yellow][!] {year} Data: {stats[0]} trading days now in database.[/bold yellow]")

    except Exception as e:
        console.print(f"[error][!] Backfill Failed: {e}[/error]")
    finally:
        lib.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing historical data for a specific year.")
    parser.add_argument("year", type=int, help="The year to backfill (e.g., 2021)")
    args = parser.parse_args()

    try:
        backfill_year(args.year)
    except KeyboardInterrupt:
        print("\n[!] Backfill interrupted by user.")
        sys.exit(0)
