"""Shared utilities for mutual-fund holding universe filtering."""

import logging
import os
import sqlite3
from typing import Optional, Set

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

# Module-level cache: resolved_month → frozenset of symbols.
# Cleared automatically when the latest month changes (key mismatch).
_cache: dict[str, Set[str]] = {}


def get_holding_symbols(month: Optional[str] = None) -> Set[str]:
    """Return the set of symbols held by at least one mutual fund.

    Parameters
    ----------
    month : str, optional
        Filter to a specific YYYY-MM month. When *None*, the latest
        month present in ``fund_traction`` is used automatically.

    Returns
    -------
    set[str]
        Unique uppercase symbols. Empty set on any error or when no
        data exists yet.
    """
    val_db = os.path.join(DB_DIR, "myra_valuation.db")
    if not os.path.exists(val_db):
        logger.warning("holdings: myra_valuation.db missing")
        return set()

    conn = sqlite3.connect(val_db)
    try:
        # ── resolve latest month ──────────────────────────────────────
        if month is None:
            row = conn.execute("SELECT MAX(month) FROM fund_traction").fetchone()
            if not row or not row[0]:
                logger.warning("holdings: fund_traction is empty")
                return set()
            month = row[0]

        # ── check cache (re-use if the same month is requested) ───────
        cached = _cache.get(month)
        if cached is not None:
            return cached

        # ── query ─────────────────────────────────────────────────────
        rows = conn.execute(
            "SELECT symbol FROM fund_traction WHERE month = ?", (month,)
        ).fetchall()

        symbols: Set[str] = {r[0].strip() for r in rows if r and r[0]}

        if not symbols:
            logger.warning("holdings: no rows for month %s", month)

        # Store with a new key so old-month caches don't linger
        _cache.clear()
        _cache[month] = symbols
        return symbols

    finally:
        conn.close()


def clear_holding_symbols_cache() -> None:
    """Invalidate the in-process holding cache (for tests or explicit refresh)."""
    _cache.clear()
