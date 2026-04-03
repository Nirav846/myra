import duckdb
import os

db_path = "results/Data/myra_market_data.db"
if os.path.exists(db_path):
    conn = duckdb.connect(db_path)
    
    # 1. Check summary table
    print("--- Fundamentals Summary Table Check ---")
    sql = "SELECT symbol, pe, roe, eps, book_value, last_updated FROM fundamentals LIMIT 5"
    try:
        print(conn.execute(sql).df())
    except Exception as e:
        print(f"Error checking summary table: {e}")

    # 2. Check quarterly table for specific symbols
    print("\n--- Fundamentals Quarterly Table Deep Dive ---")
    symbols = ['RELIANCE', 'TCS', 'TATASTEEL']
    for s in symbols:
        print(f"\nSymbol: {s}")
        sql_q = f"SELECT report_date, eps, book_value, source, last_updated FROM fundamentals_quarterly WHERE symbol='{s}' ORDER BY last_updated DESC LIMIT 3"
        try:
            print(conn.execute(sql_q).df())
        except Exception as e:
            print(f"Error checking quarterly table for {s}: {e}")

    conn.close()
else:
    print(f"Database not found at {db_path}")
