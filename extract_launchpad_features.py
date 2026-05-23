import sqlite3, os, time, pandas as pd, numpy as np
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

tech = os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical'])
conn = sqlite3.connect(tech)
t0 = time.time()

# 1. Load events and technical data
events = pd.read_sql("SELECT * FROM launchpad_events WHERE success = 1 AND breakout_date IS NOT NULL", conn)
print(f"Loaded {len(events)} events ({time.time()-t0:.1f}s)")

symbols = events['symbol'].unique().tolist()
placeholders = ",".join(["?"] * len(symbols))
tech_df = pd.read_sql(
    f"SELECT symbol, date, high, low, close, vwap, volume, delivery_pct, liquidity_distance, fvg_freshness FROM technical_data WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
    conn, params=symbols
)
conn.close()
print(f"Loaded {len(tech_df)} rows ({time.time()-t0:.1f}s)")

# 2. Convert columns to numeric
for c in ['high','low','close','vwap','volume','delivery_pct','liquidity_distance','fvg_freshness']:
    tech_df[c] = pd.to_numeric(tech_df[c], errors='coerce')

# 3. Merge events with technical data on symbol, then filter by date range
merged = events[['symbol','trigger_date','breakout_date','return_pct','max_drawdown_pct','days_to_breakout','success']].merge(
    tech_df, on='symbol', how='inner'
)
merged = merged[(merged['date'] >= merged['trigger_date']) & (merged['date'] <= merged['breakout_date'])]
print(f"Merged and filtered to {len(merged)} rows ({time.time()-t0:.1f}s)")

# 4. Compute rolling metrics inside each window using groupby on event + rolling per symbol
# We'll compute per-row metrics first, then aggregate by event
grp_sym = merged.groupby('symbol')

# ATR (14-day)
merged['atr'] = grp_sym['high'].transform(lambda x: x.rolling(14, min_periods=5).mean()) - \
                grp_sym['low'].transform(lambda x: x.rolling(14, min_periods=5).mean())
merged['range_atr_ratio'] = (merged['high'] - merged['low']) / (merged['atr'] + 1e-9)

# Volume ratio (20-day)
merged['vol_avg_20'] = grp_sym['volume'].transform(lambda x: x.rolling(20, min_periods=5).mean())
merged['vol_ratio'] = merged['volume'] / (merged['vol_avg_20'] + 1e-9)

# Delivery Z-score (20-day)
merged['del_avg_20'] = grp_sym['delivery_pct'].transform(lambda x: x.rolling(20, min_periods=5).mean())
merged['del_std_20'] = grp_sym['delivery_pct'].transform(lambda x: x.rolling(20, min_periods=5).std())
merged['del_zscore'] = (merged['delivery_pct'] - merged['del_avg_20']) / (merged['del_std_20'] + 1e-9)
print(f"Rolling metrics computed ({time.time()-t0:.1f}s)")

# 5. Aggregate per event (min/mean of rolling metrics, count of rows)
agg = merged.groupby(['symbol','trigger_date','breakout_date'], as_index=False).agg(
    return_pct=('return_pct','first'),
    max_drawdown_pct=('max_drawdown_pct','first'),
    days_to_breakout=('days_to_breakout','first'),
    success=('success','first'),
    del_zscore_min=('del_zscore','min'),
    del_zscore_mean=('del_zscore','mean'),
    range_atr_min=('range_atr_ratio','min'),
    vol_ratio_min=('vol_ratio','min'),
    digestion_days=('date','count'),
    close_min=('close','min'),
    vwap_min=('vwap','min'),
    volume_min=('volume','min'),
    liquidity_min=('liquidity_distance', lambda x: x.min() if x.notna().any() else 0.0),
    fvg_freshness_min=('fvg_freshness', lambda x: x.min() if x.notna().any() else 0.0),
)
print(f"Aggregated to {len(agg)} feature rows ({time.time()-t0:.1f}s)")

# 6. Save
conn = sqlite3.connect(tech)
agg.to_sql('launchpad_features', conn, if_exists='replace', index=False)
conn.close()
print(f"Saved. Total: {time.time()-t0:.1f}s")
