from rich.console import Console
from myra_app.screener import MYRAScreener
import pandas as pd

def test_all_scanners():
    console = Console()
    screener = MYRAScreener(console)
    
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN", "BHEL"]
    
    scanners = [
        ("126", "SMC-1 Institutional Flow"),
        ("multibagger_early", "Multibagger Early Detection"),
        ("109", "Weekly Breakout"),
        ("115", "Institutional Buying")
    ]
    
    for sid, name in scanners:
        console.print(f"\n[bold magenta]>>> Testing Scanner: {name} ({sid})[/bold magenta]")
        results = screener.execute_scan(sid, name, portfolio_symbols=symbols)
        
        if results:
            console.print(f"[bold green]PASS: Found {len(results)} matches.[/bold green]")
            for r in results:
                console.print(f"  - {r['Stock']}: LTP {r['LTP']}, Stars {r['Stars']}, Grade {r['Grade']}")
        else:
            console.print(f"[yellow]OK: Scan finished, no current signals for {symbols}.[/yellow]")

if __name__ == "__main__":
    test_all_scanners()
