import sqlite3
import pandas as pd

DB_PATH = "db/myra_technical.db"

def scan_institutional_accumulation(lookback=5):
    conn = sqlite3.connect(DB_PATH)
    
    # Query for the latest 2 dates to compare current vs previous
    # This is MUCH faster now thanks to your new index
    query = """
    SELECT symbol, date, close, delivery, delivery_pct
    FROM technical_data
    WHERE date IN (SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT ?)
    ORDER BY symbol, date ASC
    """
    
    df = pd.read_sql_query(query, conn, params=(lookback,))
    conn.close()
    
    if df.empty:
        print("[!] No data found for the scan.")
        return

    # Group by symbol to calculate growth
    results = []
    for symbol, group in df.groupby('symbol'):
        if len(group) < lookback: continue
        
        # Latest vs Start of lookback
        start_price = group['close'].iloc[0]
        end_price = group['close'].iloc[-1]
        
        start_deliv = group['delivery'].iloc[0]
        end_deliv = group['delivery'].iloc[-1]
        
        avg_deliv_pct = group['delivery_pct'].mean()
        
        # SIGNAL: Price Up + Delivery Up (The "Atomic" Signal)
        if end_price > start_price and end_deliv > start_deliv:
            price_change = ((end_price - start_price) / start_price) * 100
            deliv_growth = ((end_deliv - start_deliv) / start_deliv) * 100
            
            results.append({
                'Symbol': symbol,
                'Price Change %': round(price_change, 2),
                'Deliv Growth %': round(deliv_growth, 2),
                'Avg Deliv %': round(avg_deliv_pct, 2)
            })

    # Sort by the strongest delivery growth
    scan_results = pd.DataFrame(results).sort_values(by='Deliv Growth %', ascending=False)
    print(f"\n[MYRA] Top Institutional Accumulation (Last {lookback} Days):")
    print(scan_results.head(15).to_string(index=False))

if __name__ == "__main__":
    scan_institutional_accumulation()