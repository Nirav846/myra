import os
import sys
import pandas as pd
from rich.console import Console

# Fix path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

from myra_app.librarian import Librarian
from myra_app.data_adapter import DataAdapter
from myra_app.strategies.multibagger_early import Strategy

def trace_multibagger_v6():
    console = Console()
    console.print("[bold cyan][DEBUG] Tracing Multibagger v6 Logic...[/bold cyan]")
    
    lib = Librarian(read_only=True)
    adapter = DataAdapter(librarian=lib)
    strat = Strategy(librarian=lib)
    
    # Trace a few symbols: 1 Bluechip (likely fail), 1 Midcap, 1 Smallcap
    test_symbols = ["ONGC", "TCS", "IDEA", "SUZLON", "ZOMATO"]
    
    for sym in test_symbols:
        console.print(f"\n[bold yellow][*] TRACING: {sym}[/bold yellow]")
        
        # 1. Fetch Data
        df = adapter.get_price_df(sym, lookback_days=252)
        if df.empty:
            console.print(f"  [red]FAIL: No data retrieved for {sym}[/red]")
            continue
            
        funda = adapter.get_latest_funda(sym, df=df)
        
        # 2. Run Strategy with debug capture
        # I'll manually check the thresholds here to see where it fails
        
        # Basing Check
        base_window = df.iloc[-60:]
        base_max = base_window['Close'].max()
        base_min = base_window['Close'].min()
        base_range_pct = (base_max - base_min) / base_min
        console.print(f"  - 60D Base Range: {round(base_range_pct*100, 2)}% (Target: < 25%)")
        
        # Volatility Compression
        atr_short = (df['High'] - df['Low']).iloc[-5:].mean()
        atr_long = (df['High'] - df['Low']).iloc[-20:].mean()
        console.print(f"  - Short ATR: {round(atr_short, 2)} vs Long ATR: {round(atr_long, 2)} (Compressing: {atr_short < atr_long})")
        
        # Institutional
        rdv = funda.get('RDV', 0)
        console.print(f"  - RDV: {round(rdv, 2)} (Target: > 1.5 for high score)")
        
        # Execute run
        res = strat.run(df, funda)
        if res["signal"]:
            console.print(f"  [green]SUCCESS: Signal Generated! Score: {res['metrics']['Score']}[/green]")
        else:
            console.print(f"  [red]FAIL: {res['reason']} (Score: {res.get('score', 'N/A')})[/red]")

    lib.close()

if __name__ == "__main__":
    trace_multibagger_v6()
