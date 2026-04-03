
import os
import sys
from rich.console import Console

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian

def force_backfill():
    console = Console()
    console.print("[bold cyan][MYRA] Initializing Force Backfill (1.5 Years)...[/bold cyan]")
    
    lib = Librarian(console=console)
    
    try:
        # Sync symbols first
        console.print("[info][*] Updating index constituents...[/info]")
        lib.populate_index_constituents()
        
        # Sync 1.5 years of OHLCV + Delivery (history_years=1.5)
        console.print("[info][*] Fetching 1.5 years of history... This may take several minutes.[/info]")
        lib.sync_market_data(history_years=1.5, skip_maintenance=False)
        
        console.print("[success][✔] Force Backfill Complete![/success]")
        
        # Quick verify
        res = lib.conn.execute("SELECT symbol, COUNT(*) as cnt FROM prices WHERE symbol IN ('SUNPHARMA', 'RELIANCE') GROUP BY symbol").fetchall()
        for r in res:
            console.print(f"[bold yellow][!] {r[0]}: {r[1]} rows[/bold yellow]")
            
    except Exception as e:
        console.print(f"[error][!] Backfill Failed: {e}[/error]")
    finally:
        lib.conn.close()

if __name__ == "__main__":
    force_backfill()
