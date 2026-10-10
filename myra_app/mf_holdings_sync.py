"""Sync *complete* mutual-fund portfolios from RupeeVest into MYRA.

Why MYRA-native ingestion: the published traction artifact is generated with
``include_holds=false`` and silently drops every stock a fund merely holds
without change (``excluded_hold_count`` ~270 of ~1017 for 2026-09). Reading
RupeeVest's Portfolio Tracker directly keeps **every** held stock, and adds
fund AUM (Rs Cr) and the AMFI classification, so the Smart Money scanner can
rank the whole held universe rather than the reported subset.

Design:
  * one HTTP call per fund returns several months at once -> few, cheap calls;
  * writes are idempotent (DELETE+INSERT per fund/month, upsert elsewhere);
  * ``dry_run`` (the default) performs no network writes and no DDL;
  * per-fund failures are counted and surfaced, never hidden.

Tables live in ``myra_valuation.db`` next to the traction tables.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime

from myra_app.constants import DB_DIR
from myra_app.data_sources.rupeevest import (
    fetch_portfolio_tracker,
    load_index,
    parse_tracker,
)
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

# Plain-text watchlist; '.list' (not '.txt') because *.txt is git-excluded here.
DEFAULT_FUNDS_FILE = os.path.join(os.path.dirname(__file__), "mf_funds.list")
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_MAX_FAILURES = 3

_CREATE_MF_FUND = """
CREATE TABLE IF NOT EXISTS mf_fund (
    fund_slug    TEXT PRIMARY KEY,
    display_name TEXT,
    schemecode   TEXT,
    category     TEXT,
    updated_at   TEXT
)
"""

_CREATE_MF_HOLDING = """
CREATE TABLE IF NOT EXISTS mf_holding (
    fund_slug  TEXT NOT NULL,
    month      TEXT NOT NULL,          -- YYYY-MM
    fincode    TEXT NOT NULL,
    company    TEXT NOT NULL,
    weight_pct REAL,
    shares     REAL,
    PRIMARY KEY (fund_slug, month, fincode)
)
"""

_CREATE_MF_FUND_AUM = """
CREATE TABLE IF NOT EXISTS mf_fund_aum (
    fund_slug TEXT NOT NULL,
    month     TEXT NOT NULL,
    aum_cr    REAL,
    PRIMARY KEY (fund_slug, month)
)
"""

_CREATE_HOLDING_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mf_holding_month ON mf_holding (month)",
    "CREATE INDEX IF NOT EXISTS idx_mf_holding_fund ON mf_holding (fund_slug, month)",
]


def _db_path() -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])


def _ensure_tables(conn) -> None:
    conn.execute(_CREATE_MF_FUND)
    conn.execute(_CREATE_MF_HOLDING)
    conn.execute(_CREATE_MF_FUND_AUM)
    for stmt in _CREATE_HOLDING_INDEXES:
        conn.execute(stmt)
    conn.commit()


def load_fund_names(path: str | None = None) -> list[str]:
    """Read the fund watchlist (one RupeeVest name per line)."""
    path = path or DEFAULT_FUNDS_FILE
    names: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            names.append(line)
    return names


def _write_fund(conn, parsed: dict, now: str) -> None:
    slug = parsed["slug"]
    if not slug:
        return
    conn.execute(
        """INSERT INTO mf_fund (fund_slug, display_name, schemecode, category, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(fund_slug) DO UPDATE SET
               display_name=excluded.display_name,
               schemecode=excluded.schemecode,
               category=excluded.category,
               updated_at=excluded.updated_at""",
        (
            slug,
            parsed["fund_name"],
            parsed["schemecode"],
            parsed["category"],
            now,
        ),
    )


def _write_holdings(conn, parsed: dict) -> int:
    """Replace this fund's holdings per month. Returns rows written."""
    slug = parsed["slug"]
    if not slug:
        return 0
    months = set(parsed["months"])
    rows = 0
    # Clear only the months this payload carries; other months stay untouched.
    for month in months:
        conn.execute(
            "DELETE FROM mf_holding WHERE fund_slug = ? AND month = ?", (slug, month)
        )
    for h in parsed["holdings"]:
        if h["month"] not in months:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO mf_holding
               (fund_slug, month, fincode, company, weight_pct, shares)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                slug,
                h["month"],
                h["fincode"],
                h["company"],
                h["weight_pct"],
                h["shares"],
            ),
        )
        rows += 1
    for month, aum in parsed["aum_cr"].items():
        conn.execute(
            """INSERT INTO mf_fund_aum (fund_slug, month, aum_cr)
               VALUES (?, ?, ?)
               ON CONFLICT(fund_slug, month) DO UPDATE SET aum_cr=excluded.aum_cr""",
            (slug, month, aum),
        )
    return rows


