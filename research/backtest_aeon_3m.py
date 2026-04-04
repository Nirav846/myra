import os
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from myra_app.screener import MYRAScreener
from rich.console import Console
from tqdm import tqdm

console = Console()

def run_backtest():
    # Use the standard Librarian from MYRAScreener
    # This avoids the "same database file with different configuration" error
    screener = MYRAScreener(console)
    lib = screener.lib
    conn = lib.conn
    
    # 1. Define Timeframe (Last 3 Months)
    end_date = datetime(2026, 3, 22)
    start_date = end_date - timedelta(days=90)
    
    # Get trading days from the prices table
    trading_days_df = conn.execute("SELECT DISTINCT date FROM prices WHERE date >= ? AND date <= ? ORDER BY date ASC", [start_date, end_date]).df()
    if trading_days_df.empty:
        print(f"[!] No trading days found between {start_date.date()} and {end_date.date()}")
        return
        
    trading_days = trading_days_df['date'].tolist()
    
    results = []
    print(f"[*] Starting 3-Month Backtest: {start_date.date()} to {end_date.date()}")
    print(f"[*] Database has {len(trading_days)} trading days in this period.")
    
    total_signals_found = 0
    
    # 2. Iterate through each trading day
    for day in tqdm(trading_days):
        # Convert Timestamp to string YYYY-MM-DD
        day_str = day.strftime('%Y-%m-%d')
        
        try:
            # Execute the AEON scan for this specific historical date
            # scan_all=True ensures we check the full universe
            hits = screener.execute_scan(
                "aeon_agent_signals", 
                "Backtest", 
                as_of_date=day_str, 
                scan_all=True,
                is_piped=True # Silent mode
            )
        except Exception:
            # console.print(f"[error]Error on {day_str}: {e}[/error]")
            hits = []
        
        if not hits: continue
        
        total_signals_found += len(hits)
        
        for h in hits:
            sym = h['Stock']
            # Accessing metrics directly from result dictionary
            smc_phase = h.get('SMC', '-')
            entry_price = h.get('LTP', 0)
            
            if entry_price == 0 or entry_price == '-' or pd.isna(entry_price): continue
            entry_price = float(entry_price)
            
            # 3. Forward-Walk: Check performance 10 days later
            # We query the prices table for future dates
            future_prices = conn.execute("""
                SELECT close FROM prices 
                WHERE symbol = ? AND date > ? 
                ORDER BY date ASC LIMIT 10
            """, [sym, day]).df()['close'].tolist()
            
            if not future_prices: continue
            
            max_future = max(future_prices)
            exit_price = future_prices[-1]
            gain = (exit_price - entry_price) / entry_price * 100
            max_gain = (max_future - entry_price) / entry_price * 100
            
            results.append({
                "Date": day_str,
                "Stock": sym,
                "SMC": smc_phase,
                "Entry": entry_price,
                "Exit_10d": exit_price,
                "Max_10d": max_future,
                "Return%": gain,
                "Peak%": max_gain,
                "Success": 1 if max_gain >= 3.0 else 0
            })

    # 4. Final Aggregation
    print(f"\n[*] Total signals detected: {total_signals_found}")
    print(f"[*] Valid signals (with future data): {len(results)}")

    if not results:
        print("[!] No valid signals with future data generated during the backtest period.")
        return

    df_res = pd.DataFrame(results)
    
    print("\n" + "="*50)
    print(" AEON 3-MONTH PERFORMANCE AUDIT (ML-1)")
    print("="*50)
    
    total_signals = len(df_res)
    ignition_only = df_res[df_res['SMC'].str.contains('Ignition', na=False)]
    basing_only = df_res[df_res['SMC'].str.contains('Basing', na=False)]
    
    def print_stats(df, label):
        if df.empty: 
            print(f"\n[{label}] No signals found.")
            return
        win_rate = df['Success'].mean() * 100
        avg_ret = df['Return%'].mean()
        avg_peak = df['Peak%'].mean()
        print(f"\n[{label}] Metrics ({len(df)} signals):")
        print(f"  Hit Rate (>3% Peak): {win_rate:.1f}%")
        print(f"  Avg 10-day Return:   {avg_ret:.2f}%")
        print(f"  Avg Peak Return:     {avg_peak:.2f}%")
        print(f"  Max Individual Gain: {df['Peak%'].max():.2f}%")

    print_stats(df_res, "GLOBAL")
    print_stats(ignition_only, "IGNITION PHASE")
    print_stats(basing_only, "BASING PHASE")
    
    # Save results to CSV for manual review
    report_file = f"myra_reports/AEON_Audit_{datetime.now().strftime('%d%m%Y_%H%M%S')}.csv"
    os.makedirs("myra_reports", exist_ok=True)
    df_res.to_csv(report_file, index=False)
    print(f"\n[✔] Detailed report saved to {report_file}")
    
    screener.close()

if __name__ == "__main__":
    try:
        run_backtest()
    except KeyboardInterrupt:
        print("\n[!] Backtest interrupted.")
        sys.exit(0)
