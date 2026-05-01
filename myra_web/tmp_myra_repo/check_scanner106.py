import sqlite3, pandas as pd
conn = sqlite3.connect('myra_app/db/myra_technical.db')
for sym in ['HDFCBANK','TCS','RELIANCE','INFY','SBIN']:
    df = pd.read_sql(f"SELECT date, close FROM technical_data WHERE symbol='{sym}' ORDER BY date DESC LIMIT 252", conn)
    if len(df) == 0:
        print(f'{sym}: NO DATA')
        continue
    h52 = df['close'].max()
    latest = df['close'].iloc[0]
    pct = (latest / h52 - 1) * 100
    print(f'{sym}: close={latest:.2f}, 52w_high={h52:.2f}, pct_from_high={pct:.1f}%, trigger={pct >= -2}')
conn.close()
