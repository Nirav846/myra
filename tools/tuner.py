import duckdb
import os

db_path = os.path.join("results", "Data", "myra_market_data.db")
conn = duckdb.connect(db_path)

print("--- Data Health Metrics ---")
try:
    p_count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    p_min = conn.execute("SELECT MIN(date) FROM prices").fetchone()[0]
    p_max = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    print(f"Prices: {p_count} rows, {p_min} to {p_max}")

    c_count = conn.execute("SELECT COUNT(*) FROM calculated_indicators").fetchone()[0]
    c_max = conn.execute("SELECT MAX(date) FROM calculated_indicators").fetchone()[0]
    print(f"Indicators: {c_count} rows, Max Date: {c_max}")

    # Check for indices
    indices = conn.execute("PRAGMA show_indexes").fetchall()
    print(f"Indices: {len(indices)}")
    for idx in indices:
        print(f" - {idx}")

except Exception as e:
    print(f"Error: {e}")

conn.close()
