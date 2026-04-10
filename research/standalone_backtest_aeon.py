import os
import sys
import duckdb
import pandas as pd
import numpy as np
import joblib
from datetime import datetime, timedelta

# 2. Implementation: The Absolute Path Anchor
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from myra_app.ml_engine import EvolutionaryAgent, AEONEngine
from myra_core.utils.myra_log import myra_log


def run_standalone_backtest():
    db_path = os.path.join(BASE_DIR, "results", "Data", "myra_market_data.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(BASE_DIR, "myra.db")

    # 1. Direct Connection (Bypass Librarian)
    conn = duckdb.connect(db_path, read_only=True)

    # 2. Load AEON Agent
    agent = EvolutionaryAgent(input_size=80)
    model_path = os.path.join(BASE_DIR, "models", "aeon_agent.joblib")
    if os.path.exists(model_path):
        genes = joblib.load(model_path)
        agent.set_genes(genes)
    else:
        print(f"[!] No trained model found at {model_path}")
        return

    # 3. Define Timeframe
    end_date = datetime(2026, 3, 22)
    start_date = end_date - timedelta(days=90)

    # Get trading days
    trading_days_df = conn.execute(
        "SELECT DISTINCT date FROM prices WHERE date >= ? AND date <= ? ORDER BY date ASC",
        [start_date, end_date],
    ).df()
    trading_days = trading_days_df["date"].tolist()

    # Get all potential symbols (NIFTY 500)
    symbols = (
        conn.execute(
            "SELECT symbol FROM index_constituents WHERE index_name = 'NIFTY 500'"
        )
        .df()["symbol"]
        .tolist()
    )

    results = []
    print(
        f"[*] Standalone Backtest: {len(symbols)} stocks over {len(trading_days)} days..."
    )

    total_days = len(trading_days)
    for i, day in enumerate(trading_days, 1):
        myra_log(i, total_days, desc="AEON Backtest")
        # Performance Guard Compliant (Fix 51)
        day_str = day.date().isoformat() if hasattr(day, "date") else str(day)

        # Load snapshot of indicators for all symbols on this day
        # We need at least 10 days of history for each
        data_query = f"""
            SELECT * FROM calculated_indicators 
            WHERE date <= '{day_str}' 
            AND symbol IN ({repr(symbols)[1:-1]})
            ORDER BY date ASC
        """
        df_all = conn.execute(data_query).df()  # noqa: performance
        if df_all.empty:
            continue

        # Optimized with generator (Fix 89: Avoid .append in nested loop)
        def _get_sym_signals():
            for sym in symbols:
                df_sym = df_all[df_all["symbol"] == sym].tail(10)
                if len(df_sym) < 10:
                    continue

                # Feature extraction (Align with ml_engine.py)
                cols = [
                    "d_poc",
                    "absorp_ratio",
                    "std20",
                    "delivery_percent",
                    "sma50",
                    "sma200",
                    "rdv",
                    "close",
                ]
                state = df_sym[cols].values.flatten().reshape(1, -1)
                state = np.nan_to_num(state)

                action = agent.forward(state)

                if action > 0:  # Signal (Tactical, Core, or Conviction)
                    # Forward Walk: Check next 10 days
                    future = conn.execute(  # noqa: performance
                        f"""
                        SELECT close FROM prices 
                        WHERE symbol = '{sym}' AND date > '{day_str}' 
                        ORDER BY date ASC LIMIT 10
                    """,
                    ).df()

                    if future.empty:
                        continue

                    entry = df_sym["close"].iloc[-1]
                    peak = future["close"].max()
                    exit_p = future["close"].iloc[-1]

                    yield {
                        "Date": day_str,
                        "Stock": sym,
                        "Action": action,
                        "Entry": entry,
                        "Peak": peak,
                        "Return": (exit_p - entry) / entry * 100,
                        "Max_Gain": (peak - entry) / entry * 100,
                    }

        results.extend(list(_get_sym_signals()))

    if not results:
        print("[!] No signals generated.")
        conn.close()
        return

    df_res = pd.DataFrame(results)
    print("\n" + "=" * 50)
    print(" AEON 3-MONTH STANDALONE AUDIT")
    print("=" * 50)

    win_rate = (df_res["Max_Gain"] >= 3.0).mean() * 100
    avg_gain = df_res["Return"].mean()

    print(f"Total Signals: {len(df_res)}")
    print(f"Hit Rate (>3% Peak): {win_rate:.1f}%")
    print(f"Avg 10-Day Return:   {avg_gain:.2f}%")
    print(f"Best Trade:          {df_res['Max_Gain'].max():.2f}%")

    conn.close()


if __name__ == "__main__":
    run_standalone_backtest()