def sync_mf_holdings(
    dry_run: bool = True,
    *,
    funds_file: str | None = None,
    delay: float = 1.0,
    max_failures: int = 3,
    limit: int | None = None,
    conn=None,
    index=None,
    fetch=None,
    sleep=None,
) -> dict:
    """Fetch every fund's full portfolio and upsert it into the valuation DB.

    ``dry_run=True`` (default) does no writes and no DDL -- it still performs the
    network reads so callers can validate coverage for free.

    ``index`` / ``fetch`` / ``sleep`` are injectable for tests.
    """
    result = {
        "success": False,
        "dry_run": dry_run,
        "funds_seen": 0,
        "funds_synced": 0,
        "holdings_rows": 0,
        "aum_rows": 0,
        "months": [],
        "failures": [],
        "error": None,
    }
    fetch = fetch or fetch_portfolio_tracker
    sleep = sleep or time.sleep
    owns_conn = conn is None and not dry_run
    if not dry_run and conn is None:
        conn = sqlite3.connect(_db_path())

    try:
        names = load_fund_names(funds_file) if funds_file else load_fund_names()
        if limit is not None:
            names = names[:limit]
        if index is None:
            index = load_index()

        if not dry_run:
            _ensure_tables(conn)

        months_seen: set[str] = set()
        now = _now_iso()
        for i, name in enumerate(names):
            result["funds_seen"] += 1
            resolved = index.resolve(name)
            if not resolved:
                result["failures"].append(
                    {"fund": name, "error": "not found on RupeeVest"}
                )
                continue
            canonical, schemecode = resolved
            try:
                payload = fetch(schemecode)
                parsed = parse_tracker(
                    payload, schemecode=schemecode, fund_name=canonical
                )
            except Exception as exc:  # noqa: BLE001 - per-fund isolation
                result["failures"].append({"fund": name, "error": str(exc)})
                continue

            if not parsed["slug"] or not parsed["holdings"]:
                result["failures"].append(
                    {"fund": name, "error": "empty holdings payload"}
                )
                continue

            months_seen.update(parsed["months"])
            if not dry_run:
                _write_fund(conn, parsed, now)
                result["holdings_rows"] += _write_holdings(conn, parsed)
                result["aum_rows"] += len(parsed["aum_cr"])
            else:
                result["holdings_rows"] += len(parsed["holdings"])
                result["aum_rows"] += len(parsed["aum_cr"])
            result["funds_synced"] += 1

            if delay and i < len(names) - 1:
                sleep(delay)

        if not dry_run and conn is not None:
            conn.commit()

        result["months"] = sorted(months_seen, reverse=True)
        if len(result["failures"]) > max_failures:
            result["error"] = (
                f"{len(result['failures'])} fund(s) failed "
                f"(tolerance {max_failures}): "
                + "; ".join(
                    f"{f['fund']}: {f['error']}" for f in result["failures"][:5]
                )
            )
            result["success"] = False
        else:
            result["success"] = True
        return result
    except Exception as exc:  # noqa: BLE001 - surfaced, never re-raised
        result["error"] = str(exc)
        return result
    finally:
        if owns_conn and conn is not None:
            conn.close()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def previous_month(today=None) -> str:
    """ISO ``YYYY-MM`` of the calendar month before *today* (default: today)."""
    today = today or datetime.now().date()
    if today.month > 1:
        return f"{today.year}-{today.month - 1:02d}"
    return f"{today.year - 1}-12"


def month_coverage(month: str, conn=None) -> int:
    """Distinct funds with holdings rows for *month* (0 when unavailable)."""
    owns = conn is None
    conn = conn or sqlite3.connect(_db_path())
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT fund_slug) FROM mf_holding WHERE month = ?",
            (month,),
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        if owns:
            conn.close()
