import duckdb
import os

db_path = "results/Data/myra_market_data.db"
if os.path.exists(db_path):
    conn = duckdb.connect(db_path)
    print("Forcing fundamental refresh for Active Universe...")
    # Reset sync markers
    conn.execute("UPDATE symbols_master SET last_fundamental_update = '1900-01-01'")
    # Clear summary table to ensure clean mapping from quarterly
    conn.execute("DELETE FROM fundamentals")
    conn.close()
    print("Done. Please restart MYRA. On startup, background sync will repopulate fundamentals correctly.")
else:
    print("DB not found.")
