import duckdb
import os

db = "results/Data/myra_market_data.db"
if os.path.exists(db):
    con = duckdb.connect(db, read_only=True)
    res = con.execute(
        "SELECT date, COUNT(*) FROM prices GROUP BY date ORDER BY date ASC"
    ).fetchall()
    print("Date | Count")
    for r in res:
        print(f"{r[0]} | {r[1]}")
    con.close()
else:
    print("DB not found")
