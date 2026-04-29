import os
import sqlite3

db = "db/myra_technical.db"
if os.path.exists(db):
    con = sqlite3.connect(db)
    res = con.execute(
        "SELECT date, COUNT(*) FROM technical_data GROUP BY date ORDER BY date ASC"
    ).fetchall()
    print("Date | Count")
    for r in res:
        print(f"{r[0]} | {r[1]}")
    con.close()
else:
    print("DB not found")
