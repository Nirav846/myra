
import pandas as pd
import duckdb
import os
import pandas_ta as ta
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.linear_model import LogisticRegression

def debug_whale_internal():
    conn = duckdb.connect('results/Data/myra_market_data.db')
    symbol = 'SUNPHARMA'
    print(f"Deep Debugging Whale Tracker for {symbol}...")
    
    # Load prices
    df = conn.execute(f"SELECT * FROM prices WHERE symbol = '{symbol}' ORDER BY date ASC").df()
    df['date'] = pd.to_datetime(df['date'])
    # Check if delivery_percent exists
    print(f"Columns: {df.columns.tolist()}")
    
    df.set_index('date', inplace=True)
    df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'}, inplace=True)
    
    funda = {"symbol": symbol, "Market_Regime": 1, "ROE": 15}
    
    if len(df) < 250: 
        print(f"Data too short: {len(df)}")
        return
    
    # Feature Engineering
    c = df["Close"]; v = df["Volume"]
    df["RSI"] = ta.rsi(c, length=14)
    df["ATR"] = ta.atr(df["High"], df["Low"], c, length=20)
    df["Force"] = (c.diff() * v) / df["ATR"]
    df["Ret_1d"] = c.pct_change(1); df["Ret_3d"] = c.pct_change(3)
    df["RSI_Lag3"] = df["RSI"].shift(3); df["Force_Lag3"] = df["Force"].shift(3)
    df["Tightness"] = ta.atr(df["High"], df["Low"], c, length=5) / df["ATR"]
    df["Vol_Dry"] = v / v.rolling(20).mean()
    df["Regime"] = funda.get("Market_Regime", 1)
    
    # Use delivery_percent if available, else use a placeholder
    if 'delivery_percent' in df.columns:
        del_mean = df["delivery_percent"].rolling(50).mean(); del_std = df["delivery_percent"].rolling(50).std()
        df["Del_Clump"] = (df["delivery_percent"] > (del_mean + 1.0 * del_std)).rolling(10).sum()
    else:
        print("Warning: delivery_percent missing from DB!")
        df["Del_Clump"] = 0
        
    features = ["RSI", "Ret_1d", "Ret_3d", "RSI_Lag3", "Force", "Force_Lag3", "Tightness", "Vol_Dry", "Regime", "Del_Clump"]
    
    # Target labeling
    future_max = df["High"].shift(-10).rolling(10).max()
    df["Target"] = (future_max > (c + 1.5 * df["ATR"])).astype(int)
    
    ml_df = df.dropna(subset=features + ["Target"]).copy()
    print(f"ML dataset size: {len(ml_df)}")
    if len(ml_df) < 150:
        print("ML dataset too small (<150)")
        return
        
    pos_targets = len(ml_df[ml_df["Target"] == 1])
    print(f"Positive targets: {pos_targets}")
    if pos_targets < 5:
        print("Not enough positive targets for training (<5)")
        # return # Let's see if we can still train for debug purposes
        
    X = ml_df[features]; y = ml_df["Target"]
    scaler = StandardScaler(); X_scaled = scaler.fit_transform(X)
    
    m1 = XGBClassifier(n_estimators=30, max_depth=3, learning_rate=0.1, verbosity=0, random_state=42)
    m2 = RandomForestClassifier(n_estimators=30, max_depth=5, random_state=42)
    m3 = LogisticRegression(class_weight='balanced', random_state=42)
    committee = VotingClassifier(estimators=[('xgb', m1), ('rf', m2), ('lr', m3)], voting='soft')
    
    # Train
    committee.fit(X_scaled[:-10], y[:-10])
    
    # Latest Inference
    latest_raw = df[features].iloc[[-1]].fillna(0); latest_scaled = scaler.transform(latest_raw)
    prob = committee.predict_proba(latest_scaled)[0][1]
    
    ma50 = ta.sma(c, length=50).iloc[-1]
    curr_c = c.iloc[-1]
    
    print(f"Latest Prob: {prob:.4f}")
    print(f"Close: {curr_c}, MA50: {ma50}")
    print(f"Signal Conditions: Prob > 0.60 ({prob > 0.60}) AND Close > MA50 ({curr_c > ma50})")

if __name__ == "__main__":
    debug_whale_internal()
