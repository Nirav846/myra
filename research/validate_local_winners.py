import os
import duckdb
import pandas as pd
import numpy as np

def run_local_validation(symbol, target_breakout_date):
    db_path = os.path.join(os.getcwd(), 'results', 'Data', 'myra_market_data.db')
    conn = duckdb.connect(db_path, read_only=True)
    
    print(f"\n--- [SANDBOX VALIDATION] Analyzing {symbol} before {target_breakout_date} ---")
    
    # Analyze 100 days before the breakout to 20 days after
    start_date = (pd.to_datetime(target_breakout_date) - pd.DateOffset(days=100)).strftime('%Y-%m-%d')
    end_date = (pd.to_datetime(target_breakout_date) + pd.DateOffset(days=20)).strftime('%Y-%m-%d')
    
    query = f"""
        SELECT date, close, volume, delivery_qty, delivery_percent
        FROM prices
        WHERE symbol = '{symbol}' AND date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY date ASC
    """
    df = conn.execute(query).df()
    conn.close()

    if df.empty:
        print(f"No data for {symbol} in the requested range.")
        return

    # Advanced Confluence Logic
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
    
    df['accumulation_score'] = df['absorption'] * (df['delivery_percent'] / 100.0)
    df.loc[df['confluence'] < 0, 'accumulation_score'] *= 0.1 # Penalize downtrends

    # Check for breakout window
    df['date'] = pd.to_datetime(df['date'])
    pre_move = df[df['date'] <= pd.to_datetime(target_breakout_date)].tail(40)
    
    print(f"--- [ANALYSIS] Accumulation Pattern for {symbol} ---")
    # Identify the 'Sweet Spot' - High Score + High Confluence before takeoff
    sweet_spot = pre_move[pre_move['accumulation_score'] > pre_move['accumulation_score'].median() * 2.5]
    if not sweet_spot.empty:
        print(sweet_spot[['date', 'close', 'accumulation_score', 'confluence']].to_string(index=False))
    else:
        print("Model missed the specific sweet spot. May need threshold adjustment.")

    post_move = df[df['date'] > pd.to_datetime(target_breakout_date)].head(20)
    if not post_move.empty:
        expansion = (post_move['close'].max() / df[df['date'] <= pd.to_datetime(target_breakout_date)]['close'].iloc[-1] - 1) * 100
        print(f"\nBreakout Expansion (Max % in 20D): {expansion:.2f}%")

if __name__ == "__main__":
    # IRFC major breakout started around late Nov / early Dec 2023
    run_local_validation('IRFC', '2023-11-20')
    # RVNL major breakout around same time
    run_local_validation('RVNL', '2024-01-01')
