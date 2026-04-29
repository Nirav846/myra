import sqlite3

conn = sqlite3.connect("db/myra_technical.db")
p = conn.execute(
    "SELECT COUNT(*) FROM technical_data WHERE symbol='AXISBANK'"
).fetchone()[0]
c = conn.execute(
    "SELECT COUNT(*) FROM calculated_indicators WHERE symbol='AXISBANK'"
).fetchone()[0]
print(f"AXISBANK - Prices: {p} | Indicators: {c}")
conn.close()
