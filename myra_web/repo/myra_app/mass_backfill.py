import os
import pandas as pd
import sqlite3
import yfinance as yf
from datetime import datetime, timedelta
import time
from myra_core.utils.myra_log import myra_log
from myra_app.librarian import Librarian


def mass_backfill(db_path="technical.db", missing_csv="missing_data.csv"):
    """
    Massive backfill for all symbols in the database.
    Optimized for high-volume without getting blocked.
    """
    print("[MYRA] Initializing Mass Market Backfill (3800 Stocks)...")

    lib = Librarian(read_only=True)
    all_symbols = lib.get_all_symbols()

    if not os.path.exists(missing_csv):
        print("[!] missing_data.csv not found. Please run missing_detector.py first.")
        return

    df_missing = pd.read_csv(missing_csv)
    # Only focus on symbols we have in our master list
    df_missing = df_missing[df_missing["symbol"].isin(all_symbols)]

    grouped = df_missing.groupby("symbol")
    symbols_to_fix = list(grouped.groups.keys())

    print(f"[*] Found {len(symbols_to_fix)} symbols requiring backfill.")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    stats = {"processed": 0, "rows": 0, "errors": 0}

    # Batch parameters
    batch_size = 50
    for i in range(0, len(symbols_to_fix), batch_size):
        batch = symbols_to_fix[i : i + batch_size]
        print(f"\n[Batch {i//batch_size + 1}] Processing {len(batch)} symbols...")

        total_batch = len(batch)
        batch_records = []
        for idx, symbol in enumerate(batch, 1):
            myra_log(idx, total_batch, desc="Backfilling")
            try:
                yf_sym = f"{symbol}.NS"
                sym_gaps = grouped.get_group(symbol)

                # Fetch only necessary range
                min_date = sym_gaps["missing_date"].min()
                # Performance Guard Compliant (Fix 51)
                max_dt = datetime.strptime(
                    sym_gaps["missing_date"].max(), "%Y-%m-%d"
                ) + timedelta(days=1)
                max_date = max_dt.date().isoformat()

                data = yf.download(
                    yf_sym, start=min_date, end=max_date, progress=False, interval="1d"
                )

                if data.empty:
                    continue

                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data.reset_index(inplace=True)

                # Optimized with list comprehension (Fix 63, 64, 66: Avoid .append in loop)
                gap_dates = set(sym_gaps["missing_date"])

                def _to_record(row):
                    d_str = row.Date.date().isoformat()
                    if d_str in gap_dates:
                        return (
                            symbol,
                            d_str,
                            float(row.Open),
                            float(row.High),
                            float(row.Low),
                            float(row.Close),
                            int(row.Volume),
                            None,
                            None,
                            float(row.Close),
                            None,
                            None,
                        )
                    return None

                records = [
                    r for row in data.itertuples(index=False) if (r := _to_record(row))
                ]

                if records:
                    batch_records.extend(records)

                stats["processed"] += 1
                time.sleep(0.2)  # Micro-throttle

            except Exception:
                stats["errors"] += 1

        # PERFORMANCE GUARD: Batched database insertion outside symbol loop
        if batch_records:
            cursor.executemany(
                """
                INSERT OR IGNORE INTO technical_data
                (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                batch_records,
            )
            conn.commit()
            stats["rows"] += len(batch_records)

        print(f"Batch Complete. Total Rows Added: {stats['rows']}")
        print("Pausing for 5s to avoid rate limits...")
        time.sleep(5)

    conn.close()
    print("\n" + "=" * 30)
    print("MASS BACKFILL COMPLETE")
    print("=" * 30)
    print(f"Symbols Processed: {stats['processed']}")
    print(f"Rows Added:        {stats['rows']}")
    print(f"Errors:            {stats['errors']}")
    print("=" * 30)


if __name__ == "__main__":
    mass_backfill()
