import sqlite3
import os

db_path = "db/myra_valuation.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(fundamentals);")
    cols = cursor.fetchall()
    for col in cols:
        print(col)
    conn.close()
else:
    print(f"File not found: {db_path}")
