"""Bottom Hunter M1 — persistent state tables (D2).

Two small state tables live in the **meta** sidecar (``myra_metadata.db``),
alongside ``symbols_master`` / ``index_constituents`` — runtime scanner state
belongs with the general-purpose metadata sidecar, not with a strategy.

Roles
-----
``bhm1_cooldown``  — WRITTEN by the scanner.  One row per symbol that is
    currently inside a cooldown cycle.  ``floor_year_low`` is ``year_low``
    on the signal day; a cycle only restarts when the running minimum of
    ``year_low`` drops *strictly* below that floor (a fresh 52-week low).
    ``run_min_year_low`` is the running minimum of ``year_low`` observed
    since the signal, persisted so the floor comparison survives windows
    that no longer cover the signal day (>252 trading days later).  A row
    whose ``signal_date`` is NULL means "not in cooldown" (cycle expired).

``bhm1_positions``  — READ by the scanner, maintained by the user (or a
    future management API).  One row per held symbol.  ``n_tranches`` /
    ``blended_basis`` are inputs to the ADD-classification: a fresh
    crossing on a held symbol surfaces as ``signal_type="ADD"`` exactly when
    ``n_tranches < max_tranches`` (D4 option 2 — the scanner surfaces the
    add-to-position signal; it does NOT open, close, or simulate positions).

Persistence contract (read carefully — this is the scanner's PIT discipline)
---------------------------------------------------------------------------
- **Live scans (as_of == latest trading day in the data)**: seed the
  crossing sweep with the persisted cooldown rows, then persist the final
  cooldown state back into the table.  This is the ONLY mode that writes.
- **Back-dated scans (as_of < latest)**: the sweep starts UNSEEDED (fresh,
  ``floor = +inf``), exactly like the backtest's ``_precompute_kaushik_events``,
  so the output reproduces the backtest's event calendar for that window
  point-in-time.  Nothing is read from or written to these tables, and every
  crossing is classified ``signal_type="NEW"`` (no PIT position store exists
  in v1 — ADD classification is a live-tool concept).
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

# Column layouts mirror the SchemaRegistry entries (single source of truth
# for schema; these DDLs exist so the scanner can create its own tables
# without depending on the registry being run first).
COOLDOWN_TABLE = "bhm1_cooldown"
POSITIONS_TABLE = "bhm1_positions"

COOLDOWN_DDL = f"""
CREATE TABLE IF NOT EXISTS {COOLDOWN_TABLE} (
    symbol          TEXT NOT NULL PRIMARY KEY,
    signal_date     TEXT,
    floor_year_low  REAL,
    run_min_year_low REAL,
    updated_at      TEXT
)
"""

POSITIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {POSITIONS_TABLE} (
    symbol             TEXT NOT NULL PRIMARY KEY,
    first_entry_date   TEXT,
    last_tranche_date  TEXT,
    n_tranches         INTEGER,
    blended_basis      REAL,
    updated_at         TEXT
)
"""


def meta_db_path() -> str:
    """Filesystem path of the meta sidecar (myra_metadata.db)."""
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])


def connect_meta(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection to the meta sidecar, ensuring both tables exist."""
    path = db_path or meta_db_path()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(COOLDOWN_DDL)
    conn.execute(POSITIONS_DDL)
    conn.commit()
    return conn


# ── cooldown ─────────────────────────────────────────────────────────────


def load_cooldown(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return {symbol: state} for every symbol currently in cooldown."""
    rows = conn.execute(
        f"SELECT symbol, signal_date, floor_year_low, run_min_year_low "
        f"FROM {COOLDOWN_TABLE} "
        f"WHERE signal_date IS NOT NULL"
    ).fetchall()
    return {r[0]: {"signal_date": r[1], "floor": r[2], "run_min": r[3]} for r in rows}


def save_cooldown(
    conn: sqlite3.Connection, state: dict[str, dict], updated_at: str
) -> int:
    """Upsert live cooldown rows produced by a scan ending at *updated_at*.

    ``state`` maps symbol → {signal_date, floor, run_min} for symbols that
    are IN cooldown at the end of the scan.  Symbols previously in cooldown
    whose cycle expired are removed from ``state`` by the scanner and must be
    cleared via :func:`clear_cooldown` (the row is kept with a NULL
    ``signal_date`` so the schema stays consistent, not because history is
    retained — this table is live state, not a PIT store).
    """
    n = 0
    params = [
        (
            symbol,
            s.get("signal_date"),
            s.get("floor"),
            s.get("run_min"),
            updated_at,
        )
        for symbol, s in state.items()
    ]
    if params:
        conn.executemany(
            f"INSERT INTO {COOLDOWN_TABLE} "
            f"(symbol, signal_date, floor_year_low, run_min_year_low, updated_at) "
            f"VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(symbol) DO UPDATE SET "
            f"signal_date=excluded.signal_date, "
            f"floor_year_low=excluded.floor_year_low, "
            f"run_min_year_low=excluded.run_min_year_low, "
            f"updated_at=excluded.updated_at",
            params,
        )
        n = len(params)
    conn.commit()
    return n


def clear_cooldown(
    conn: sqlite3.Connection, symbols: list[str], updated_at: str
) -> int:
    """Mark the given symbols as NOT in cooldown (signal_date → NULL).

    Called after a scan whose sweep ended those symbols' cycles (a fresh
    52-week low below the floor).  The scanner passes every symbol whose
    persisted row no longer represents the final in-cooldown state.
    """
    if not symbols:
        return 0
    ph = ",".join("?" for _ in symbols)
    conn.execute(
        f"UPDATE {COOLDOWN_TABLE} SET signal_date=NULL, "
        f"run_min_year_low=NULL, updated_at=? WHERE symbol IN ({ph})",
        (updated_at, *symbols),
    )
    conn.commit()
    return len(symbols)


# ── positions ────────────────────────────────────────────────────────────


def load_positions(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return {symbol: position} for the user's currently held positions."""
    rows = conn.execute(
        f"SELECT symbol, first_entry_date, last_tranche_date, n_tranches, "
        f"blended_basis FROM {POSITIONS_TABLE}"
    ).fetchall()
    return {
        r[0]: {
            "first_entry_date": r[1],
            "last_tranche_date": r[2],
            "n_tranches": r[3] or 0,
            "blended_basis": r[4],
        }
        for r in rows
        if r[1] is not None
    }


def upsert_position(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    first_entry_date: str,
    last_tranche_date: str,
    n_tranches: int,
    blended_basis: Optional[float],
    updated_at: str,
) -> None:
    """Create/replace one position row (user-side bookkeeping helper)."""
    conn.execute(
        f"INSERT INTO {POSITIONS_TABLE} "
        f"(symbol, first_entry_date, last_tranche_date, n_tranches, "
        f"blended_basis, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(symbol) DO UPDATE SET "
        f"first_entry_date=excluded.first_entry_date, "
        f"last_tranche_date=excluded.last_tranche_date, "
        f"n_tranches=excluded.n_tranches, "
        f"blended_basis=excluded.blended_basis, "
        f"updated_at=excluded.updated_at",
        (
            symbol,
            first_entry_date,
            last_tranche_date,
            n_tranches,
            blended_basis,
            updated_at,
        ),
    )
    conn.commit()


def delete_position(conn: sqlite3.Connection, symbol: str) -> None:
    """Remove one position row (user-side bookkeeping helper)."""
    conn.execute(f"DELETE FROM {POSITIONS_TABLE} WHERE symbol = ?", (symbol,))
    conn.commit()
