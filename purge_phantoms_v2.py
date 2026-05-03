# save as purge_phantoms_v2.py (overwrite) and run: python purge_phantoms_v2.py
import sqlite3, os, csv

DB = "myra_app/db/myra_technical.db"
ARCHIVE = "data/Market_Archives"

conn = sqlite3.connect(DB)

# Fetch all date pairs with duplicate OHLCV groups
pairs = conn.execute("""
    SELECT d1, d2, COUNT(*) as cnt FROM (
        SELECT symbol,
               MIN(date) as d1, MAX(date) as d2
        FROM technical_data
        GROUP BY symbol, open, high, low, close, volume
        HAVING COUNT(*) > 1
    ) GROUP BY d1, d2 ORDER BY cnt DESC
""").fetchall()

print(f"Total pairs to resolve: {len(pairs)}")

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

phantom_dates = set()
skipped = 0

for d1, d2, cnt in pairs:
    # Get one symbol that has identical OHLCV on both d1 and d2
    sample = conn.execute("""
        SELECT symbol, open, high, low, close, volume
        FROM technical_data
        WHERE date = ? OR date = ?
        GROUP BY symbol, open, high, low, close, volume
        HAVING COUNT(DISTINCT date) >= 2
        LIMIT 1
    """, (d1, d2)).fetchone()
    
    if not sample:
        # Fallback: just pick any symbol with duplicates on either date
        sample = conn.execute("""
            SELECT symbol, open, high, low, close, volume
            FROM technical_data
            WHERE date = ? OR date = ?
            GROUP BY symbol, open, high, low, close, volume
            HAVING COUNT(*) > 1
            LIMIT 1
        """, (d1, d2)).fetchone()
    
    if not sample:
        print(f"SKIP {d1}<->{d2}: no sample symbol found")
        skipped += 1
        continue
    
    sym, o, h, l, c, v = sample
    db_ohlcv = (o, h, l, c, v)

    arch1 = get_archive_ohlcv(sym, d1)
    arch2 = get_archive_ohlcv(sym, d2)

    if arch1 == db_ohlcv and arch2 != db_ohlcv:
        phantom_dates.add(d2)
        print(f"Real: {d1}, Phantom: {d2}  ({cnt} symbols)")
    elif arch2 == db_ohlcv and arch1 != db_ohlcv:
        phantom_dates.add(d1)
        print(f"Real: {d2}, Phantom: {d1}  ({cnt} symbols)")
    else:
        # Both match or neither — ambiguous, skip for safety
        print(f"SKIP {d1}<->{d2}: ambiguous (arch1 match:{arch1==db_ohlcv}, arch2 match:{arch2==db_ohlcv})")
        skipped += 1

print(f"\nIdentified {len(phantom_dates)} phantom dates.")
print(f"Skipped pairs: {skipped}")

if phantom_dates:
    # Delete all phantom rows by matching duplicate groups
    total_deleted = 0
    phantom_list = sorted(phantom_dates)
    for pd in phantom_list:
        res = conn.execute("""
            DELETE FROM technical_data
            WHERE date = ?
              AND (symbol, open, high, low, close, volume) IN (
                  SELECT symbol, open, high, low, close, volume
                  FROM technical_data
                  WHERE date = ?
                  GROUP BY symbol, open, high, low, close, volume
              )
        """, (pd, pd)).rowcount
        total_deleted += res
        if res:
            print(f"  Deleted {res} rows from {pd}")
    
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(f"Total deleted: {total_deleted} rows.")

conn.close()