import sys, sqlite3, os, random, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\01screener\Myra')
from myra_app.constants import DB_DIR
import pandas as pd, numpy as np

random.seed(42)
SAMPLE_SIZE = 500
DCB_WINDOW = 120
NUM_SCAN_DATES = 15
WARMUP_DAYS = 260

# ------------------------------------------------------------
# 1.  Load universe
# ------------------------------------------------------------
tech_conn = sqlite3.connect(os.path.join(DB_DIR, 'myra_technical.db'))
val_conn = sqlite3.connect(os.path.join(DB_DIR, 'myra_valuation.db'))
universe_all = [r[0] for r in val_conn.execute(
    "SELECT symbol FROM fundamentals WHERE COALESCE(market_cap,0) BETWEEN 200e7 AND 50000e7"
).fetchall()]
val_conn.close()

sample_syms = random.sample(universe_all, min(SAMPLE_SIZE, len(universe_all)))
history = {}
print(f"Loading daily history for {len(sample_syms)} symbols...")
for sym in sample_syms:
    df = pd.read_sql("""
        SELECT date, open, high, low, close, volume, delivery, delivery_pct
        FROM technical_data WHERE symbol=? ORDER BY date
    """, tech_conn, params=(sym,))
    if len(df) >= WARMUP_DAYS + 400:
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        history[sym] = df
tech_conn.close()
print(f"Loaded {len(history)} symbols\n")

