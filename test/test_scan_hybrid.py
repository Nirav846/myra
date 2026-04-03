from rich.console import Console
from myra_app.screener import MYRAScreener
import pandas as pd

def test_hybrid_scan():
    console = Console()
    screener = MYRAScreener(console)
    
    # RELIANCE, TCS, HDFCBANK are in our technical.db (SQLite)
    # Others are in DuckDB
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN"]
    
    console.print(f"[bold blue]Testing Hybrid Scan for: {symbols}[/bold blue]")
    
    # 115: Institutional Buying (Primitive Scanner)
    results = screener.execute_scan("115", "Institutional Buying Test", portfolio_symbols=symbols)
    
    if results:
        console.print(f"[bold green]Success: Found {len(results)} candidates.[/bold green]")
        for r in results:
            console.print(f"Stock: {r['Stock']}, LTP: {r['LTP']}, Stars: {r['Stars']}")
    else:
        console.print("[yellow]Scan completed, but 0 candidates found (Logic working, but criteria not met).[/yellow]")

if __name__ == "__main__":
    test_hybrid_scan()
