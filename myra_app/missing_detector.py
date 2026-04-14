import sqlite3
import pandas as pd
import os
import sys
from datetime import datetime, timedelta

from myra_app.librarian_core import LibrarianCore

# Fix path - Ensures MYRA can find its internal modules
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def detect_missing_candles(
    tech_db=None, calendar_csv=None, output_csv=None, lookback_days=1000
):
    """
    True Gap Detection based on ACTUAL presence in the technical db.
    """
    tech_db = (
        tech_db if tech_db else os.path.join(PROJECT_ROOT, "db", LibrarianCore.DB_MAP["technical"])
    )
    meta_db = os.path.join(PROJECT_ROOT, "db", LibrarianCore.DB_MAP["meta"])

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

    if not os.path.exists(tech_db):
        print(f"[!] Critical Error: Database not found at {tech_db}")
        return

    print(f"[MYRA] Scanning {tech_db} for Delivery Gaps...")

    # 1. Get Active Symbols from Meta DB
    try:
        conn_meta = sqlite3.connect(meta_db)
        active_symbols = [
            r[0]
            for r in conn_meta.execute(
                "SELECT symbol FROM symbols_master WHERE is_active = 1"
            ).fetchall()
        ]
        conn_meta.close()
    except Exception as e:
        print(
            f"[!] Meta DB Error: {e}. Falling back to symbols present in technical DB."
        )
        conn_tech = sqlite3.connect(tech_db)
        active_symbols = [
            r[0]
            for r in conn_tech.execute(
                "SELECT DISTINCT symbol FROM technical_data"
            ).fetchall()
        ]
        conn_tech.close()

    if not active_symbols:
        print("[!] No symbols found to scan.")
        return

    import numpy as np

    # 2. Get Trading Days from Calendar
    if not os.path.exists(calendar_csv):
        print(f"[!] Calendar missing at {calendar_csv}. Generating generic sequence...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)

        # PERFORMANCE GUARD FIX: F-string list comprehension replaces heavy .strftime() loop
        trading_days = [
            f"{d.year}-{d.month:02d}-{d.day:02d}"
            for d in pd.bdate_range(start=start_date, end=end_date)
        ]
    else:
        df_cal = pd.read_csv(calendar_csv)
        lookback_date = (
            (datetime.now() - timedelta(days=lookback_days)).date().isoformat()
        )
        trading_days = sorted(
            df_cal.loc[df_cal["date"] >= lookback_date, "date"].tolist()
        )

    trading_days_arr = np.array(trading_days)

    # 3. Get Empirical Bounds from Technical DB
    conn_tech = sqlite3.connect(tech_db)
    print("[*] Calculating empirical bounds for symbols...")
    bounds = pd.read_sql(
        "SELECT symbol, MIN(date) as first FROM technical_data GROUP BY symbol",
        conn_tech,
    )

    existing = pd.read_sql("SELECT symbol, date FROM technical_data", conn_tech)
    conn_tech.close()

    bounds_map = dict(zip(bounds["symbol"], bounds["first"]))
    existing_map = {}
    if not existing.empty:
        for sym, dates in existing.groupby("symbol")["date"]:
            existing_map[sym] = set(dates)

    # 4. Gap Detection Logic
    valid_days_cache = {}
    effective_starts = np.array(
        [bounds_map.get(sym, trading_days[0]) for sym in active_symbols]
    )
    unique_starts = np.unique(effective_starts)

    indices = np.searchsorted(trading_days_arr, unique_starts)
    for us, idx in zip(unique_starts, indices):
        valid_days_cache[us] = set(trading_days_arr[idx:])

    empty_set = set()
    missing_lists = [
        list(valid_days_cache[es] - existing_map.get(sym, empty_set))
        for sym, es in zip(active_symbols, effective_starts)
    ]

    df_missing_candidates = pd.DataFrame(
        {"symbol": active_symbols, "missing_date": missing_lists}
    )

    # Filter and Explode
    df_missing_candidates = df_missing_candidates[
        df_missing_candidates["missing_date"].str.len() > 0
    ]

    if not df_missing_candidates.empty:
        # PERFORMANCE GUARD FIX: Use .loc memory-safe slice instead of chained indexing df[][[]]
        df_missing = df_missing_candidates.explode("missing_date").loc[
            :, ["symbol", "missing_date"]
        ]

        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        df_missing.to_csv(output_csv, index=False)
        print(f"[+] Found {len(df_missing)} EMPIRICAL gaps (Missing Delivery Data).")
        print(f"[+] Missing list saved to {output_csv}")
    else:
        print("[+] SUCCESS: Database is fully populated with delivery data!")


if __name__ == "__main__":
    detect_missing_candles(lookback_days=730)
