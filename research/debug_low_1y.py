
import os
import pandas as pd
from myra_app.librarian import Librarian

def check_low_1y():
    lib = Librarian(read_only=True)
    try:
        latest_date = lib.conn.execute("SELECT MAX(date) FROM calculated_indicators").fetchone()[0]
        print(f"Latest date: {latest_date}")
        
        df = lib.conn.execute("""
            SELECT count(*) as total,
                   sum(case when low_1y > 0 then 1 else 0 end) as populated,
                   avg(low_1y) as avg_low
            FROM calculated_indicators 
            WHERE date = ?
        """, (latest_date,)).df()
        print(df)
        
        # Check some samples
        samples = lib.conn.execute("""
            SELECT symbol, low_1y 
            FROM calculated_indicators 
            WHERE date = ? 
            LIMIT 5
        """, (latest_date,)).df()
        print("Samples:")
        print(samples)
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        lib.close()

if __name__ == "__main__":
    check_low_1y()
