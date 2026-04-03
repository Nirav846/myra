
import os
import duckdb
import pandas as pd
import numpy as np
import joblib
from datetime import datetime, timedelta
from myra_app.ml_engine import EvolutionaryAgent
from tqdm import tqdm

def run_portfolio_backtest():
    db_path = "results/Data/myra_market_data.db"
    if not os.path.exists(db_path): db_path = "myra.db"
    
    # 1. Connect
    conn = duckdb.connect(db_path, read_only=True)
    
    # 2. Load AEON (60-day context)
    input_size = 480
    agent = EvolutionaryAgent(input_size=input_size)
    expected_size = input_size * 16 + 16 + 16 * 4 + 4
    if os.path.exists("models/aeon_agent.joblib"):
        genes = joblib.load("models/aeon_agent.joblib")
        if len(genes) == expected_size:
            agent.set_genes(genes)
        else:
            print(f"[!] Model mismatch. Expected {expected_size}, got {len(genes)}. Please retrain.")
            return
    else:
        print("[!] No model found.")
        return

    # 3. Define 4-Month Timeframe with 90-day warmup
    end_date = datetime(2026, 3, 22)
    start_date = end_date - timedelta(days=120)
    
    trading_days = conn.execute("SELECT DISTINCT date FROM prices WHERE date >= ? AND date <= ? ORDER BY date ASC", [start_date, end_date]).df()['date'].tolist()
    symbols = conn.execute("SELECT symbol FROM index_constituents WHERE index_name = 'NIFTY 500'").df()['symbol'].tolist()
    
    # 4. Portfolio State
    inventory = {} 
    history = []
    
    print(f"[*] Portfolio Backtest (60d Context): {len(symbols)} stocks over {len(trading_days)} days...")
    
    for day in tqdm(trading_days):
        day_str = day.strftime('%Y-%m-%d')
        
        # Load snapshot data
        data_query = f"""
            SELECT * FROM calculated_indicators 
            WHERE date <= '{day_str}' 
            AND symbol IN ({repr(symbols)[1:-1]})
            ORDER BY date ASC
        """
        df_all = conn.execute(data_query).df()
        
        for sym in symbols:
            df_sym = df_all[df_all['symbol'] == sym].tail(60)
            if len(df_sym) < 60: continue
            
            # Feature extraction
            cols = ['d_poc', 'absorp_ratio', 'std20', 'delivery_percent', 'sma50', 'sma200', 'rdv', 'close']
            state = df_sym[cols].values.flatten().reshape(1, -1)
            state = np.nan_to_num(state)
            
            # Inference with Sensitivity
            probs = agent.get_probs(state)[0]
            action = np.argmax(probs)
            if action == 0 and probs[0] < 0.55:
                best_buy = np.argmax(probs[1:]) + 1
                if probs[best_buy] > 0.30:
                    action = best_buy
            
            current_price = df_sym['close'].iloc[-1]
            
            # --- PORTFOLIO LOGIC ---
            if sym in inventory and action == 0:
                trade_data = inventory[sym]
                entry = trade_data["entry"]
                phase = trade_data["phase"]
                gain = (current_price - entry) / entry * 100
                history.append({
                    "Stock": sym, "Entry_Date": "Prior", "Exit_Date": day_str, 
                    "Entry": entry, "Exit": current_price, "Return%": gain, "Phase": phase
                })
                del inventory[sym]
            elif sym not in inventory and action > 0:
                phase_label = "Ignition" if action == 3 else "Basing"
                inventory[sym] = {"entry": current_price, "phase": phase_label}

    # 5. Summary
    if not history:
        print("[!] No completed trades.")
        return

    df_h = pd.DataFrame(history)
    
    def print_phase_stats(df, label):
        if df.empty: return
        print(f"\n[{label}] Metrics ({len(df)} trades):")
        print(f"  Win Rate:     {(df['Return%'] > 0).mean()*100:.1f}%")
        print(f"  Avg Profit:   {df['Return%'].mean():.2f}%")
        print(f"  Best Trade:   {df['Return%'].max():.2f}%")
        print(f"  Worst Trade:  {df['Return%'].min():.2f}%")

    print("\n" + "="*50)
    print(" AEON 4-MONTH PORTFOLIO AUDIT (60d Context)")
    print("="*50)
    print_phase_stats(df_h, "GLOBAL")
    print_phase_stats(df_h[df_h['Phase'] == 'Basing'], "BASING PHASE (1)")
    print_phase_stats(df_h[df_h['Phase'] == 'Ignition'], "IGNITION PHASE (2)")
    
    conn.close()

if __name__ == "__main__":
    run_portfolio_backtest()
