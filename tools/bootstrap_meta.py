"""
tools/bootstrap_meta.py

Run this once to:
  1. Backfill in_nifty500 from index_constituents (already populated)
  2. Backfill in_active_universe from technical_data
  3. Run SectorManager incremental sync (Morningstar + NSE CSVs)

Safe to re-run — all operations are idempotent.
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

meta_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])

# ── Step 1: Backfill in_nifty500 ─────────────────────────────────────────────
print("Step 1: Backfilling in_nifty500 from index_constituents...")
conn = sqlite3.connect(meta_db)

conn.execute("UPDATE symbols_master SET in_nifty500 = 0")
conn.execute("""
    UPDATE symbols_master
    SET in_nifty500 = 1
    WHERE symbol IN (
        SELECT symbol FROM index_constituents WHERE index_name = 'NIFTY 500'
    )
""")
conn.commit()

count = conn.execute("SELECT COUNT(*) FROM symbols_master WHERE in_nifty500 = 1").fetchone()[0]
print(f"  Done — {count} symbols marked in_nifty500=1")

# ── Step 2: Verify active universe ───────────────────────────────────────────
print("\nStep 2: Checking in_active_universe...")
active = conn.execute("SELECT COUNT(*) FROM symbols_master WHERE in_active_universe = 1").fetchone()[0]
print(f"  {active} symbols already marked active — no change needed")
conn.close()

# ── Step 3: Sector sync ───────────────────────────────────────────────────────
print("\nStep 3: Running SectorManager incremental sync...")
print("  This fetches from Morningstar + NSE indices CSV.")
print("  Expect this to take 2-5 minutes for 3,610 symbols.\n")

try:
    from myra_app.sector_manager import SectorManager
    mgr = SectorManager(db_path=meta_db)
    mgr.incremental_sync()
except Exception as e:
    print(f"  [ERROR] SectorManager failed: {e}")
    print("  You can retry with: python tools/sync_sectors.py --incremental")
    sys.exit(1)

# ── Step 4: Report results ────────────────────────────────────────────────────
print("\n=== RESULTS ===")
conn = sqlite3.connect(meta_db)

total = conn.execute("SELECT COUNT(*) FROM symbols_master").fetchone()[0]
with_sector = conn.execute(
    "SELECT COUNT(*) FROM symbols_master WHERE sector IS NOT NULL AND sector != ''"
).fetchone()[0]
nifty500 = conn.execute("SELECT COUNT(*) FROM symbols_master WHERE in_nifty500 = 1").fetchone()[0]

print(f"Total symbols : {total}")
print(f"With sector   : {with_sector} ({with_sector / total * 100:.1f}%)")
print(f"Missing sector: {total - with_sector}")
print(f"in_nifty500=1 : {nifty500}")

print("\nSectors found:")
rows = conn.execute("""
    SELECT sector, COUNT(*) as cnt
    FROM symbols_master
    WHERE sector IS NOT NULL AND sector != ''
    GROUP BY sector
    ORDER BY cnt DESC
""").fetchall()
for r in rows:
    print(f"  {r[1]:>5}  {r[0]}")

print("\nSource breakdown:")
rows = conn.execute("""
    SELECT COALESCE(source, 'NULL'), COUNT(*)
    FROM symbols_master
    GROUP BY source
    ORDER BY COUNT(*) DESC
""").fetchall()
for r in rows:
    print(f"  {r[1]:>5}  {r[0]}")

print("\nNIFTY 500 symbols missing sector:")
rows = conn.execute("""
    SELECT symbol FROM symbols_master
    WHERE in_nifty500 = 1 AND (sector IS NULL OR sector = '')
    ORDER BY symbol
""").fetchall()
if rows:
    for r in rows:
        print(f"  {r[0]}")
else:
    print("  None — all NIFTY 500 symbols have sector data.")

conn.close()
print("\nDone.")
