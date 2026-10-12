"""Source-ownership rules for the ``fundamentals`` table.

Two invariants live here:

1. **Nothing silently erases a good metric.** (Enforced primarily by
   ``safe_write``'s fill-only/COALESCE policy; this module never writes nulls.)
2. **A lower-quality source may not overwrite a better one while the better one
   is healthy.** Screener.in is the authoritative source for the core
   fundamentals (P/E, ROE, book value, market cap, sector, shareholding), so an
   enrichment pass from yfinance/NSE/Morningstar must not clobber it.

Why *per-symbol freshness* rather than a single "is Screener down?" switch:

    A global kill-switch is dangerous. The moment the timer expires, every
    other source would be allowed to overwrite every Screener value at once --
    a mass, silent quality regression. Instead we track the last *successful*
    fetch per (symbol, source) and let a field be taken over only when the
    owning source has not refreshed *that symbol* within the takeover window.
    A global outage still results in a takeover, but staged by each symbol's
    own last-success time, so it degrades gracefully and self-heals the moment
    Screener answers again.

Why 60 days by default: Screener data is quarterly. One quarter plus a
reporting lag is ~60 days, so a claim older than that is either genuinely dead
or no longer "authoritative". It is deliberately generous: a short window would
hand the whole table to the weaker sources during a routine two-week outage.
Operators can tune it with ``MYRA_SCREENER_TAKEOVER_DAYS``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

from myra_app import constants
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

#: Canonical Screener source name (matches the DataFetcher stream name).
SCREENER_SOURCE = "screener_in"

#: Higher wins. Unknown sources land on :data:`DEFAULT_PRIORITY`.
SOURCE_PRIORITY = {
    "screener_in": 100,
    "screener": 100,
    "upstox": 90,
    "nse_shareholding": 80,
    "nse": 70,
    "morningstar": 60,
    "finology": 50,
    "yahoo": 40,
    "yfinance": 40,
    "google": 30,
    "google_finance": 30,
}
DEFAULT_PRIORITY = 50

#: ``fundamentals`` columns Screener.in is authoritative for. A lower-priority
#: writer may not touch these while Screener's claim on the symbol is fresh.
#: Deliberately excludes fields Screener does not supply (shares_outstanding,
#: free_float_shares, volatility, ...) so those owners are never blocked.
SCREENER_OWNED_FIELDS = frozenset(
    {
        "pe",
        "roe",
        "eps",
        "book_value",
        "market_cap",
        "sector",
        "promoter_holding_pct",
        "public_holding_pct",
        "free_float_pct",
        "free_float_market_cap",
        "inst_holding",
        "pledged_pct",
    }
)

TABLE = "fundamental_source_freshness"


def takeover_days() -> int:
    try:
        return max(1, int(os.environ.get("MYRA_SCREENER_TAKEOVER_DAYS", "60")))
    except (TypeError, ValueError):
        return 60


def get_priority(source: Optional[str]) -> int:
    if not source:
        return DEFAULT_PRIORITY
    return SOURCE_PRIORITY.get(str(source).strip().lower(), DEFAULT_PRIORITY)


def _val_path(db_path: Optional[str] = None) -> str:
    return db_path or os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["valuation"])


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            symbol        TEXT NOT NULL,
            source        TEXT NOT NULL,
            last_success_at TEXT,
            PRIMARY KEY (symbol, source)
        )
        """
    )


def _clean_symbol(symbol: str) -> str:
    return str(symbol).split(".")[0].strip().upper()


