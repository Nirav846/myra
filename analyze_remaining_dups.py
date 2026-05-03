# analyze_remaining_dups.py
import sqlite3
from datetime import datetime

conn = sqlite3.connect('myra_app/db/myra_technical.db')
pairs = conn.execute("""
    SELECT d1, d2, COUNT(*) as cnt FROM (
        SELECT symbol, MIN(date) as d1, MAX(date) as d2
        FROM technical_data
        GROUP BY symbol, open, high, low, close, volume
        HAVING COUNT(*) > 1
    ) GROUP BY d1, d2 ORDER BY cnt DESC
""").fetchall()
conn.close()

consecutive = 0
far_apart = 0
for d1, d2, cnt in pairs:
    dt1 = datetime.strptime(d1, '%Y-%m-%d')
    dt2 = datetime.strptime(d2, '%Y-%m-%d')
    diff = abs((dt2 - dt1).days)
    if diff <= 3:
        consecutive += 1
    else:
        far_apart += 1
        if far_apart <= 10:
            print(f'Non-consecutive: {d1} <-> {d2} ({cnt} symbols, diff={diff}d)')

print(f'\nConsecutive pairs (≤3d): {consecutive}')
print(f'Far-apart pairs:        {far_apart}')