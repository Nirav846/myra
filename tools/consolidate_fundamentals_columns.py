"""One-time migration: consolidate duplicate column pairs in fundamentals.

For each canonical (snake_case) column that has NULL/0, copy the value from
its camelCase alias column if the alias has a non-zero value.  Idempotent.

Safe to re-run at any time.  Does NOT drop the alias columns.
"""

import sqlite3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402


DUPLICATE_PAIRS = [
    ("market_cap", "marketCap"),
    ("pe", "peRatio"),
    ("roe", "returnOnEquity"),
    ("eps", "earningsPerShare"),
    ("book_value", "bookValuePerShare"),
    ("debt_to_equity", "debtToEquity"),
    ("dividend_yield", "dividendYield"),
    ("sales_growth", "revenueGrowth"),
    ("profit_growth", "earningsGrowth"),
    ("net_margin", "netMargin"),
]


def main() -> None:
    db_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if not os.path.exists(db_path):
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    total_rows = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
    print(f"[consolidate] Database: {db_path}")
    print(f"[consolidate] Total rows: {total_rows}")
    print()

    # Get existing columns to skip non-existent alias columns
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(fundamentals)")}
    print(f"[consolidate] Existing columns: {len(existing_cols)}")

    total_updates = 0
    for canonical, alias in DUPLICATE_PAIRS:
        if alias not in existing_cols:
            print(
                f"[consolidate]  SKIP {alias} → {canonical}: alias column does not exist"
            )
            continue
        if canonical not in existing_cols:
            print(
                f"[consolidate]  SKIP {alias} → {canonical}: canonical column does not exist"
            )
            continue

        try:
            cur = conn.execute(  # noqa: PG-NPLUS1
                f"UPDATE fundamentals SET {canonical} = {alias} "
                f"WHERE ({canonical} IS NULL OR {canonical} = 0) "
                f"AND {alias} IS NOT NULL AND {alias} != 0"
            )
            updated = cur.rowcount
            conn.commit()
            total_updates += updated
            if updated > 0:
                print(
                    f"[consolidate]  {alias:30s} -> {canonical:20s}: {updated:5d} rows updated"
                )
            else:
                print(f"[consolidate]  {alias:30s} -> {canonical:20s}: no rows needed")
        except Exception as e:
            conn.rollback()
            print(f"[consolidate]  ERROR {alias} -> {canonical}: {e}")

    print()
    print(f"[consolidate] TOTAL rows updated: {total_updates}")
    conn.close()


if __name__ == "__main__":
    main()
