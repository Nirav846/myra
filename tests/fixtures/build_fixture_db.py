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
  * Per-db_key override rows (_DB_OVERRIDE_ROWS / _DB_OVERRIDE_UPDATES) are
    applied after the generic seeder, for shapes the generator cannot make
    (NULL shares_outstanding, _SME/base pairs, non-equity junk symbols).
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


# ---------------------------------------------------------------------------
# Per-db_key override rows, applied after the generic seeder.
#
# The generator cannot express the shapes below: _QUANTITY_NAMES forces
# shares_outstanding = i * 1000 (never NULL) and _SYMBOL_PREFIX forces
# TESTSYM%02d symbols, so neither the null-share nor the SME/base-pair case can
# be seeded.  They exist for tests/test_fundamentals_stale_filter.py.
#
# The staleness math is deliberate, so do not "fix" a row without re-reading
# that test file: the stale set must be exactly 28 rows / 25 distinct tickers
# (3 SME/base collisions), so the progress log lands on 25/25.
# ---------------------------------------------------------------------------

# Older / newer than the 90-day staleness window, independent of build date.
OLD_LFU = "2020-01-01"
FRESH_LFU = "2099-12-31"  # sentinel: always outside the staleness window


def _fundamentals_row(
    symbol: str,
    *,
    shares: float | None = None,
    lfu: str = OLD_LFU,
    source_ms: str = "MORNINGSTAR",
    pe: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    market_cap: float | None = None,
) -> dict:
    """One fundamentals override row (columns not listed stay NULL)."""
    return {
        "symbol": symbol,
        "shares_outstanding": shares,
        "last_fundamental_update": lfu,
        "source_ms": source_ms,
        "pe": pe,
        "sector": sector,
        "industry": industry,
        "market_cap": market_cap,
    }


_FUNDAMENTALS_OVERRIDES: list[dict] = [
    # -- non-equity junk (5): index pseudo-symbols with no Yahoo ticker -------
    # At least one row per predicate branch; "NIFTY JUNK SPACE" hits two.
    _fundamentals_row("FIX JUNK SPACE", source_ms="UPSTOX"),  # '% %'
    _fundamentals_row("FIX JUNK SPACE2", source_ms="UPSTOX"),  # '% %'
    _fundamentals_row("NIFTY JUNK SPACE", source_ms="UPSTOX"),  # '% %' + NIFTY%
    _fundamentals_row("NIFTYJUNK1", source_ms="UPSTOX"),  # NIFTY%, no space
    _fundamentals_row("NIFJUNK1", source_ms="UPSTOX"),  # NIF%, no space
    # -- protected UPSTOX equities (4) ---------------------------------------
    # Null share count, so stale by design -- but ordinary equities, and the
    # non-equity predicate must not remove them.
    _fundamentals_row(
        "ARTEMISMED",
        source_ms="UPSTOX",
        pe=31.5,
        sector="Healthcare",
        industry="Hospitals",
        market_cap=4.5e9,
    ),
    _fundamentals_row(
        "DHANI",
        source_ms="UPSTOX",
        pe=24.0,
        sector="Financial Services",
        industry="Brokerage",
        market_cap=2.1e9,
    ),
    _fundamentals_row(
        "GUJRAFFIA",
        source_ms="UPSTOX",
        pe=18.25,
        sector="Consumer Staples",
        industry="Cement",
        market_cap=6.0e9,
    ),
    _fundamentals_row(
        "SINGERIND",
        source_ms="UPSTOX",
        pe=42.75,
        sector="Consumer Durables",
        industry="Appliances",
        market_cap=1.4e9,
    ),
    # -- Case A: SME stale by date only, base already fresh --------------------
    # Only the SME row is selected, so dedupe must fall back to the _SME row.
    _fundamentals_row("FIXCASEA", shares=12_000_000, lfu=FRESH_LFU),
    _fundamentals_row("FIXCASEA_SME", shares=12_000_000, source_ms="UPSTOX"),
    # -- join path: base twin holds a valid Morningstar share count -----------
    _fundamentals_row("FIXJOIN1", shares=9_500_000),
    _fundamentals_row("FIXJOIN1_SME", source_ms="UPSTOX"),
    # -- Case C: both sides stale, base twin has NO share count ---------------
    # Nothing to copy locally: one fetch, written to the base row.
    _fundamentals_row("FIXJOINC", shares=None),
    _fundamentals_row("FIXJOINC_SME", source_ms="UPSTOX"),
    # -- deliberately divergent metrics (pe/sector/industry/market_cap) ------
    # The local join must copy shares_outstanding and nothing else.
    _fundamentals_row(
        "FIXDIVERGE",
        shares=3_300_000,
        pe=11.1,
        sector="Industrials",
        industry="Capital Goods",
        market_cap=1.1e9,
    ),
    _fundamentals_row(
        "FIXDIVERGE_SME",
        source_ms="UPSTOX",
        pe=99.9,
        sector="Energy",
        industry="Renewables",
        market_cap=2.2e9,
    ),
    # -- standalone base twins with no share count (Case C shape) ------------
    *[_fundamentals_row(f"FIXBASEC{i}") for i in range(1, 6)],
]
# -- standalone base twins that are already fresh: never selected -----------
_FUNDAMENTALS_OVERRIDES += [
    _fundamentals_row(f"FIXBASEB{i}", shares=1_000_000 * i, lfu=FRESH_LFU)
    for i in range(1, 6)
]
# -- filler _SME rows (10 _SME rows in total) --------------------------------
# Deliberately NOT stale, so they add no fetches and keep the stale set at
# exactly 25 distinct tickers.
_FUNDAMENTALS_OVERRIDES += [
    _fundamentals_row(
        f"FIXSMEM{i:02d}_SME", shares=2_000_000 * i, lfu=FRESH_LFU, source_ms="UPSTOX"
    )
    for i in range(1, 7)
]

