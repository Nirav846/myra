# save as purge_phantoms.py in project root, run: python purge_phantoms.py
import sqlite3
import os

DB_PATH = "myra_app/db/myra_technical.db"
ARCHIVE_DIR = "data/Market_Archives"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 1. Find all duplicate groups (same symbol, same OHLCV, multiple dates)
pairs = cursor.execute("""
    SELECT d1, d2, COUNT(*) as cnt
    FROM (
        SELECT symbol,
               MIN(date) as d1,
               MAX(date) as d2
        FROM technical_data
        GROUP BY symbol, open, high, low, close, volume
        HAVING COUNT(*) > 1
    )
    GROUP BY d1, d2
    ORDER BY cnt DESC
""").fetchall()

phantom_dates = set()
ambiguous = []

for d1, d2, cnt in pairs:
    f1 = os.path.exists(os.path.join(ARCHIVE_DIR, f"nse_full_{d1}.csv"))
    f2 = os.path.exists(os.path.join(ARCHIVE_DIR, f"nse_full_{d2}.csv"))
    if f1 and not f2:
        phantom_dates.add(d2)
    elif f2 and not f1:
        phantom_dates.add(d1)
    else:
        ambiguous.append((d1, d2, f1, f2, cnt))

print(f"Identified {len(phantom_dates)} phantom dates.")
print(f"Ambiguous pairs: {len(ambiguous)} (logged)")

# 2. Generate DELETE script
sql_lines = []
sql_lines.append("BEGIN TRANSACTION;")
phantom_list = list(phantom_dates)
phantom_list.sort()
for pd in phantom_list:
    # Delete rows on phantom date that have a duplicate group containing a real date.
    # Use EXISTS to ensure we only delete rows where there is another row with
    # identical OHLCV on a different (presumed real) date.
    sql_lines.append(f"""
DELETE FROM technical_data
WHERE date = '{pd}'
  AND EXISTS (
      SELECT 1 FROM technical_data t2
      WHERE t2.symbol = technical_data.symbol
        AND t2.open = technical_data.open
        AND t2.high = technical_data.high
        AND t2.low = technical_data.low
        AND t2.close = technical_data.close
        AND t2.volume = technical_data.volume
        AND t2.date != '{pd}'
  );
""")
sql_lines.append("COMMIT;")

with open("purge_phantoms.sql", "w") as f:
    f.write("\n".join(sql_lines))

print("SQL written to purge_phantoms.sql")
print(f"It will delete duplicate rows for {len(phantom_dates)} phantom dates.")

# 3. Log ambiguous pairs
if ambiguous:
    with open("ambiguous_pairs.log", "w") as f:
        f.write("Ambiguous date pairs (both or neither archive file found):\n")
        for d1, d2, f1, f2, cnt in ambiguous:
            f.write(f"{d1} <-> {d2}  files: {f1}, {f2}  count: {cnt}\n")
    print("Ambiguous pairs written to ambiguous_pairs.log – review manually.")

conn.close()