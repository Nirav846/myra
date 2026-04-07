import duckdb

conn = duckdb.connect("results/Data/myra_market_data.db")
p = conn.execute("SELECT COUNT(*) FROM prices WHERE symbol='AXISBANK'").fetchone()[0]
c = conn.execute(
    "SELECT COUNT(*) FROM calculated_indicators WHERE symbol='AXISBANK'"
).fetchone()[0]
print(f"AXISBANK - Prices: {p} | Indicators: {c}")
conn.close()
