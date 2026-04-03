from myra_app.librarian import Librarian
from myra_app.fundamental_ranker import FundamentalRanker
from rich.console import Console

def materialize():
    console = Console()
    console.print("[bold blue]Starting Fundamental Score Materialization...[/bold blue]")
    
    lib = Librarian(console=console)
    # FundamentalRanker is already initialized in Librarian.connect()
    # But we can also use it directly
    ranker = lib.fundamental_ranker
    
    # Materialize all active universe symbols
    symbols = lib.get_active_universe()
    if not symbols:
        console.print("[yellow]No active universe found. Materializing all symbols...[/yellow]")
        symbols = None
    
    ranker.materialize_scores(symbols=symbols)
    console.print("[bold green]Materialization Complete.[/bold green]")

if __name__ == "__main__":
    materialize()
