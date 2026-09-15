"""Build synthetic fixture DBs for the test suite.

Reads schema/<db_key>.sql for the 9 registered DB_MAP databases, applies the
real DDL to tests/fixtures/<db_key>_fixture.db, then seeds clearly fake rows
(10-20 per table) so scanners/endpoints/tests can run without touching the
live myra_app/db/*.db files.

Skipped for seeding (DDL still applied) -- see schema/registry_mismatch_report.md:
  * stale/parked tables (historical snapshots, parked caches)
  * empty/dead tables (network_cache.cache)
  * external upstox tables (written by out-of-repo upstox_fetcher)

Design notes:
  * schema/*.sql statements are blank-line separated and carry NO trailing
    semicolons (sqlite_master stores them that way) -- we append ';'.
  * There are no FOREIGN KEY clauses anywhere in the DDL, so only per-table
    PRIMARY KEY / NOT NULL distinctness matters.
  * Deterministic generation: every row's valued columns are unique per row,
    so any composite PK is satisfied.
"""

from __future__ import annotations

import os
import sqlite3
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import myra_app.constants as constants
from myra_app.librarian_core import LibrarianCore

FIXTURES_DIR = os.path.join(constants.PROJECT_ROOT, "tests", "fixtures")
SCHEMA_DIR = os.path.join(constants.PROJECT_ROOT, "schema")

# Tables that exist on disk but should NOT be seeded (DDL still applied).
NO_SEED = {
    "institutional": {
        "block_deals_historical",
        "bulk_deals_historical",
    },
    "meta": {
        "index_membership_history",
    },
    "valuation": {
        "full_fundamental_cache",
        "screener_fundamentals",
        "upstox_balance_sheet",
        "upstox_cache",
        "upstox_cash_flow",
        "upstox_competitors",
        "upstox_corporate_actions",
        "upstox_income_statement",
    },
    "network_cache": {
        "cache",
    },
}

ROWS_PER_TABLE = 12

_SYMBOL_PREFIX = "TESTSYM"
_FLAG_NAMES = {
    "is_trading_day",
    "is_current",
    "is_active",
    "is_etf",
}
_QUANTITY_NAMES = {
    "delivery",
    "net_qty",
    "qty",
    "shares",
    "shares_outstanding",
    "volume",
}


def _split_ddl(ddl: str) -> list[str]:
    """Split blank-line-separated DDL into standalone statements with ';'."""
    statements = []
    for chunk in ddl.split("\n\n"):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("--"):
            continue
        statements.append(chunk + ";")
    return statements


def _gen_value(name: str, typ: str, i: int) -> object:
    """Deterministic fake value for one cell (distinct across rows)."""
    low = name.lower()
    up = typ.upper()

    if up.startswith("BLOB"):
        return b"fixture"
    if up.startswith(("CHAR", "TEXT", "CLOB")):
        if low == "symbol" or low.endswith("_symbol"):
            return f"{_SYMBOL_PREFIX}{i:02d}"
        if low == "isin":
            return f"INEFIX{i:05d}"
        if "time" in low:
            return f"2025-01-{i:02d} 09:15:00"
        if "date" in low or low in {"month_end", "period"}:
            return f"2025-01-{i:02d}"
        if low == "month":
            return f"2025-{i % 12 + 1:02d}"
        if low in {"buy_sell"}:
            return "BUY"
        return f"FIX-{low}-{i:02d}"

    if up.startswith(("INT", "BOOL")):
        if low in _FLAG_NAMES:
            return i % 2
        if low in _QUANTITY_NAMES:
            return i * 1000
        return i

    if up.startswith(("REAL", "NUM", "DEC", "FLO", "DOUB")):
        if "price" in low or low in {
            "book_value",
            "book_value_per_share",
            "close",
            "eps",
            "face_value",
            "high",
            "low",
            "market_cap",
            "open",
            "pe",
            "sma_50",
            "swing_high",
            "swing_low",
            "vwap",
        }:
            return round(i * 100 + 5.25, 2)
        return round(i + i * 0.1, 2)

    return f"FIX-{low}-{i:02d}"


def _seed_table(conn: sqlite3.Connection, table: str) -> None:
    cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    col_names = [c[1] for c in cols]
    col_types = [c[2] for c in cols]
    placeholders = ", ".join("?" for _ in col_names)
    sql = f'INSERT INTO "{table}" ({", ".join(col_names)}) VALUES ({placeholders})'
    rows = [
        tuple(_gen_value(n, t, i) for n, t in zip(col_names, col_types))
        for i in range(1, ROWS_PER_TABLE + 1)
    ]
    conn.executemany(sql, rows)


def build_one(db_key: str) -> str:
    ddl_path = os.path.join(SCHEMA_DIR, f"{db_key}.sql")
    if not os.path.exists(ddl_path):
        raise FileNotFoundError(f"missing DDL: {ddl_path}")
    with open(ddl_path, encoding="utf-8") as fh:
        ddl = fh.read()

    dest = os.path.join(FIXTURES_DIR, f"{db_key}_fixture.db")
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    if os.path.exists(dest):
        os.remove(dest)

    conn = sqlite3.connect(dest)
    try:
        conn.executescript("\n".join(_split_ddl(ddl)))
        seeded, skipped = [], []
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            if table in NO_SEED.get(db_key, ()):
                skipped.append(table)
                continue
            _seed_table(conn, table)
            seeded.append(table)
        conn.commit()
    finally:
        conn.close()

    print(
        f"[{db_key}] {os.path.basename(dest)}: seeded {len(seeded)} tables "
        f"({', '.join(seeded) or '-'}) | skipped {len(skipped)} "
        f"({', '.join(skipped) or '-'})"
    )
    return dest


def main() -> None:
    built = []
    for db_key in LibrarianCore.DB_MAP:
        built.append(build_one(db_key))
    print(f"\nBuilt {len(built)} fixture DBs in {FIXTURES_DIR}")
    for path in built:
        print(f"  {path}")


if __name__ == "__main__":
    sys.exit(main())