# Rows the generic seeder cannot be asked to make stale (it writes a
# non-date-shaped last_fundamental_update), so they are aged by UPDATE.
_DB_OVERRIDE_UPDATES: dict[str, list[tuple[str, tuple]]] = {
    "valuation": [
        # The 12 ordinary equities every other test relies on must be *in* the
        # stale set, otherwise "the filter kept them" proves nothing.
        (
            "UPDATE fundamentals SET last_fundamental_update = ? "
            "WHERE symbol LIKE 'TESTSYM%'",
            (OLD_LFU,),
        ),
    ],
}

_DB_OVERRIDE_ROWS: dict[str, dict[str, list[dict]]] = {
    "valuation": {"fundamentals": _FUNDAMENTALS_OVERRIDES},
}


def _apply_overrides(conn: sqlite3.Connection, db_key: str) -> tuple[int, int]:
    """Apply this db_key's override UPDATEs + INSERT rows.  Returns both counts."""
    rows_added = 0
    for sql, params in _DB_OVERRIDE_UPDATES.get(db_key, ()):
        conn.execute(sql, params)
    for table, rows in _DB_OVERRIDE_ROWS.get(db_key, {}).items():
        if not rows:
            continue
        cols = list(rows[0])
        placeholders = ", ".join("?" for _ in cols)
        sql = f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({placeholders})'
        conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        rows_added += len(rows)
    return rows_added, len(_DB_OVERRIDE_UPDATES.get(db_key, ()))


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
        added, aged = _apply_overrides(conn, db_key)
        conn.commit()
    finally:
        conn.close()

    print(
        f"[{db_key}] {os.path.basename(dest)}: seeded {len(seeded)} tables "
        f"({', '.join(seeded) or '-'}) | skipped {len(skipped)} "
        f"({', '.join(skipped) or '-'})"
        + (
            f" | +{added} override rows, {aged} override updates"
            if added or aged
            else ""
        )
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
