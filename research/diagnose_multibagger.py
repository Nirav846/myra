from rich.console import Console
from myra_app.librarian import Librarian
from myra_app.engine import Engine
import pandas as pd

def diagnose_multibagger():
    console = Console()
    lib = Librarian(console=console)
    engine = Engine(lib)
    
    # Check a few major stocks that should have data
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "BHEL", "HAL", "SBIN"]
    
    console.print(f"[bold yellow]Diagnosing Multibagger Strategy for {symbols}...[/bold yellow]")
    
    # We manually trigger the engine logic for these stocks
    results, _ = engine.run_scan(symbols, "multibagger_early", silent=True)
    
    # Now let's do a deep dive into ONE stock to see why it fails
    from myra_app.data_adapter import DataAdapter
    adapter = DataAdapter(librarian=lib)
    
    for sym in symbols:
        df = adapter.get_price_df(sym, lookback_days=756)
        funda = adapter.get_latest_funda(sym)
        
        from myra_app.strategies.multibagger_early import Strategy
        strat = Strategy()
        res = strat.run(df, funda)
        
        ltp = df['Close'].iloc[-1] if not df.empty else 0
        l1y = funda.get('low_1y', 0)
        deliv = funda.get('delivery_percent', 0)
        rdv = funda.get('RDV', 0)
        
        console.print(f"\n[bold]Stock: {sym}[/bold]")
        console.print(f"  LTP: {ltp}, 52W Low: {l1y} ({round(ltp/l1y, 2) if l1y > 0 else 'N/A'}x)")
        console.print(f"  Delivery: {deliv}%, RDV: {rdv}")
        console.print(f"  Result: {res.get('signal')}, Reason: {res.get('reason')}")

if __name__ == "__main__":
    diagnose_multibagger()
