import os
import duckdb
import pandas as pd
import numpy as np
from datetime import date


def run_2023_test(symbol, move_start_date):
    db_path = os.path.join(os.getcwd(), "results", "Data", "myra_market_data.db")
    conn = duckdb.connect(db_path, read_only=True)

    print(f"\n--- [SANDBOX 2023] Analyzing {symbol} before {move_start_date} ---")

    # Extract data from 6 months before the move to 1 month after
    # Performance Guard Compliant (Fix 14, 15)
    start_lookback = (
        (pd.to_datetime(move_start_date) - pd.DateOffset(months=6)).date().isoformat()
    )
    end_lookback = (
        (pd.to_datetime(move_start_date) + pd.DateOffset(months=1)).date().isoformat()
    )

    query = f"""
        SELECT date, close, volume, delivery_qty, delivery_percent
        FROM prices
        WHERE symbol = '{symbol}' AND date BETWEEN '{start_lookback}' AND '{end_lookback}'
        ORDER BY date ASC
    """
    df = conn.execute(query).df()
    conn.close()

    if df.empty:
        print(f"Error: No data found for {symbol} in the requested 2023 range.")
        return

    # 1. Logic: Absorption + Multi-Dilation Confluence
    df["returns"] = np.log(df["close"] / df["close"].shift(1))
    df["deliv_norm"] = df["delivery_qty"] / df["delivery_qty"].rolling(20).mean()
    df["local_std"] = df["returns"].rolling(10).std()
    df["absorption"] = df["deliv_norm"] / df["local_std"].replace(0, np.nan)

    def dilated_trend(series, dilation, window=5):
        # Fix 37: Avoid .apply() on rolling windows
        vals = series.values
        full_window = window * dilation
        result = np.zeros(len(vals))
        for i in range(full_window - 1, len(vals)):
            chunk = vals[i - full_window + 1 : i + 1]
            sampled = chunk[::-dilation]
            if len(sampled) > 0:
                result[i] = np.mean(sampled)
        return pd.Series(result, index=series.index)

    df["cnn_2"] = dilated_trend(df["returns"], 2)
    df["cnn_4"] = dilated_trend(df["returns"], 4)
    df["cnn_8"] = dilated_trend(df["returns"], 8)
    df["confluence"] = df["cnn_2"] + df["cnn_4"] + df["cnn_8"]

    # REFINED SCORE
    df["accumulation_score"] = df["absorption"] * (df["delivery_percent"] / 100.0)
    df.loc[df["confluence"] < 0, "accumulation_score"] *= 0.1  # Penalize downtrends

    # 2. Results
    # Look at the last 30 days leading up to the move_start_date
    df["date"] = pd.to_datetime(df["date"])
    pre_move = df[df["date"] <= pd.to_datetime(move_start_date)].tail(30)

    print(f"--- [ANALYSIS] High-Score Days in the Accumulation Zone for {symbol} ---")
    high_scores = pre_move[
        pre_move["accumulation_score"] > pre_move["accumulation_score"].median() * 2
    ]
    if not high_scores.empty:
        print(
            high_scores[
                ["date", "close", "accumulation_score", "confluence"]
            ].to_string(index=False)
        )
    else:
        print("No significant accumulation signals detected with current thresholds.")

    # Check for the breakout
    post_move = df[df["date"] > pd.to_datetime(move_start_date)].head(20)
    if not post_move.empty:
        # Fix 63: Avoid chained indexing
        base_df = df[df["date"] <= pd.to_datetime(move_start_date)]
        last_close = base_df["close"].iloc[-1]
        total_move = (post_move["close"].iloc[-1] / last_close - 1) * 100
        print(f"\nMove performance 20 days after {move_start_date}: {total_move:.2f}%")


if __name__ == "__main__":
    # TRENT move started roughly Feb 2023
    run_2023_test("TRENT", "2023-02-01")
    # TATAPOWER move started roughly Dec 2023
    run_2023_test("TATAPOWER", "2023-12-01")
