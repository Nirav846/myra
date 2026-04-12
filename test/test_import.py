import duckdb
import os

db = "results/Data/myra_market_data.db"
local = "results/Data/Market_Archives/nse_full_02052023.csv"

if os.path.exists(db):
    con = duckdb.connect(db)
    try:
        sql = f"INSERT OR REPLACE INTO prices SELECT trim(SYMBOL) as symbol, strptime(trim(DATE1), '%d-%b-%Y')::DATE as date, OPEN_PRICE as open, HIGH_PRICE as high, LOW_PRICE as low, CLOSE_PRICE as close, TTL_TRD_QNTY as volume, try_cast(trim(DELIV_QTY) AS BIGINT) as delivery_qty, try_cast(trim(DELIV_PER) AS DOUBLE) as delivery_percent, 'NSE' as exchange FROM read_csv_auto('{local.replace('\\','/')}') WHERE trim(SERIES) IN ('EQ', 'BE', 'BZ', 'SM')"
        con.execute(sql)
        print("Success")
        res = con.execute(
            "SELECT COUNT(*) FROM prices WHERE date = '2023-05-02'"
        ).fetchone()
        print(f"Rows for 2023-05-02: {res[0]}")
    except Exception as e:
        print(f"Error: {e}")
    con.close()
else:
    print("DB not found")