all_dates = sorted(history[list(history.keys())[0]].index)
usable = all_dates[WARMUP_DAYS: len(all_dates) - 400 - 5]
step = max(30, len(usable) // NUM_SCAN_DATES)
scan_dates = usable[::step][:NUM_SCAN_DATES]
print(f"Scan dates: {len(scan_dates)} from {scan_dates[0].strftime('%Y-%m-%d')} to {scan_dates[-1].strftime('%Y-%m-%d')}")

# Train/test split by date
split_date = scan_dates[len(scan_dates)//2]
train_dates = [d for d in scan_dates if d < split_date]
test_dates  = [d for d in scan_dates if d >= split_date]
print(f"Train dates: {len(train_dates)}, Test dates: {len(test_dates)}\n")

# ------------------------------------------------------------
# 2.  Build DCB signals with spike data
# ------------------------------------------------------------
all_signals = []
for scan_date in scan_dates:
    for sym, df in history.items():
        if scan_date not in df.index: continue
        idx = df.index.get_loc(scan_date)
        df_slice = df.iloc[max(0, idx - WARMUP_DAYS):idx+1]
        if len(df_slice) < DCB_WINDOW: continue
        
        # --- DCB computation (same as production scanner) ---
        last_n = df_slice.tail(DCB_WINDOW)
        avg_del = last_n['delivery_pct'].mean()
        if avg_del == 0: continue
        high_del = last_n[last_n['delivery_pct'] > avg_del]
        if len(high_del) < 10 or high_del['delivery'].sum() == 0: continue
        dcb = (high_del['close'] * high_del['delivery']).sum() / high_del['delivery'].sum()
        close = last_n['close'].iloc[-1]
        if close >= dcb: continue
        discount_pct = (dcb - close) / dcb * 100
        if discount_pct < 15 or discount_pct > 60 or dcb > close * 5: continue
        
        adtv = (last_n['close'] * last_n['volume'] / 1e7).mean()
        if adtv < 1.0: continue
        
        # 20-day delivery absorption
        recent = df_slice.tail(20)
        up = recent[recent['close'] > recent['open']]
        down = recent[recent['close'] < recent['open']]
        del_abs = None
        if len(up) > 0 and len(down) > 0:
            del_abs = up['delivery_pct'].mean() - down['delivery_pct'].mean()
        if del_abs is None or del_abs < -2.0: continue
        
        # --- Spike metrics (raw values, not thresholded) ---
        avg_del_50d = last_n['delivery_pct'].rolling(50).mean().iloc[-1]
        current_del_pct = last_n['delivery_pct'].iloc[-1]
        del_ratio = current_del_pct / avg_del_50d if avg_del_50d > 0 else 1.0
        
        current_row = last_n.iloc[-1]
        close_loc = (current_row['close'] - current_row['low']) / (current_row['high'] - current_row['low']) if (current_row['high'] - current_row['low']) > 0 else 0.5
        
        fwd = df.iloc[idx+1:]
        ret_20d = (fwd['close'].iloc[19] / close - 1) * 100 if len(fwd) >= 20 else None
        ret_40d = (fwd['close'].iloc[39] / close - 1) * 100 if len(fwd) >= 40 else None
        ret_60d = (fwd['close'].iloc[59] / close - 1) * 100 if len(fwd) >= 60 else None
        
        if ret_60d is None: continue
        
        all_signals.append({
            'symbol': sym, 'date': scan_date, 'discount_pct': discount_pct,
            'del_abs': del_abs, 'del_ratio': del_ratio, 'close_loc': close_loc,
            'ret_20d': ret_20d, 'ret_40d': ret_40d, 'ret_60d': ret_60d,
        })

sig_df = pd.DataFrame(all_signals)
sig_df['is_train'] = sig_df['date'].isin(train_dates)
print(f"Total DCB signals: {len(sig_df)}")
print(f"Train: {sig_df['is_train'].sum()}, Test: {(~sig_df['is_train']).sum()}\n")

# ------------------------------------------------------------
# 3.  Grid search over spike thresholds
# ------------------------------------------------------------
DEL_RATIOS = [1.2, 1.3, 1.5, 1.7, 2.0]
CLOSE_LOCS = [0.4, 0.5, 0.6, 0.7, 0.8]
DELTA_MIN  = [0, 5]  # 0 = no delta filter, 5 = del_abs >= 5%

results = []

for dr in DEL_RATIOS:
    for cl in CLOSE_LOCS:
        for dm in DELTA_MIN:
            # Apply spike filter
            spike_mask = (sig_df['del_ratio'] >= dr) & (sig_df['close_loc'] >= cl)
            if dm > 0:
                spike_mask = spike_mask & (sig_df['del_abs'] >= dm)
            
            train_set = sig_df[sig_df['is_train'] & spike_mask]
            test_set  = sig_df[~sig_df['is_train'] & spike_mask]
            
            if len(train_set) < 5:
                continue
            
            train_60d = train_set['ret_60d'].mean()
            train_win = (train_set['ret_60d'] > 0).mean() * 100
            train_n = len(train_set)
            
            test_60d = test_set['ret_60d'].mean() if len(test_set) >= 3 else None
            test_win = (test_set['ret_60d'] > 0).mean() * 100 if len(test_set) >= 3 else None
            test_n = len(test_set)
            
            # Baseline: all DCB signals (no spike filter)
            all_train = sig_df[sig_df['is_train']]
            baseline_60d = all_train['ret_60d'].mean()
            lift_vs_baseline = train_60d - baseline_60d
            
            results.append({
                'del_ratio': dr, 'close_loc': cl, 'del_abs_min': dm,
                'train_n': train_n, 'train_60d': train_60d, 'train_win': train_win,
                'test_n': test_n, 'test_60d': test_60d, 'test_win': test_win,
                'lift': lift_vs_baseline,
            })

res_df = pd.DataFrame(results).sort_values('train_60d', ascending=False)

# ------------------------------------------------------------
# 4.  Print results
# ------------------------------------------------------------
print("=== Spike‑Deep Threshold Grid Search (sorted by train 60d return) ===\n")
print(f"{'Ratio≥':>7} {'CL≥':>5} {'DelAbs≥':>7} {'TrN':>4} {'Tr60d':>7} {'TrWin':>6} {'TeN':>4} {'Te60d':>7} {'TeWin':>6} {'Lift':>7}")
print('-' * 80)

for _, r in res_df.head(20).iterrows():
    t60 = f"{r['test_60d']:>+6.1f}%" if r['test_60d'] is not None else '   N/A'
    tw  = f"{r['test_win']:>5.0f}%" if r['test_win'] is not None else '  N/A'
    print(f"{r['del_ratio']:>7.1f} {r['close_loc']:>5.2f} {r['del_abs_min']:>7} {r['train_n']:>4} "
          f"{r['train_60d']:>+6.1f}% {r['train_win']:>5.0f}% {r['test_n']:>4} {t60:>7} {tw:>6} {r['lift']:>+6.1f}%")

# ------------------------------------------------------------
# 5.  Best combination that generalises
# ------------------------------------------------------------
print(f"\n=== Best combination that generalises to test set ===\n")
generalised = res_df[(res_df['test_60d'].notna()) & (res_df['test_n'] >= 3)].copy()
if len(generalised) > 0:
    generalised['test_60d_abs'] = generalised['test_60d'].abs()
    generalised = generalised.sort_values(['test_60d', 'train_60d'], ascending=[False, False])
    best = generalised.iloc[0]
    print(f"  del_ratio ≥ {best['del_ratio']:.1f}")
    print(f"  close_loc ≥ {best['close_loc']:.2f}")
    print(f"  del_abs ≥ {best['del_abs_min']}")
    print(f"  Train: {best['train_n']} signals, 60d={best['train_60d']:+.1f}%, win={best['train_win']:.0f}%")
    print(f"  Test:  {best['test_n']} signals, 60d={best['test_60d']:+.1f}%, win={best['test_win']:.0f}%")
    
    # Current production values
    prod = res_df[(res_df['del_ratio'] == 1.3) & (res_df['close_loc'] == 0.6) & (res_df['del_abs_min'] == 0)]
    if len(prod) > 0:
        p = prod.iloc[0]
        print(f"\n  Current production (1.3 / 0.6 / del_abs≥0):")
        print(f"  Train: {p['train_n']} signals, 60d={p['train_60d']:+.1f}%, win={p['train_win']:.0f}%")
        t60 = f"{p['test_60d']:+.1f}%" if p['test_60d'] is not None else 'N/A'
        print(f"  Test:  {p['test_n']} signals, 60d={t60}")
else:
    print("  No combination had enough test signals. Need more scan dates.")
