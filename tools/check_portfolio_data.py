import duckdb
import os

db = "results/Data/myra_market_data.db"
symbols = [
    "APCOTEXIND",
    "SJVN",
    "TEJASNET",
    "JSWENERGY",
    "GNFC",
    "APOLLOPIPE",
    "TEXMOPIPES",
    "NTPC",
    "AGARIND",
    "TATAPOWER",
    "CAPACITE",
    "SAKSOFT",
    "ADVENZYMES",
    "NLCINDIA",
    "EKC",
    "DSSL",
    "RHIM",
    "AARTIIND",
    "PCBL",
    "CLEAN",
    "RVNL",
    "NHPC",
    "PREMEXPLN",
    "MUFIN",
]
s_list = "','".join(symbols)

if os.path.exists(db):
    con = duckdb.connect(db, read_only=True)
    res = con.execute(
        f"SELECT symbol, COUNT(*) as c, MIN(date), MAX(date) FROM prices WHERE symbol IN ('{s_list}') GROUP BY symbol"
    ).fetchall()
    print("Symbol | Count | Min Date | Max Date")
    for r in res:
        print(f"{r[0]} | {r[1]} | {r[2]} | {r[3]}")
    con.close()
else:
    print("DB not found")
