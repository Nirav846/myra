import os
import sys

# 2. Implementation: The Absolute Path Anchor
# Anchor to project root regardless of where the script is called from
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from myra_app.fundamental_manager import FundamentalManager
from myra_app.librarian import Librarian
from myra_app.fetcher import DataFetcher
from rich.console import Console
from tqdm import tqdm

def run_2026_ingest():
    console = Console()
    console.print("[bold cyan]--- [FUNDAMENTAL INGESTOR] Starting 2026 Optimization ---[/bold cyan]")
    
    lib = Librarian(console=console)
    fetcher = DataFetcher()
    fm = FundamentalManager(fetcher=fetcher)
    
    # Only target the 680 active stocks to save CPU/Bandwidth
    active_universe = lib.get_active_universe()
    console.print(f"[*] Active Universe: {len(active_universe)} symbols.")
    
    # Optional: Force reset if requested via arg or just rely on is_stale
    # For a "force refresh", we might want to reset the markers first
    if "--reset" in sys.argv:
        console.print("[warning][!] Resetting sync markers for all active stocks...[/warning]")
        conn = lib._meta_conn
        if conn:
            conn.execute("UPDATE symbols_master SET last_fundamental_update = '1900-01-01'")
            conn.commit()
        else:
            console.print("[red][!] Could not connect to meta.db via Librarian.[/red]")

    stale_stocks = [s for s in active_universe if fm.is_stale(s)]
    console.print(f"[*] Found {len(stale_stocks)} stale stocks needing update.")
    
    if not stale_stocks:
        console.print("[success][✔] All stocks are current. No update needed.[/success]")
        return

    for symbol in tqdm(stale_stocks, desc="Ingesting 2026 Fundamentals"):
        try:
            success = fm.fetch_fundamentals(symbol)
            if success:
                # console.print(f"[dim][+] Updated {symbol}[/dim]")
                pass
            else:
                # console.print(f"[red][!] Failed {symbol}[/red]")
                pass
        except Exception as e:
            console.print(f"[error][!] Error updating {symbol}: {e}[/error]")
            
    console.print("\n[success][✔] Fundamental Ingestion Complete.[/success]")
    console.print("[info][*] DCAL/ONGC should now show as Current.[/info]")

if __name__ == "__main__":
    run_2026_ingest()
