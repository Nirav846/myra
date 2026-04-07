import os
import duckdb
import pandas as pd
from myra_app.librarian import Librarian
from myra_app.engine import Engine, SMCManager


def validate_smc1_integration():
    # Use read_only=False to trigger schema migration if needed
    lib = Librarian(read_only=False)

    symbol = "RVNL"
    # The day the breakout really intensified
    target_date = "2024-01-15"

    print(
        f"--- [HISTORICAL VALIDATION] Testing SMC-1 for {symbol} up to {target_date} ---"
    )

    # 1. Fetch 60 days of data leading UP TO target_date
    query = f"""
        SELECT date, open, high, low, close, volume, delivery_qty, delivery_percent
        FROM prices 
        WHERE symbol = '{symbol}' AND date <= '{target_date}'
        ORDER BY date DESC 
        LIMIT 60
    """
    df = lib.conn.execute(query).df()
    if df.empty:
        print("Error: Librarian failed to fetch historical data.")
        return

    # Ensure column names are lowercase for SMCManager and sorted ASC
    df.columns = [c.lower() for c in df.columns]
    df = df.sort_values("date")

    print(f"Data Head:\n{df[['date', 'close', 'delivery_qty']].head()}")
    print(f"Data Tail:\n{df[['date', 'close', 'delivery_qty']].tail()}")

    # 2. Test Engine Math (D-POC)
    d_poc = SMCManager.calculate_d_poc(df)
    conf = SMCManager.get_confluence_score(df)

    # We need to compute Phase for the LAST row in this slice
    phase = SMCManager.get_smc_phase(df, d_poc, conf)

    print(f"LTP on {target_date}: {df['close'].iloc[-1]:.2f}")
    print(f"Computed D-POC (60d): {d_poc:.2f}")
    print(f"Confluence Score: {conf:.4f}")
    print(f"Detected SMC Phase: {phase}")

    if phase == 2:
        print(
            "\nSUCCESS: SMC-1 detected PHASE 2 (Ignition) before the January breakout!"
        )
    elif phase == 1:
        print("\nSUCCESS: SMC-1 detected PHASE 1 (Accumulation) in the base.")
    else:
        print(
            f"\nRESULT: Detected Phase {phase}. (Note: Ignition requires Vol > 1.5x and Price > D-POC * 1.03)"
        )


if __name__ == "__main__":
    validate_smc1_integration()
