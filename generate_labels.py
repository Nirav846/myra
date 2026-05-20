import sqlite3, os, pandas as pd, numpy as np, time
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

CFG = {
    "trigger_zscore_min": 2.0, "trigger_vol_zscore_min": 2.5,
    "trigger_del_pct_min": 45.0, "trigger_vol_fallback_del_min": 40.0,
    "trigger_window_days": 5, "digestion_min_days": 20,
    "digestion_max_days": 180, "breakout_range_mult": 1.02,
    "breakout_vol_mult": 1.3, "breakout_del_pct_min": 10.0,
    "confirm_window": 5, "confirm_max_drawdown_pct": 8.0,
    "not_extended_mult": 1.02
}

tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical'])
conn = sqlite3.connect(tech_db)
symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM technical_data").fetchall()]

all_events = []
t0 = time.time()

for si, symbol in enumerate(symbols):
    if (si + 1) % 500 == 0:
        print(f"  Progress: {si+1}/{len(symbols)}")

    df = pd.read_sql(
        f"SELECT * FROM technical_data WHERE symbol=? AND date>='2020-01-01' ORDER BY date",
        conn, params=(symbol,)
    )
    if len(df) < 60:
        continue

    df = df.reset_index(drop=True)
    df['del_mean'] = df['delivery'].rolling(20, min_periods=10).mean()
    df['del_std'] = df['delivery'].rolling(20, min_periods=10).std()
    df['del_zscore'] = (df['delivery'] - df['del_mean']) / (df['del_std'] + 1e-9)
    df['vol_mean'] = df['volume'].rolling(20, min_periods=10).mean()
    df['vol_std'] = df['volume'].rolling(20, min_periods=10).std()
    df['vol_zscore'] = (df['volume'] - df['vol_mean']) / (df['vol_std'] + 1e-9)
    df['vol_avg'] = df['volume'].rolling(20, min_periods=10).mean()
    df['high_20'] = df['high'].rolling(20, min_periods=10).max()
    df['ret_10'] = df['close'].pct_change(10)

    i = 0
    while i < len(df):
        row = df.iloc[i]
        is_trigger = (
            (row['del_zscore'] >= CFG['trigger_zscore_min'] and row['delivery_pct'] >= CFG['trigger_del_pct_min'])
            or (row['vol_zscore'] >= CFG['trigger_vol_zscore_min'] and row['delivery_pct'] >= CFG['trigger_vol_fallback_del_min'])
        )
        not_extended = row['close'] < row['high_20'] * CFG['not_extended_mult'] if pd.notna(row['high_20']) else True
        not_rallied = row['ret_10'] < 0.10 if pd.notna(row['ret_10']) else True

        if is_trigger and not_extended and not_rallied:
            cluster_end = min(i + CFG['trigger_window_days'], len(df))
            peak_pos = df.loc[i:cluster_end-1, 'close'].values.argmax()
            peak_idx = i + peak_pos
            trig_price = float(df.loc[peak_idx, 'close'])
            trig_date = df.loc[peak_idx, 'date']

            digest_start = peak_idx + 1
            search_end = min(digest_start + CFG['digestion_max_days'], len(df))
            found_breakout = False

            for j in range(digest_start, search_end):
                if j - peak_idx < CFG['digestion_min_days']:
                    continue
                r = df.iloc[j]
                ref_start = max(digest_start, j - 20)
                ref_high = df.loc[ref_start:j-1, 'close'].max()
                threshold = ref_high * CFG['breakout_range_mult']
                vol_ratio = r['volume'] / r['vol_avg'] if r['vol_avg'] > 0 else 0

                if (r['close'] > threshold and vol_ratio >= CFG['breakout_vol_mult'] and r['delivery_pct'] >= CFG['breakout_del_pct_min']):
                    confirm_end = min(j + 1 + CFG['confirm_window'], len(df))
                    confirm_df = df.loc[j+1:confirm_end-1]
                    if len(confirm_df) == 0:
                        continue
                    bc = float(r['close'])
                    max_dd = (bc - confirm_df['close'].min()) / bc * 100
                    any_above = (confirm_df['close'] >= bc * 0.99).any()
                    if max_dd <= CFG['confirm_max_drawdown_pct'] and any_above:
                        launchpad_row = df.iloc[j-1]
                        digest_low = df.loc[digest_start:j-1, 'close'].min()
                        digest_low_date = df.loc[df.loc[digest_start:j-1, 'close'].idxmin(), 'date']
                        drawdown = (trig_price - digest_low) / trig_price * 100
                        ret_pct = (bc - float(launchpad_row['close'])) / float(launchpad_row['close']) * 100

                        all_events.append({
                            'symbol': symbol, 'trigger_date': trig_date,
                            'trigger_peak_price': trig_price,
                            'digestion_low_price': digest_low,
                            'digestion_low_date': digest_low_date,
                            'launchpad_date': launchpad_row['date'],
                            'launchpad_close': float(launchpad_row['close']),
                            'breakout_date': r['date'], 'breakout_close': bc,
                            'return_pct': round(ret_pct, 4),
                            'days_to_breakout': j - peak_idx,
                            'success': 1, 'max_drawdown_pct': round(drawdown, 4),
                            'min_range_atr_ratio': None, 'min_vol_ratio': None
                        })
                        found_breakout = True
                        break

            if not found_breakout:
                all_events.append({
                    'symbol': symbol, 'trigger_date': trig_date,
                    'trigger_peak_price': trig_price, 'success': 0,
                    'digestion_low_price': None, 'digestion_low_date': None,
                    'launchpad_date': None, 'launchpad_close': None,
                    'breakout_date': None, 'breakout_close': None,
                    'return_pct': None, 'days_to_breakout': None,
                    'max_drawdown_pct': None, 'min_range_atr_ratio': None, 'min_vol_ratio': None
                })

            i = cluster_end
        else:
            i += 1

df_events = pd.DataFrame(all_events)
df_events.to_sql('launchpad_events', conn, if_exists='replace', index=False)
conn.close()

success = int(df_events['success'].sum())
total = len(df_events)
print(f"\nDone in {time.time()-t0:.0f}s. {total} events, {success} successes ({success/total*100:.1f}% success rate)")
