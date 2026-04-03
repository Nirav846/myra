import duckdb
import os
from rich.console import Console
from rich.table import Table

console = Console()

def tune_database():
    db_path = os.path.join(os.getcwd(), "results", "Data", "myra_market_data.db")
    if not os.path.exists(db_path):
        console.print("[error][!] Database not found. Run sync first.[/error]")
        return

    conn = duckdb.connect(db_path)
    console.print("[bold cyan]--- [TUNER] DuckDB Turbo-SQL Optimization ---[/bold cyan]")

    # 1. Covering Index for Prices (OHLCV + Delivery)
    # This speeds up get_ohlcv and get_delivery_data
    console.print("\n[*] Auditing Prices Index...")
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_covering ON prices (symbol, date, close, high, low, open, volume, delivery_qty)")
        console.print("[success][✔] Covering index created for 'prices' table.[/success]")
    except Exception as e:
        console.print(f"[error][!] Failed to create prices index: {e}[/error]")

    # 2. Covering Index for Indicators
    # Speeds up precompute_indicators and global scans
    console.print("\n[*] Auditing Indicators Index...")
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_covering ON calculated_indicators (date, symbol, sma50, close)")
        console.print("[success][✔] Covering index created for 'calculated_indicators' table.[/success]")
    except Exception as e:
        console.print(f"[error][!] Failed to create indicators index: {e}[/error]")

    # 3. Analyze Heavy Query: Indicator Computation
    console.print("\n[*] Analyzing Indicator Computation Plan...")
    # (Just an example of checking the plan)
    try:
        plan = conn.execute("EXPLAIN SELECT symbol, date, close FROM prices WHERE date >= CURRENT_DATE - INTERVAL 756 DAY").fetchone()[1]
        if "SCAN" in plan:
            console.print("[info][*] Query plan confirmed using efficient Columnar Scan.[/info]")
    except Exception: pass

    # 4. Final Vacuum & Statistics
    console.print("\n[*] Performing final maintenance...")
    conn.execute("ANALYZE")
    conn.execute("VACUUM")
    
    db_size = os.path.getsize(db_path) / (1024 * 1024)
    console.print(f"[success][✔] Optimization complete. DB Size: {round(db_size, 1)} MB[/success]")
    
    conn.close()

if __name__ == "__main__": tune_database()
