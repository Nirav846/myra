import os
import duckdb
import pandas as pd
import numpy as np
from datetime import date

def run_2023_test(symbol, move_start_date):
    db_path = os.path.join(os.getcwd(), 'results', 'Data', 'myra_market_data.db')
    conn = duckdb.connect(db_path, read_only=True)
    
    print(f"\n--- [SANDBOX 2023] Analyzing {symbol} before {move_start_date} ---")
    
    # Extract data from 6 months before the move to 1 month after
    start_lookback = (pd.to_datetime(move_start_date) - pd.DateOffset(months=6)).strftime('%Y-%m-%d')
    end_lookback = (pd.to_datetime(move_start_date) + pd.DateOffset(months=1)).strftime('%Y-%m-%d')
    
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
    df['returns'] = np.log(df['close'] / df['close'].shift(1))
    df['deliv_norm'] = df['delivery_qty'] / df['delivery_qty'].rolling(20).mean()
    df['local_std'] = df['returns'].rolling(10).std()
    df['absorption'] = df['deliv_norm'] / df['local_std'].replace(0, np.nan)

    def dilated_trend(series, dilation, window=5):
        return series.rolling(window * dilation).apply(lambda x: x[::dilation].mean() if len(x[::dilation]) > 0 else 0, raw=True)

    df['cnn_2'] = dilated_trend(df['returns'], 2)
    df['cnn_4'] = dilated_trend(df['returns'], 4)
    df['cnn_8'] = dilated_trend(df['returns'], 8)
    df['confluence'] = (df['cnn_2'] + df['cnn_4'] + df['cnn_8'])
    
    # REFINED SCORE
    df['accumulation_score'] = df['absorption'] * (df['delivery_percent'] / 100.0)
    df.loc[df['confluence'] < 0, 'accumulation_score'] *= 0.1 # Penalize downtrends

    # 2. Results
    # Look at the last 30 days leading up to the move_start_date
    df['date'] = pd.to_datetime(df['date'])
    pre_move = df[df['date'] <= pd.to_datetime(move_start_date)].tail(30)
    
    print(f"--- [ANALYSIS] High-Score Days in the Accumulation Zone for {symbol} ---")
    high_scores = pre_move[pre_move['accumulation_score'] > pre_move['accumulation_score'].median() * 2]
    if not high_scores.empty:
        print(high_scores[['date', 'close', 'accumulation_score', 'confluence']].to_string(index=False))
    else:
        print("No significant accumulation signals detected with current thresholds.")

    # Check for the breakout
    post_move = df[df['date'] > pd.to_datetime(move_start_date)].head(20)
    if not post_move.empty:
        total_move = (post_move['close'].iloc[-1] / df[df['date'] <= pd.to_datetime(move_start_date)]['close'].iloc[-1] - 1) * 100
        print(f"\nMove performance 20 days after {move_start_date}: {total_move:.2f}%")

if __name__ == "__main__":
    # TRENT move started roughly Feb 2023
    run_2023_test('TRENT', '2023-02-01')
    # TATAPOWER move started roughly Dec 2023
    run_2023_test('TATAPOWER', '2023-12-01')
