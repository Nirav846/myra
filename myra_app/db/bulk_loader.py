"""Bulk OHLCV loader for scanners.

Replaces the per-symbol ``sqlite3.connect()`` + indexed SELECT pattern
inside each scanner's ``_get_tech_data`` (measured ~31 ms/symbol → ~83 s
of pure DB I/O per full scan) with ONE bulk query per scan run.

Usage (inside a scanner's ``scan()``)::

    self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

Then ``_get_tech_data`` consults ``self._bulk_data`` first and only falls
back to the DB when the symbol is not present in the bulk window.
"""

from __future__ import annotations

import math
import os
import sqlite3
from datetime import date
from typing import Dict

import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

# Superset of columns consumed by all scanners (12-col base, 13-col
# wyckoff adds swing_low, 8-col climax/dcb use the first eight).
TECH_COLUMNS: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
    "swing_low",
)

# Column sets returned by each scanner family (must match the scanner's
# original SELECT column order so downstream pd.DataFrame(...) construction
# is byte-for-byte identical).
COLUMNS_12: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
)
COLUMNS_13: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "swing_low",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
)
COLUMNS_8: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
)


def _tech_db_path() -> str:
    """Return the filesystem path of the technical_data sidecar DB."""
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])


def _select_columns(conn) -> list[str]:
    """Return the subset of TECH_COLUMNS present in the live schema."""
    try:
        rows = conn.execute("PRAGMA table_info(technical_data)").fetchall()
    except sqlite3.OperationalError:
        return []
    existing = {r[1] for r in rows}
    return [c for c in TECH_COLUMNS if c in existing]


def load_ohlcv_for_universe(
    start_date: str, end_date: str | None = None
) -> Dict[str, pd.DataFrame]:
    """Load OHLCV + delivery rows for ALL symbols in one query.

    Returns ``{symbol: DataFrame}`` with each frame ordered by ``date``
    ascending and containing the columns that exist in the live schema
    (subset of ``TECH_COLUMNS``).

    ``end_date`` defaults to today when omitted.
    """
    end_date = end_date or date.today().isoformat()
    tech_db = _tech_db_path()
    if not os.path.exists(tech_db):
        return {}

    with sqlite3.connect(tech_db) as conn:
        cols = _select_columns(conn)
        if not cols:
            return {}
        sql = (
            "SELECT symbol, " + ", ".join(cols) + " FROM technical_data "
            "WHERE date BETWEEN ? AND ?"
        )
        df = pd.read_sql_query(sql, conn, params=(start_date, end_date))

    if df is None or df.empty:
        return {}

    result: Dict[str, pd.DataFrame] = {}
    for symbol, group in df.groupby("symbol"):
        # Preserve the SQL `ORDER BY date ASC` contract per symbol.
        result[symbol] = group.sort_values("date").reset_index(drop=True)
    return result


def rows_for_symbol(
    bulk: Dict[str, pd.DataFrame],
    symbol: str,
    columns: tuple[str, ...],
    min_date: str,
    max_date: str | None = None,
) -> list[tuple]:
    """Slice one symbol out of the pre-loaded bulk window.

    Returns ``list[tuple]`` filtered to ``[min_date, max_date]`` with the
    requested column order — identical shape to what the per-symbol SQL
    SELECT used to return. Columns missing from the live schema are padded
    with ``None`` so downstream ``col_count`` checks behave exactly like
    the original ``SELECT ... NULL AS ...`` fallback. Returns an empty list
    when the symbol is not present in the bulk window.
    """
    df = bulk.get(symbol)
    if df is None or df.empty:
        return []
    max_date = max_date or date.today().isoformat()
    mask = df["date"] >= min_date
    if max_date:
        mask = mask & (df["date"] <= max_date)
    sub = df.loc[mask]
    if sub.empty:
        return []
    present = [c for c in columns if c in sub.columns]
    if not present:
        return []
    out = []
    for r in sub[present].itertuples(index=False, name=None):
        row = list(r)
        padded = []
        i = 0
        for c in columns:
            if c in sub.columns:
                value = row[i]
                i += 1
            else:
                value = None
            # sqlite3 returns None for NULL; pandas read_sql produces NaN
            # in float columns. Normalize so tuples match the per-symbol
            # SQL path byte-for-byte.
            if isinstance(value, float) and math.isnan(value):
                value = None
            padded.append(value)  # noqa: PG-APPEND
        out.append(tuple(padded))  # noqa: PG-APPEND
    return out
