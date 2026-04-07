import sqlite3
import pandas as pd
import os
import sys
from datetime import datetime, timedelta

# Fix path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def detect_missing_candles(
    tech_db=None, calendar_csv=None, output_csv=None, lookback_days=1000
):
    """
    True Gap Detection based on ACTUAL presence in database.
    Only flags dates where we have NO data for an active symbol,
    starting from the EARLIEST date that symbol appears in our DB.
    """
    tech_db = tech_db if tech_db else os.path.join(PROJECT_ROOT, "db", "technical.db")
    meta_db = os.path.join(PROJECT_ROOT, "db", "meta.db")
    calendar_csv = (
        calendar_csv
        if calendar_csv
        else os.path.join(PROJECT_ROOT, "data", "trading_calendar_master.csv")
    )
    output_csv = (
        output_csv
        if output_csv
        else os.path.join(PROJECT_ROOT, "data", "missing_data.csv")
    )

    print("[MYRA] Initializing Empirical Gap Detection...")

    # 1. Get Active Symbols
    conn_meta = sqlite3.connect(meta_db)
    active_symbols = [
        r[0]
        for r in conn_meta.execute(
            "SELECT symbol FROM symbols_master WHERE is_active = 1 AND in_active_universe = 1"
        ).fetchall()
    ]
    conn_meta.close()

    if not active_symbols:
        print("[!] No active symbols found.")
        return

    import numpy as np

    # 2. Get Trading Days from Calendar
    df_cal = pd.read_csv(calendar_csv)
    # Filter for last 2 years roughly (730 days) as per user mandate
    # Or use provided lookback
    # Performance Guard Compliant (Fix 40)
    lookback_date = (datetime.now() - timedelta(days=lookback_days)).date().isoformat()
    # Fix 41: Use .loc for safety/performance
    trading_days = sorted(df_cal.loc[df_cal["date"] >= lookback_date, "date"].tolist())
    trading_days_arr = np.array(trading_days)

    # 3. Get Empirical Bounds from Technical DB
    conn_tech = sqlite3.connect(tech_db)
    print("[*] Calculating empirical bounds for symbols...")
    bounds = pd.read_sql(
        "SELECT symbol, MIN(date) as first, MAX(date) as last FROM technical_data GROUP BY symbol",
        conn_tech,
    )

    # Get all records for gap set operations
    existing = pd.read_sql(
        "SELECT symbol, date FROM technical_data WHERE date >= ?",
        conn_tech,
        params=(lookback_date,),
    )
    conn_tech.close()

    bounds_map = {row.symbol: row.first for row in bounds.itertuples(index=False)}
    existing_map = {}
    if not existing.empty:
        for sym, group in existing.groupby("symbol"):
            existing_map[sym] = set(group["date"])

    # 4. Gap Detection
    valid_days_cache = {}
    missing_symbols = []
    missing_dates = []

    for sym in active_symbols:
        # Determine the effective start date for this symbol
        # If we have data, we start from our earliest record.
        # If we have NO data, we start from lookback_date.
        effective_start = bounds_map.get(sym, lookback_date)

        if effective_start not in valid_days_cache:
            idx = np.searchsorted(trading_days_arr, effective_start)
            valid_days_cache[effective_start] = set(trading_days_arr[idx:])

        valid_days = valid_days_cache[effective_start]
        sym_dates = existing_map.get(sym, set())

        missing = valid_days - sym_dates

        if missing:
            missing_symbols.extend([sym] * len(missing))
            missing_dates.extend(missing)

    # 5. Final Report
    if missing_symbols:
        df_missing = pd.DataFrame(
            {"symbol": missing_symbols, "missing_date": missing_dates},
            index=range(len(missing_symbols)),
        )
    else:
        df_missing = pd.DataFrame(columns=["symbol", "missing_date"])
    if not df_missing.empty:
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        df_missing.to_csv(output_csv, index=False)
        print(
            f"[+] Found {len(df_missing)} EMPIRICAL missing candles. Saved to {output_csv}."
        )
    else:
        print("[+] SUCCESS: technical.db is FULLY POPULATED for all active symbols!")


if __name__ == "__main__":
    # Use 730 days (2 years) as default lookback for full population check
    detect_missing_candles(lookback_days=730)
