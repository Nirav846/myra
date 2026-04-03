import duckdb
import os

def audit_db(symbol='RELIANCE'):
    db_path = 'results/Data/myra_market_data.db'
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return

    conn = duckdb.connect(db_path)
    print(f"--- {symbol} Data Audit ---")
    
    try:
        prices = conn.execute(f"SELECT delivery_qty, delivery_percent FROM prices WHERE symbol='{symbol}' ORDER BY date DESC LIMIT 1").fetchone()
        print(f"Prices (Deliv): {prices}")
    except Exception as e: print(f"Prices Error: {e}")

    try:
        indicators = conn.execute(f"SELECT money_flow_cr, rdv, squeeze_flag, eps_latest, bvps_latest FROM calculated_indicators WHERE symbol='{symbol}' ORDER BY date DESC LIMIT 1").fetchone()
        print(f"Calculated Indicators: {indicators}")
    except Exception as e: print(f"Indicators Error: {e}")

    try:
        fundamentals = conn.execute(f"SELECT pe, roe, profit_growth, sales_growth, debt_to_equity FROM fundamentals WHERE symbol='{symbol}'").fetchone()
        print(f"Fundamentals (Table): {fundamentals}")
    except Exception as e: print(f"Fundamentals Error: {e}")

    try:
        quarterly = conn.execute(f"SELECT eps, net_profit FROM fundamentals_quarterly WHERE symbol='{symbol}' ORDER BY last_updated DESC LIMIT 1").fetchone()
        print(f"Fundamentals (Quarterly): {quarterly}")
    except Exception as e: print(f"Quarterly Error: {e}")

    conn.close()

if __name__ == "__main__":
    audit_db()