def record_source_success(symbol: str, source: str, *, conn=None) -> None:
    """Stamp that ``source`` just successfully refreshed ``symbol``.

    Best-effort: freshness is advisory bookkeeping and must never abort a run.
    """
    if not symbol or not source:
        return
    own = conn is None
    try:
        if own:
            conn = sqlite3.connect(_val_path(), timeout=15)
            conn.execute("PRAGMA journal_mode=WAL")
        ensure_table(conn)
        conn.execute(
            f"INSERT INTO {TABLE} (symbol, source, last_success_at) VALUES (?, ?, ?) "
            f"ON CONFLICT(symbol, source) DO UPDATE SET "
            f"last_success_at = excluded.last_success_at",
            (_clean_symbol(symbol), str(source), _utcnow_iso()),
        )
        if own:
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("could not record source freshness for %s: %s", symbol, exc)
    finally:
        if own and conn is not None:
            conn.close()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def screener_claim_age_days(symbol: str, *, conn=None) -> Optional[float]:
    """Age in days of Screener's last success for ``symbol`` (None if never)."""
    own = conn is None
    try:
        if own:
            conn = sqlite3.connect(_val_path(), timeout=15)
            conn.execute("PRAGMA journal_mode=WAL")
        ensure_table(conn)
        row = conn.execute(
            f"SELECT MAX(last_success_at) FROM {TABLE} "
            f"WHERE symbol = ? AND source IN (?, ?)",
            (_clean_symbol(symbol), "screener_in", "screener"),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if own and conn is not None:
            conn.close()
    if not row or not row[0]:
        return None
    parsed = _parse(row[0])
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0


def screener_claim_is_fresh(symbol: str, *, conn=None, now=None) -> bool:
    """True while Screener's claim on ``symbol`` is within the takeover window."""
    age = screener_claim_age_days(symbol, conn=conn)
    return age is not None and age < takeover_days()


def screener_fresh_symbols(symbols: Iterable[str], *, conn=None, now=None) -> set[str]:
    """The subset of ``symbols`` whose Screener.in claim is still fresh.

    One connection for the whole list — a per-symbol :func:`screener_claim_is_fresh`
    would be ~3k connect cycles on a full run. Returns the *original* input
    strings that are fresh (so a caller can filter its own list directly, even if
    it mixes casing). Callers use this to skip an expensive per-symbol fetch for
    data Screener.in already owns and would not be overwritten anyway.
    """
    by_clean: dict[str, list[str]] = {}
    for symbol in symbols:
        if not symbol:
            continue
        # Grouping into a dict-of-lists (bounded by distinct symbols, each list
        # tiny) — not the accumulating-list anti-pattern the guard targets.
        by_clean.setdefault(_clean_symbol(symbol), []).append(symbol)  # noqa: PG-APPEND
    if not by_clean:
        return set()

    own = conn is None
    try:
        if own:
            conn = sqlite3.connect(_val_path(), timeout=15)
            conn.execute("PRAGMA journal_mode=WAL")
        ensure_table(conn)
        placeholders = ",".join("?" * len(by_clean))
        rows = conn.execute(
            f"SELECT symbol, MAX(last_success_at) FROM {TABLE} "
            f"WHERE source IN (?, ?) AND symbol IN ({placeholders}) "
            f"GROUP BY symbol",
            ("screener_in", "screener", *by_clean.keys()),
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        if own and conn is not None:
            conn.close()

    cutoff = takeover_days()
    now_dt = now or datetime.now(timezone.utc)
    fresh: set[str] = set()
    for symbol, ts in rows:
        parsed = _parse(ts) if ts else None
        if parsed is None:
            continue
        if (now_dt - parsed).total_seconds() / 86400.0 < cutoff:
            fresh.update(by_clean.get(symbol, []))
    return fresh


def filter_owned_fields(
    symbol: str, writing_source: str, values: dict, *, conn=None
) -> dict:
    """Drop fields a fresher, higher-priority source owns.

    A no-op for Screener itself, for unknown symbols (no claim to protect), and
    once the claim has gone stale (the takeover the operator asked for).
    """
    if not values:
        return values
    if get_priority(writing_source) >= get_priority(SCREENER_SOURCE):
        return values
    if not screener_claim_is_fresh(symbol, conn=conn):
        return values
    return {k: v for k, v in values.items() if k not in SCREENER_OWNED_FIELDS}


def filter_writes(symbol: str, field_sources: dict, *, conn=None) -> dict:
    """Like :func:`filter_owned_fields` but per-field.

    ``field_sources`` maps a column name to the source that produced it, so a
    multi-source batch (e.g. yfinance market cap + NSE promoter holding) can have
    individual fields vetoed. Returns the sub-dict of fields that may be written.
    """
    if not field_sources:
        return field_sources
    fresh = screener_claim_is_fresh(symbol, conn=conn)
    if not fresh:
        return dict(field_sources)
    screener_pri = get_priority(SCREENER_SOURCE)
    return {
        field: src
        for field, src in field_sources.items()
        if not (field in SCREENER_OWNED_FIELDS and get_priority(src) < screener_pri)
    }


def describe(symbol: str, *, conn=None) -> dict:
    """Small diagnostic payload for logs/health surfaces."""
    age = screener_claim_age_days(symbol, conn=conn)
    return {
        "symbol": _clean_symbol(symbol),
        "screener_claim_age_days": age,
        "takeover_days": takeover_days(),
        "screener_owned": age is not None and age < takeover_days(),
    }
