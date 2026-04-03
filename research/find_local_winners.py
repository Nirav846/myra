import os
import duckdb
import pandas as pd

def find_winners():
    db_path = os.path.join(os.getcwd(), 'results', 'Data', 'myra_market_data.db')
    conn = duckdb.connect(db_path, read_only=True)
    
    print("--- [RESEARCH] Scanning Database for 40%+ Winners (Sept 2024 - March 2026) ---")
    
    # Calculate 60-day rolling returns for all symbols
    query = """
        WITH price_expansion AS (
            SELECT 
                symbol, 
                date, 
                close,
                LAG(close, 60) OVER (PARTITION BY symbol ORDER BY date) as old_close
            FROM prices
        )
        SELECT 
            symbol, 
            MAX((close - old_close) / old_close * 100) as max_expansion,
            MIN(date) as data_start,
            MAX(date) as data_end
        FROM price_expansion
        WHERE old_close IS NOT NULL
        GROUP BY symbol
        HAVING max_expansion > 40
        ORDER BY max_expansion DESC
    """
    df = conn.execute(query).df()
    conn.close()

    if df.empty:
        print("No stocks found with 40%+ expansion in the current database range.")
    else:
        print(f"Found {len(df)} potential 'Local Multi-baggers':")
        print(df.head(20).to_string(index=False))

if __name__ == "__main__":
    find_winners()
