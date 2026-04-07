import duckdb
import pandas as pd

conn = duckdb.connect("results/Data/myra_market_data.db")
df = conn.execute(
    "SELECT date, close FROM prices WHERE symbol = 'NIFTYBEES' AND date >= '2025-12-22' AND date <= '2026-03-22' ORDER BY date ASC"
).df()
if not df.empty:
    ret = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"NIFTYBEES Return: {ret:.2f}%")
else:
    print("No data found for NIFTYBEES in the period.")
conn.close()
