import pandas as pd
import sqlite3
import os
import sys

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.engine import Engine
from myra_app.strategies import insider_signals


def debug_insider():
    lib = Librarian()
    engine = Engine(lib)

    print("--- Insider Data Audit ---")
    # Check top 5 symbols with insider buying in last 60 days
    sql = """
        SELECT symbol, COUNT(DISTINCT date) as unique_days, SUM(value_cr) as total_cr
        FROM insider_trades
        WHERE type='Buy' AND date >= date('now', '-60 day')
        GROUP BY symbol
        ORDER BY unique_days DESC
        LIMIT 10
    """
    insider_df = pd.read_sql(sql, lib._inst_conn)
    print("Top Insider Buyers (60d):")
    print(insider_df)

    if insider_df.empty:
        print("No insider buying found in last 60 days.")
        return

    # Check if these symbols exist in prices table and have enough data
    symbols = insider_df["symbol"].tolist()
    for s in symbols:
        price_check = lib._tech_conn.execute(  # noqa: performance
            f"SELECT COUNT(*) FROM technical_data WHERE symbol = '{s}'"
        ).fetchone()[0]
        ma200_check = lib._tech_conn.execute(  # noqa: performance
            f"SELECT close, sma200 FROM calculated_indicators WHERE symbol = '{s}' AND date = (SELECT MAX(date) FROM calculated_indicators)"
        ).fetchone()

        print(f"\nSymbol: {s}")
        print(f"  Price Rows: {price_check}")
        if ma200_check:
            close, sma200 = ma200_check
            print(f"  Latest Close: {close}, SMA200: {sma200}")
            print(f"  Above SMA200: {close > (sma200 or 0)}")
        else:
            print("  No calculated indicators found for this symbol.")

    # Run actual strategy run for the top symbol
    target = symbols[0]
    df = lib.get_ohlcv(target)
    # Mock funda from engine logic
    # Need to simulate what engine.run_scan does

    # Pre-calculate similar to engine
    m5 = (
        lib._inst_conn.execute(
            f"SELECT SUM(value_cr) FROM insider_trades WHERE symbol='{target}' AND type='Buy' AND date >= date('now', '-5 day')"
        ).fetchone()[0]
        or 0
    )
    m60 = (
        lib._inst_conn.execute(
            f"SELECT SUM(value_cr) FROM insider_trades WHERE symbol='{target}' AND type='Buy' AND date >= date('now', '-60 day')"
        ).fetchone()[0]
        or 0
    )
    ud = (
        lib._inst_conn.execute(
            f"SELECT COUNT(DISTINCT date) FROM insider_trades WHERE symbol='{target}' AND type='Buy' AND date >= date('now', '-60 day')"
        ).fetchone()[0]
        or 0
    )

    def get_accel_score(days):
        if days > 5:
            return 3
        if days >= 3:
            return 2
        if days >= 1:
            return 1
        return 0

    funda = {
        "AV_Latest": m5,
        "AV_Total": m60,
        "AV_Accel": get_accel_score(ud),
        "ROE": 15,
        "Stage": "Stage 2",
    }

    print(f"\nRunning insider_signals.run for {target} with funda: {funda}")
    res = insider_signals.run(df, funda)
    print(f"Result: {res}")


if __name__ == "__main__":
    debug_insider()
