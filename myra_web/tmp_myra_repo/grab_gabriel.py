import sqlite3
conn = sqlite3.connect('myra_app/db/myra_technical.db')
cols = [desc[1] for desc in conn.execute('PRAGMA table_info(technical_data)').fetchall()]
rows = conn.execute("SELECT * FROM technical_data WHERE symbol='GABRIEL' AND date IN ('2026-03-27','2025-12-18') ORDER BY date").fetchall()
for row in rows:
    print(f'\n=== {row[0]}  |  {row[1]} ===')
    for col, val in zip(cols, row):
        print(f'  {str(col):30s}: {val}')
conn.close()
