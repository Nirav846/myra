import os
import duckdb
import pandas as pd

def find_institutional_winners():
    db_path = os.path.join(os.getcwd(), 'results', 'Data', 'myra_market_data.db')
    conn = duckdb.connect(db_path, read_only=True)
    
    print("--- [RESEARCH] Scanning for NIFTY 500 Winners (40%+ move in 60d) ---")
    
    # Filter by NIFTY 500 and exclude outliers with unrealistic moves (e.g., > 300% in 60d)
    query = """
        WITH price_expansion AS (
            SELECT 
                p.symbol, 
                p.date, 
                p.close,
                LAG(p.close, 60) OVER (PARTITION BY p.symbol ORDER BY p.date) as old_close
            FROM prices p
            JOIN index_constituents ic ON p.symbol = ic.symbol
            WHERE ic.index_name = 'NIFTY 500'
        )
        SELECT 
            symbol, 
            MAX((close - old_close) / old_close * 100) as max_expansion,
            MIN(date) as data_start,
            MAX(date) as data_end
        FROM price_expansion
        WHERE old_close IS NOT NULL
        GROUP BY symbol
        HAVING max_expansion BETWEEN 40 AND 300
        ORDER BY max_expansion DESC
    """
    df = conn.execute(query).df()
    conn.close()

    if df.empty:
        print("No NIFTY 500 winners found with 40-300% expansion.")
    else:
        print(f"Found {len(df)} Institutional Winners in NIFTY 500:")
        print(df.head(20).to_string(index=False))

if __name__ == "__main__":
    find_institutional_winners()
