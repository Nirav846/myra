import sqlite3
import pandas as pd

conn = sqlite3.connect("db/myra_technical.db")
df = pd.read_sql(
    "SELECT date, close FROM technical_data WHERE symbol = 'NIFTYBEES' AND date >= '2025-12-22' AND date <= '2026-03-22' ORDER BY date ASC", conn
)
if not df.empty:
    ret = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"NIFTYBEES Return: {ret:.2f}%")
else:
    print("No data found for NIFTYBEES in the period.")
conn.close()
