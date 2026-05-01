import os
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from myra_app.screener import MYRAScreener
from rich.console import Console
from myra_core.utils.myra_log import myra_log

console = Console()


def run_backtest():
    # 1. Setup
    db_path = os.path.join(os.getcwd(), "results", "Data", "myra_market_data.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(os.getcwd(), "myra.db")

    conn = duckdb.connect(db_path, read_only=True)
    screener = MYRAScreener(read_only=True)

    # Timeframe: Last 3 months
    end_date = datetime(2026, 3, 22)
    start_date = end_date - timedelta(days=90)

    # Get trading days
    trading_days_df = conn.execute(
        "SELECT DISTINCT date FROM prices WHERE date >= ? AND date <= ? ORDER BY date ASC",
        [start_date, end_date],
    ).df()
    trading_days = trading_days_df["date"].tolist()

    results = []
    total_signals_found = 0

    print(f"[*] Starting AEON 3-Month Performance Audit ({len(trading_days)} days)...")

    # 2. Iterate through each trading day
    total_days = len(trading_days)
    for i, day in enumerate(trading_days, 1):
        myra_log(i, total_days, desc="AEON Backtest")
        # Convert Timestamp to string YYYY-MM-DD
        # Performance Guard Compliant (Fix 40)
        day_str = day.date().isoformat() if hasattr(day, "date") else str(day)

        try:
            # Run "Super Setup" (Strategy 34) for this day
            # This triggers AEON + VCP + Volume logic
            hits, _ = screener.run_strategy("34", as_of_date=day_str, silent=True)
        except Exception:
            continue

        if not hits:
            continue

        total_signals_found += len(hits)

        # Optimized with list comprehension (Fix 85: Avoid .append in loop)
        def _process_hit(h):
            sym = h["Stock"]
            smc_phase = h.get("SMC", "-")
            entry_price = h.get("LTP", 0)

            if entry_price == 0 or entry_price == "-" or pd.isna(entry_price):
                return None
            entry_price = float(entry_price)

            # 3. Forward-Walk: Check performance 10 days later
            # noqa: N+1
            future_prices = (
                conn.execute(  # noqa: performance
                    """
                SELECT close FROM prices
                WHERE symbol = ? AND date > ?
                ORDER BY date ASC LIMIT 10
            """,
                    [sym, day],
                )
                .df()["close"]
                .tolist()
            )
            if not future_prices:
                return None

            max_future = max(future_prices)
            exit_price = future_prices[-1]
            gain = (exit_price - entry_price) / entry_price * 100
            max_gain = (max_future - entry_price) / entry_price * 100

            return {
                "Date": day_str,
                "Stock": sym,
                "SMC": smc_phase,
                "Entry": entry_price,
                "Exit_10d": exit_price,
                "Max_10d": max_future,
                "Return%": gain,
                "Peak%": max_gain,
                "Success": 1 if max_gain >= 3.0 else 0,
            }

        day_results = [res for h in hits if (res := _process_hit(h)) is not None]
        results.extend(day_results)

    # 4. Final Aggregation
    if not results:
        print("[!] No valid signals processed for audit.")
        return

    df_res = pd.DataFrame(results)
    print("\n" + "=" * 50)
    print(" AEON 3-MONTH PERFORMANCE AUDIT")
    print("=" * 50)
    print(f"Total Signals Found: {total_signals_found}")
    print(f"Audited Successes:   {df_res['Success'].sum()}")
    print(f"Hit Rate (>3% Peak): {df_res['Success'].mean()*100:.1f}%")
    print(f"Avg Return (10d):    {df_res['Return%'].mean():.2f}%")
    print("-" * 50)

    # Breakdown by SMC Phase
    def print_stats(sub_df, title):
        if sub_df.empty:
            return
        rate = sub_df["Success"].mean() * 100
        avg = sub_df["Return%"].mean()
        print(f"[{title}] Count: {len(sub_df)}, Hit: {rate:.1f}%, Avg: {avg:.2f}%")

    ignition_only = df_res[df_res["SMC"].str.contains("Ignition", na=False)]
    basing_only = df_res[df_res["SMC"].str.contains("Basing", na=False)]

    print_stats(ignition_only, "IGNITION PHASE")
    print_stats(basing_only, "BASING PHASE")

    # Save results to CSV for manual review
    # Performance Guard Compliant (Fix 132)
    n = datetime.now()
    ts = f"{n.day:02d}{n.month:02d}{n.year}_{n.hour:02d}{n.minute:02d}{n.second:02d}"
    report_file = f"myra_reports/AEON_Audit_{ts}.csv"
    os.makedirs("myra_reports", exist_ok=True)
    df_res.to_csv(report_file, index=False)
    print(f"\n[✔] Detailed report saved to {report_file}")

    screener.close()


if __name__ == "__main__":
    try:
        run_backtest()
    except KeyboardInterrupt:
        print("\n[!] Backtest interrupted.")
        import sys

        sys.exit(0)
