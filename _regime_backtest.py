import sys, sqlite3, os, random, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\01screener\Myra')
from myra_app.constants import DB_DIR
import pandas as pd
import numpy as np

random.seed(42)
SAMPLE_SIZE = 800
DCB_WINDOW = 120
NUM_SCAN_DATES = 30
WARMUP_DAYS = 300
COST_BPS = 50
STCG_RATE = 0.125

print("Loading data...")

# ------------------------------------------------------------
# 1. Load universe and history
# ------------------------------------------------------------
tech_conn = sqlite3.connect(os.path.join(DB_DIR, 'myra_technical.db'))
val_conn = sqlite3.connect(os.path.join(DB_DIR, 'myra_valuation.db'))
meta_conn = sqlite3.connect(os.path.join(DB_DIR, 'myra_metadata.db'))

universe_all = [r[0] for r in val_conn.execute(
    "SELECT symbol FROM fundamentals WHERE COALESCE(market_cap,0) BETWEEN 200e7 AND 50000e7"
).fetchall()]

sample_syms = random.sample(universe_all, min(SAMPLE_SIZE, len(universe_all)))
history = {}
print(f"Loading daily history for {len(sample_syms)} symbols...")
for sym in sample_syms:
    df = pd.read_sql("""
        SELECT date, open, high, low, close, volume, delivery, delivery_pct
        FROM technical_data WHERE symbol=? ORDER BY date
    """, tech_conn, params=(sym,))
    if len(df) >= WARMUP_DAYS + 500:
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        history[sym] = df
tech_conn.close()

# Load Nifty data (benchmarks table)
nifty_df = pd.read_sql("SELECT date, close FROM benchmarks WHERE symbol = '^NSEI' ORDER BY date", meta_conn)
nifty_df['date'] = pd.to_datetime(nifty_df['date'])
nifty_df = nifty_df.set_index('date')
meta_conn.close()
print(f"Loaded {len(history)} symbols\n")

# Compute Nifty 200-day SMA
nifty_df['sma200'] = nifty_df['close'].rolling(200).mean()

# ------------------------------------------------------------
# 2. Build DCB signals (same as before, no Nifty features)
# ------------------------------------------------------------
all_signals = []
for scan_date in scan_dates:
    # Determine market regime for this date
    nifty_row = nifty_df.loc[scan_date] if scan_date in nifty_df.index else None
    if nifty_row is None or pd.isna(nifty_row['sma200']):
        continue
    above_sma = nifty_row['close'] > nifty_row['sma200']
    
    for sym, df in history.items():
        if scan_date not in df.index: continue
        idx = df.index.get_loc(scan_date)
        df_slice = df.iloc[max(0, idx - WARMUP_DAYS):idx+1]
        if len(df_slice) < DCB_WINDOW: continue

        # (same DCB logic as before, but we also record the regime label)
        last_n = df_slice.tail(DCB_WINDOW)
        avg_del = last_n['delivery_pct'].mean()
        if avg_del == 0: continue
        high_del = last_n[last_n['delivery_pct'] > avg_del]
        if len(high_del) < 10 or high_del['delivery'].sum() == 0: continue
        dcb = (high_del['close'] * high_del['delivery']).sum() / high_del['delivery'].sum()
        close = df_slice['close'].iloc[-1]
        if close >= dcb: continue
        discount_pct = (dcb - close) / dcb * 100
        if discount_pct < 10 or discount_pct > 60 or dcb > close * 5: continue

        adtv = (last_n['close'] * last_n['volume'] / 1e7).mean()
        if adtv < 0.5: continue

        recent = df_slice.tail(20)
        up = recent[recent['close'] > recent['open']]
        down = recent[recent['close'] < recent['open']]
        del_abs = None
        if len(up) > 0 and len(down) > 0:
            del_abs = up['delivery_pct'].mean() - down['delivery_pct'].mean()
        if del_abs is None or del_abs < -2.0: continue

        fwd = df.iloc[idx+1:]
        ret_60d = (fwd['close'].iloc[59] / close - 1) * 100 if len(fwd) >= 60 else None
        if ret_60d is None: continue
        cost_adj = COST_BPS / 100 * 2
        tax = STCG_RATE * max(0, ret_60d - cost_adj)
        net_60 = ret_60d - cost_adj - tax

        all_signals.append({
            'symbol': sym,
            'date': scan_date,
            'discount_pct': discount_pct,
            'del_abs': del_abs,
            'net_60d': net_60,
            'above_sma200': above_sma  # market regime label
        })

sig_df = pd.DataFrame(all_signals)
if sig_df.empty:
    print("No signals generated.")
    sys.exit(0)

print(f"Total DCB signals: {len(sig_df)}")
print(f"Signals when Nifty above 200-SMA: {sig_df['above_sma200'].sum()}")
print(f"Signals when Nifty below: {len(sig_df) - sig_df['above_sma200'].sum()}")

# ------------------------------------------------------------
# 3. Compare performance
# ------------------------------------------------------------
above = sig_df[sig_df['above_sma200']]
below = sig_df[~sig_df['above_sma200']]

if len(above) >= 5:
    print(f"\n--- Nifty ABOVE 200-SMA ---")
    print(f"  n = {len(above)}")
    print(f"  Avg net 60d: {above['net_60d'].mean():+.2f}%")
    print(f"  Win rate: {(above['net_60d'] > 0).mean()*100:.1f}%")
else:
    print("\nNot enough signals in ABOVE regime.")

if len(below) >= 5:
    print(f"\n--- Nifty BELOW 200-SMA ---")
    print(f"  n = {len(below)}")
    print(f"  Avg net 60d: {below['net_60d'].mean():+.2f}%")
    print(f"  Win rate: {(below['net_60d'] > 0).mean()*100:.1f}%")
else:
    print("\nNot enough signals in BELOW regime.")

# Combine both (overall, for reference)
print(f"\n--- Overall (all signals) ---")
print(f"  n = {len(sig_df)}")
print(f"  Avg net 60d: {sig_df['net_60d'].mean():+.2f}%")
print(f"  Win rate: {(sig_df['net_60d'] > 0).mean()*100:.1f}%")

# Determine if the filter would improve performance
if len(above) >= 5 and len(below) >= 5:
    diff = above['net_60d'].mean() - below['net_60d'].mean()
    if diff > 0:
        print(f"\n✅ Filtering to 'above' regime would improve returns by {diff:+.2f}%.")
    else:
        print(f"\n⚠️ Filtering to 'above' regime would LOWER returns by {-diff:+.2f}%.")
