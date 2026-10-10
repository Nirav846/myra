"""Safe, non-destructive write helpers for the enrichment/fundamentals layer.

Everything here implements the project's hard invariant: **never overwrite a valid
metric with null/0/NA on a failed or partial fetch**.  Two proven patterns are
captured so new code stops re-implementing them (and re-introducing clobber bugs):

* :func:`update_fill_only` — the ``consolidate_fundamentals_columns`` shape
  (``UPDATE ... SET c = COALESCE(?, c) WHERE key = ?``) with an optional
  zero-guard for counts/caps (``CASE WHEN ? > 0 THEN ? ELSE c END``).  ``None``
  values are dropped before the statement is built, so a fetch that returns
  nothing never writes a null.
* :func:`upsert_row` — the three-class ``ON CONFLICT`` upsert used by
  ``fundamental_sync._merge_and_insert``: identity/provenance columns overwrite,
  counts/caps are zero-guarded, everything else is ``COALESCE``-protected.

See ``docs/ENRICHMENT_PIPELINE_PLAN.md`` §4.3.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Mirrors ``myra_app.fundamental_sync`` so the two never drift apart.  These are
#: the ``fundamentals`` columns that must not be erased by an absent value.
FUNDAMENTALS_ALWAYS_OVERWRITE = frozenset(
    {"symbol", "date", "last_updated", "source_ms", "source_nse"}
)
FUNDAMENTALS_ZERO_GUARDED = frozenset(
    {
        "shares_outstanding",
        "market_cap",
        "promoter_holding_pct",
        "free_float_pct",
        "free_float_market_cap",
    }
)


def _ident(name: str) -> str:
    """Validate a bare SQL identifier (defence against injection / typos)."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _clean(values: dict) -> dict:
    """Drop ``None`` so a committed statement can never write a null."""
    return {k: v for k, v in values.items() if v is not None}


def sanitize_metrics(values: dict) -> dict:
    """Drop values that are structurally invalid before they can be written.

    Currently clamps-out an out-of-range ``free_float_pct`` (must be ``0 < p <= 100``)
    — the live DB had 33 rows above 100% — and its dependent market-cap field.
    """
    out = dict(values)
    ff = out.get("free_float_pct")
    if ff is not None:
        try:
            ff_f = float(ff)
        except (TypeError, ValueError):
            ff_f = None
        if ff_f is None or not (0.0 < ff_f <= 100.0):
            out.pop("free_float_pct", None)
            out.pop("free_float_market_cap", None)
    return out


def update_fill_only(
    conn,
    table: str,
    key_col: str,
    key_val,
    values: dict,
    zero_guarded: Iterable[str] = frozenset(),
) -> int:
    """Update only the columns present with a non-null value, fill-only.

    Returns the rowcount.  A no-op (0) when there is nothing safe to write.
    """
    _ident(table)
    _ident(key_col)
    zero_guarded = frozenset(zero_guarded)
    clean = _clean(values)
    if not clean:
        return 0

    set_parts: list[str] = []
    params: list = []
    for col, val in clean.items():
        _ident(col)
        if col in zero_guarded:
            set_parts.append(f"{col} = CASE WHEN ? > 0 THEN ? ELSE {col} END")
            params.extend([val, val])
        else:
            set_parts.append(f"{col} = COALESCE(?, {col})")
            params.append(val)
    params.append(key_val)

    sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {key_col} = ?"
    cur = conn.execute(sql, params)
    return cur.rowcount


def upsert_row(
    conn,
    table: str,
    key_col: str,
    record: dict,
    always_overwrite: Iterable[str] = frozenset(),
    zero_guarded: Iterable[str] = frozenset(),
) -> None:
    """Insert-or-update ``record`` using the three-class safety policy."""
    _ident(table)
    _ident(key_col)
    always_overwrite = frozenset(always_overwrite)
    zero_guarded = frozenset(zero_guarded)

    cols = list(record.keys())
    if not cols:
        return
    for col in cols:
        _ident(col)

    placeholders = ", ".join("?" for _ in cols)
    set_parts: list[str] = []
    for col in cols:
        if col == key_col or col in always_overwrite:
            set_parts.append(f"{col} = excluded.{col}")
        elif col in zero_guarded:
            set_parts.append(
                f"{col} = CASE WHEN excluded.{col} > 0 "
                f"THEN excluded.{col} ELSE {table}.{col} END"
            )
        else:
            set_parts.append(f"{col} = COALESCE(excluded.{col}, {table}.{col})")

    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key_col}) DO UPDATE SET {', '.join(set_parts)}"
    )
    conn.execute(sql, [record[c] for c in cols])
