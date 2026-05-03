# save as purge_ambiguous_phantoms.py (overwrite previous) and run: python purge_ambiguous_phantoms.py
import sqlite3, os, csv

DB = "myra_app/db/myra_technical.db"
ARCHIVE = "data/Market_Archives"

conn = sqlite3.connect(DB)
pairs = conn.execute("""
    SELECT d1, d2, COUNT(*) as cnt FROM (
        SELECT symbol,
               MIN(date) as d1, MAX(date) as d2
        FROM technical_data
        GROUP BY symbol, open, high, low, close, volume
        HAVING COUNT(*) > 1
    ) GROUP BY d1, d2 ORDER BY cnt DESC
""").fetchall()

resolved = {}
phantom_to_real = {}

def get_archive_ohlcv(symbol, date_str):
    fname = os.path.join(ARCHIVE, f"nse_full_{date_str}.csv")
    if not os.path.exists(fname):
        return None
    with open(fname, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('symbol', '').strip() == symbol:
                try:
                    o = float(row['open_price'])
                    h = float(row['high_price'])
                    l = float(row['low_price'])
                    c = float(row['close_price'])
                    v = int(float(row['ttl_trd_qnty']))
                    return (o, h, l, c, v)
                except (ValueError, KeyError):
                    return None
    return None

for d1, d2, cnt in pairs:
    if d1 in resolved and d2 in resolved:
        continue
    sample = conn.execute("""
        SELECT symbol, open, high, low, close, volume
        FROM technical_data
        WHERE date = ?
        GROUP BY symbol, open, high, low, close, volume
        HAVING COUNT(*) > 1
        LIMIT 1
    """, (d1,)).fetchone()
    if not sample:
        sample = conn.execute("""
            SELECT symbol, open, high, low, close, volume
            FROM technical_data
            WHERE date = ?
            GROUP BY symbol, open, high, low, close, volume
            HAVING COUNT(*) > 1
            LIMIT 1
        """, (d2,)).fetchone()
    if not sample:
        continue
    sym, o, h, l, c, v = sample
    db_ohlcv = (o, h, l, c, v)

    arch1 = get_archive_ohlcv(sym, d1)
    arch2 = get_archive_ohlcv(sym, d2)
    if arch1 == db_ohlcv and arch2 != db_ohlcv:
        real, phantom = d1, d2
    elif arch2 == db_ohlcv and arch1 != db_ohlcv:
        real, phantom = d2, d1
    else:
        print(f"SKIP pair {d1}<->{d2}: could not determine (arch1 match:{arch1==db_ohlcv}, arch2 match:{arch2==db_ohlcv})")
        continue
    resolved[real] = 'real'
    resolved[phantom] = 'phantom'
    phantom_to_real[phantom] = real
    print(f"Real: {real}, Phantom: {phantom}  ({cnt} symbols)")

print(f"\nFound {len(phantom_to_real)} phantom dates. Purging...")
cursor = conn.cursor()
total_deleted = 0
for phantom, real in phantom_to_real.items():
    res = cursor.execute("""
        DELETE FROM technical_data
        WHERE date = ?
          AND EXISTS (
              SELECT 1 FROM technical_data t2
              WHERE t2.symbol = technical_data.symbol
                AND t2.open = technical_data.open
                AND t2.high = technical_data.high
                AND t2.low = technical_data.low
                AND t2.close = technical_data.close
                AND t2.volume = technical_data.volume
                AND t2.date = ?
          )
    """, (phantom, real)).rowcount
    total_deleted += res
    if res > 0:
        print(f"  Deleted {res} rows from {phantom} (source {real})")

conn.commit()
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
print(f"Total deleted: {total_deleted} rows.")
conn.close()