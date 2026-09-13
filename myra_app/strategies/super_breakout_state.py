"""Super Breakout — persistent position state.

A single state table lives in the **meta** sidecar (``myra_metadata.db``),
alongside ``bhm1_cooldown`` / ``bhm1_positions`` — runtime scanner state
belongs with the general-purpose metadata sidecar, not with a strategy.

``sb_positions`` — WRITTEN by the scanner on live scans, READ for gap replay.
    One row per active position.  Tracks:
    - ``entry_date`` / ``entry_price``: the crossover day and close price.
    - ``ever_activated``: whether close >= entry * 1.02 has been reached at
      least once since entry.  When 0 the protective stop (close < SMA50)
      is live; when 1 the MA(50) trailing stop governs the exit.
    - ``highest_close``: highest close observed since entry (informational;
      the MA trail does not use it, but it supports diagnostics).

Persistence contract (mirrors BHM1's discipline)
-------------------------------------------------
- **Live scans (as_of == latest trading day)**: for each active position,
  replay the price window from entry_date to today through the shared exit
  evaluator (``_exit_ma_trailing``), seeded with the persisted
  ``ever_activated`` flag.  This ensures multi-day gaps are evaluated
  bar-by-bar (O3 — no endpoint-only shortcuts).  After replay, upsert the
  updated state; remove positions that exited.
- **Back-dated scans (as_of < latest)**: the scanner runs the full-history
  sweep unseeded and does NOT read from or write to this table.  Every
  signal is classified as a fresh entry.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

POSITIONS_TABLE = "sb_positions"

POSITIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {POSITIONS_TABLE} (
    symbol          TEXT NOT NULL PRIMARY KEY,
    entry_date      TEXT,
    entry_price     REAL,
    ever_activated  INTEGER DEFAULT 0,
    highest_close   REAL,
    updated_at      TEXT
)
"""


def meta_db_path() -> str:
    """Filesystem path of the meta sidecar (myra_metadata.db)."""
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])


def connect_meta(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection to the meta sidecar, ensuring the table exists."""
    path = db_path or meta_db_path()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(POSITIONS_DDL)
    conn.commit()
    return conn


def load_positions(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return {symbol: state} for every active position."""
    rows = conn.execute(
        f"SELECT symbol, entry_date, entry_price, ever_activated, "
        f"highest_close FROM {POSITIONS_TABLE}"
    ).fetchall()
    return {
        r[0]: {
            "entry_date": r[1],
            "entry_price": r[2],
            "ever_activated": bool(r[3]),
            "highest_close": r[4],
        }
        for r in rows
    }


def upsert_position(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    entry_date: str,
    entry_price: float,
    ever_activated: bool,
    highest_close: float,
    updated_at: str,
) -> None:
    """Create or update one position row."""
    conn.execute(
        f"INSERT INTO {POSITIONS_TABLE} "
        f"(symbol, entry_date, entry_price, ever_activated, highest_close, updated_at) "
        f"VALUES (?, ?, ?, ?, ?, ?) "
        f"ON CONFLICT(symbol) DO UPDATE SET "
        f"entry_date=excluded.entry_date, "
        f"entry_price=excluded.entry_price, "
        f"ever_activated=excluded.ever_activated, "
        f"highest_close=excluded.highest_close, "
        f"updated_at=excluded.updated_at",
        (symbol, entry_date, entry_price, int(ever_activated), highest_close, updated_at),
    )
    conn.commit()


def delete_position(conn: sqlite3.Connection, symbol: str) -> None:
    """Remove one position row (exited or manually closed)."""
    conn.execute(f"DELETE FROM {POSITIONS_TABLE} WHERE symbol = ?", (symbol,))
    conn.commit()


def delete_positions(conn: sqlite3.Connection, symbols: list[str]) -> int:
    """Remove multiple position rows in one commit."""
    if not symbols:
        return 0
    ph = ",".join("?" for _ in symbols)
    conn.execute(f"DELETE FROM {POSITIONS_TABLE} WHERE symbol IN ({ph})", symbols)
    conn.commit()
    return len(symbols)
