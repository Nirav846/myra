# check_ml_data.py
import sqlite3, os
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical'])
conn = sqlite3.connect(tech_db)

# 1. Trading days in last 2 years
cnt = conn.execute(
    "SELECT COUNT(DISTINCT date) FROM technical_data WHERE date >= '2024-05-18'"
).fetchone()[0]
print(f"Trading days in last 2 years: {cnt}")

# 2. Symbols with at least 1 year (252 days) of data
syms = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT symbol FROM technical_data
        WHERE date >= '2024-05-18'
        GROUP BY symbol HAVING COUNT(*) >= 252
    )
""").fetchone()[0]
print(f"Symbols with 1+ year of data: {syms}")

# 3. SMC columns populated for the latest date
latest = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]
print(f"\nLatest date: {latest}")
for col in ['bullish_fvg','bearish_fvg','trend_alignment',
            'delivery_ma_60','volatility_compression_score','relative_volume_score']:
    non_null = conn.execute(
        f"SELECT COUNT(*) FROM technical_data WHERE date=? AND {col} IS NOT NULL",
        (latest,)
    ).fetchone()[0]
    print(f"  {col}: {non_null} non-null rows")

conn.close()