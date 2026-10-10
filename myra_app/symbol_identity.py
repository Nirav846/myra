"""Symbol identity bridge (Phase 2.5).

``fund_traction`` symbols are a *mix*: when the upstream feed supplies an NSE
ticker the row already carries the canonical symbol, but when it does not the
row falls back to a Trendlyne-style long name (``AARTIINDUSTRIES``,
``AAVASFINANCIERS``, ...).  Those long names never join to ``technical_data`` /
``fundamentals`` (keyed by the canonical ticker), so the same company is silently
treated as two.

This module builds the additive ``symbol_alias`` map (``alias -> canonical``)
in ``myra_metadata.db`` from a **deterministic, local** source of truth: an
exact match between a normalised fund_traction symbol and a normalised
``symbols_master.name``.  It deliberately does not guess:

* an alias that is already a canonical symbol is skipped;
* a normalised name that maps to more than one symbol is treated as ambiguous
  and skipped (never guessed);
* foreign holdings (``ALPHABETINC``, ``AMAZONCOMINC``) simply do not match and
  are left alone — the bridge is not a place to invent identities.

Writes are additive and idempotent (``alias`` is the primary key) and, on
conflict, only overwrite a mapping with one of equal-or-higher confidence.

Design + status: ``docs/ENRICHMENT_PIPELINE_PLAN.md`` §4 / Phase 2.5.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

#: Corporate suffixes stripped before matching (Indian-listing oriented).
_SUFFIXES = (
    "LIMITED",
    "LTD",
    "CORPORATION",
    "CORP",
    "COMPANY",
    "CO",
    "PRIVATE",
    "PVT",
    "INDUSTRIES",
)

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_name(value: Optional[str]) -> str:
    """Uppercase, drop non-alphanumerics, then strip trailing corporate suffixes.

    ``"Aavas Financiers Limited"`` -> ``"AAVASFINANCIERS"``;
    ``"AAVASFINANCIERS"`` -> ``"AAVASFINANCIERS"`` (idempotent).
    """
    text = _NON_ALNUM.sub("", (value or "").upper())
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix) + 2:
                text = text[: -len(suffix)]
                changed = True
    return text


def resolve_aliases(
    master_rows: Iterable[tuple[str, Optional[str]]],
    ft_symbols: Iterable[str],
) -> tuple[dict[str, str], dict]:
    """Return ``(alias_to_canonical, stats)`` for the given inputs.

    ``master_rows`` yields ``(symbol, name)`` from ``symbols_master``;
    ``ft_symbols`` yields the distinct ``fund_traction.symbol`` values.  Pure and
    side-effect free so it is unit-testable without a database.
    """
    canonical: set[str] = set()
    name_index: dict[str, list[str]] = {}
    for symbol, name in master_rows:
        if not symbol:
            continue
        canonical.add(symbol)
        norm = normalize_name(name)
        if norm:
            name_index.setdefault(norm, [])
            if symbol not in name_index[norm]:
                name_index[norm].append(symbol)

    mapping: dict[str, str] = {}
    ambiguous: list[str] = []
    for raw in ft_symbols:
        alias = (raw or "").strip().upper()
        if not alias or alias in canonical:
            continue
        candidates = name_index.get(normalize_name(alias), [])
        if len(candidates) == 1 and candidates[0] != alias:
            mapping[alias] = candidates[0]
        elif len(candidates) > 1:
            ambiguous.append(alias)

    stats = {
        "canonical_count": len(canonical),
        "ambiguous": len(ambiguous),
        "ambiguous_sample": ambiguous[:10],
    }
    return mapping, stats


def _ensure_alias_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_alias (
            alias      TEXT PRIMARY KEY,
            canonical  TEXT NOT NULL,
            source     TEXT,
            confidence REAL,
            updated_at TEXT
        )
        """
    )


def write_aliases(
    conn: sqlite3.Connection,
    mapping: dict[str, str],
    source: str = "name_match",
    confidence: float = 1.0,
) -> int:
    """Upsert ``mapping`` into ``symbol_alias``; return rows written.

    On conflict, only replace when the new confidence is >= the stored one, so a
    weaker source can never downgrade a stronger mapping.
    """
    if not mapping:
        return 0
    _ensure_alias_table(conn)
    now = datetime.now().isoformat()
    written = 0
    for alias, canonical in mapping.items():
        if not alias or not canonical or alias == canonical:
            continue
        conn.execute(
            """
            INSERT INTO symbol_alias (alias, canonical, source, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                canonical  = excluded.canonical,
                source     = excluded.source,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            WHERE excluded.confidence >= symbol_alias.confidence
            """,
            (alias, canonical, source, confidence, now),
        )
        written += 1
    conn.commit()
    return written


def _db_path(db_key: str) -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP[db_key])


def backfill_symbol_alias(
    dry_run: bool = True,
    meta_conn: Optional[sqlite3.Connection] = None,
    val_conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Populate ``symbol_alias`` from local name matching.

    ``dry_run=True`` (default) resolves and reports without writing.  Callers
    that own connections may pass them (used by tests); otherwise read-only
    connections to the live DBs are opened.
    """
    own_meta = meta_conn is None
    own_val = val_conn is None
    if own_meta:
        # Read-only while resolving; writable only when we will actually write.
        if dry_run:
            meta_conn = sqlite3.connect(f"file:{_db_path('meta')}?mode=ro", uri=True)
        else:
            meta_conn = sqlite3.connect(_db_path("meta"))
    if own_val:
        val_conn = sqlite3.connect(f"file:{_db_path('valuation')}?mode=ro", uri=True)
    try:
        master_rows = meta_conn.execute(
            "SELECT symbol, name FROM symbols_master"
        ).fetchall()
        ft_symbols = [
            r[0] for r in val_conn.execute("SELECT DISTINCT symbol FROM fund_traction")
        ]
        mapping, stats = resolve_aliases(master_rows, ft_symbols)
        report = {
            "aliases": len(mapping),
            "ft_symbols": len(ft_symbols),
            "dry_run": dry_run,
            "sample": dict(list(mapping.items())[:10]),
            **stats,
        }
        if dry_run:
            return report
        report["written"] = write_aliases(meta_conn, mapping)
        return report
    finally:
        if own_meta and meta_conn is not None:
            meta_conn.close()
        if own_val and val_conn is not None:
            val_conn.close()
