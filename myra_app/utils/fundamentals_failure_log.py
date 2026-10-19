"""Additive, credential-free failure telemetry for fundamentals refreshes.

Why this module exists
----------------------
The Phase 31 cohort audit could not answer its own central question: *why* did
these symbols fail? ``sync_fundamentals`` already computes a per-source reason
(``DataFetcher.last_source_errors``, built in Phase 26), but that evidence lives
only in memory and is ``print``ed once. Nothing captures it, so by the time
anyone investigates, the reason is gone -- the 2026-10-18 run left 119 failed
symbols and no way to classify a single one of them.

This module persists that evidence. It is deliberately narrow:

* **Observability only.** It never changes source order, retries, cooldowns,
  parking, or what gets written to ``fundamentals``/``quarterly_results``.
* **No new diagnostics.** It reads the reasons the fetcher already produced; it
  does not create a second source-selection or retry mechanism.
* **No free text.** Only allowlisted categories, an integer HTTP status, a
  validated exception *class name*, and a fixed detail code are ever stored.
  Raw URLs, query strings, headers, cookies, tokens, response bodies and
  exception messages cannot reach the table because no code path stores an
  arbitrary string -- an unrecognised detail is recorded as ``unknown`` with no
  text, rather than being passed through "just in case".
* **Never fatal.** :func:`record_failure` swallows every error: telemetry that
  cannot be written must not turn an ordinary source failure into a crash, nor
  hide the original failure.

Storage
-------
``myra_metadata.db`` (``DB_MAP["meta"]``), the same sidecar that already holds
``fundamentals_park_state``, ``fundamentals_sync_status`` and ``sync_log`` --
operational bookkeeping, not market data. The table is created lazily with
``CREATE TABLE IF NOT EXISTS`` at the point of use, matching the convention used
by ``source_stats``, ``task_registry`` and ``traction_symbol_state``.

Event granularity
-----------------
**One row per (symbol, source) per refresh attempt.** A symbol whose attempt
failed against two sources produces two rows sharing an ``observed_at`` and
``run_id``; a symbol with no per-source detail produces exactly one row with a
generic category and a NULL ``source``. Recording happens in exactly one place
(the sync loop), so an attempt cannot be counted twice.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

from myra_app import constants
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

#: Fully-qualified table name (``DB_MAP["meta"]``).
TABLE = "fundamentals_failure_log"

#: Retain at most this many failure rows per symbol. Mirrors the existing
#: keep-the-last-N housekeeping used for ``source_stats`` (fetcher.py) rather
#: than inventing a time-based rule with no precedent. Bounds growth at
#: ``symbols x KEEP_PER_SYMBOL`` rows instead of growing without limit.
KEEP_PER_SYMBOL = 20

# Categories. Every stored category comes from this allowlist; callers cannot
# inject their own text as a category.
CAT_HTTP_ERROR = "http_error"
CAT_NO_DATA = "no_data"
CAT_PAGE_NOTE = "page_note"
CAT_EXCEPTION = "exception"
CAT_RATE_LIMITED = "rate_limited"
CAT_FALLBACK_EXHAUSTED = "fallback_exhausted"
CAT_UNDATED_ROWS = "undated_rows_rejected"
CAT_PERSIST_FAILED = "persist_failed"
CAT_UNKNOWN = "unknown"

ALLOWED_CATEGORIES = frozenset(
    {
        CAT_HTTP_ERROR,
        CAT_NO_DATA,
        CAT_PAGE_NOTE,
        CAT_EXCEPTION,
        CAT_RATE_LIMITED,
        CAT_FALLBACK_EXHAUSTED,
        CAT_UNDATED_ROWS,
        CAT_PERSIST_FAILED,
        CAT_UNKNOWN,
    }
)

#: Known ``DataFetcher.last_page_note`` values -> stable detail codes. Only
#: values listed here are persisted; anything else becomes ``unknown`` rather
#: than leaking an unvetted string into the table.
PAGE_NOTE_CODES = {
    "HTTP 200 but no quarterly table in page": "no_quarterly_table",
}

# ``HTTP 403`` -- the shape fetcher.py:1196 writes for a non-200 response.
_HTTP_RE = re.compile(r"^HTTP (\d{3})$")
# ``screener_in: TimeoutError`` -- fetcher.py:1211. The message is never
# included (only the class name), which is what makes this value safe. The
# prefix must additionally equal the source key (checked at classification
# time), so a value shaped like "anything: token" cannot smuggle text through
# the class-name field.
_SOURCE_EXC_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}: ([A-Za-z_][A-Za-z0-9_]{0,63})$"
)
# ``persist failed (IntegrityError: ...)`` -- fundamental_manager.py:348.
# Only the class name is kept; the message after the colon is discarded.
_PERSIST_RE = re.compile(r"^persist failed \(([A-Za-z_][A-Za-z0-9_]{0,63}):")
# A real Python exception class ends in one of these suffixes. Anything else is
# rejected rather than stored -- there is no "store it anyway" fallback, so a
# message that happens to contain ": token" cannot leak.
_EXC_CLASS_RE = re.compile(
    r"^(?:[A-Z][A-Za-z0-9_]{0,63}"
    r"(?:Error|Exception|Warning|Interrupt|Exit|Timeout|Iteration)|Timeout)$"
)
_SOURCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._&-]{0,39}$")

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    observed_at     TEXT    NOT NULL,
    run_id          TEXT,
    source          TEXT,
    category        TEXT    NOT NULL,
    http_status     INTEGER,
    exception_class TEXT,
    detail_code     TEXT
)
"""

_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_symbol " f"ON {TABLE} (symbol, id)"
)


# --------------------------------------------------------------------------- #
# Value sanitisation -- the only values that can reach the table
# --------------------------------------------------------------------------- #


def db_path(db_dir=None):
    """Absolute path of the metadata sidecar (always via ``DB_MAP``)."""
    return os.path.join(db_dir or constants.DB_DIR, LibrarianCore.DB_MAP["meta"])


def _clean_symbol(symbol):
    """Return a storable symbol, or ``None`` if it cannot be made safe."""
    if symbol is None:
        return None
    text = str(symbol).strip()
    return text if _SYMBOL_RE.match(text) else None


def _clean_source(source):
    """Return a storable source name, or ``None``."""
    if not source:
        return None
    text = str(source).strip()
    return text if _SOURCE_RE.match(text) else None


def _clean_exception_class(name):
    """Return a storable exception class name, or ``None``.

    The regex is anchored and character-restricted, so an exception *message*
    (which may embed a URL, cookie or token) can never match. This is the
    sanitisation boundary: there is no fallback that stores the raw value.
    """
    if not name:
        return None
    text = str(name).strip()
    return text if _EXC_CLASS_RE.match(text) else None


def _clean_http_status(status):
    """Return an HTTP status as an int in 100..599, or ``None``."""
    if status is None or isinstance(status, bool):
        return None
    try:
        value = int(status)
    except (TypeError, ValueError):
        return None
    return value if 100 <= value <= 599 else None


def _clean_detail_code(code):
    """Return a detail code from the fixed allowlist, or ``None``."""
    return code if code in PAGE_NOTE_CODES.values() else None


def _clean_run_id(run_id):
    """Return a run identifier, or ``None``. Generated by us, never user text."""
    if not run_id:
        return None
    text = str(run_id).strip()
    return text[:40] if text else None


# --------------------------------------------------------------------------- #
# Classification -- turning existing in-memory evidence into allowlisted events
# --------------------------------------------------------------------------- #


def _event(
    source=None,
    category=CAT_UNKNOWN,
    http_status=None,
    exception_class=None,
    detail_code=None,
):
    """Build one normalised, already-sanitised event dict."""
    return {
        "source": _clean_source(source),
        "category": category if category in ALLOWED_CATEGORIES else CAT_UNKNOWN,
        "http_status": _clean_http_status(http_status),
        "exception_class": _clean_exception_class(exception_class),
        "detail_code": _clean_detail_code(detail_code),
    }


def classify_source_detail(source, detail):
    """Classify one ``last_source_errors`` entry.

    ``detail`` is the value the fetcher wrote. Only the handful of shapes the
    fetcher actually produces are recognised; anything else is ``unknown`` with
    no stored text, so an unexpected or secret-bearing value is dropped rather
    than persisted.
    """
    if not isinstance(detail, str):
        return _event(source=source, category=CAT_UNKNOWN)

    text = detail.strip()

    match = _HTTP_RE.match(text)
    if match:
        return _event(
            source=source,
            category=CAT_HTTP_ERROR,
            http_status=int(match.group(1)),
        )

    if text == "no data returned":
        return _event(source=source, category=CAT_NO_DATA)

    if text in PAGE_NOTE_CODES:
        return _event(
            source=source,
            category=CAT_PAGE_NOTE,
            detail_code=PAGE_NOTE_CODES[text],
        )

    # The fetcher writes ``f"{name}: {type(exc).__name__}"``, so the prefix must
    # be this symbol's own source key. Requiring it stops a value like
    # "https://host/?token=x" or "Cookie: sessionid" from ever reaching the
    # exception-class column.
    if source and text.startswith(f"{source}: "):
        match = _SOURCE_EXC_RE.match(text)
        if match:
            return _event(
                source=source,
                category=CAT_EXCEPTION,
                exception_class=match.group(1),
            )

    return _event(source=source, category=CAT_UNKNOWN)


def classify_last_error(last_error, last_source=None):
    """Classify ``FundamentalManager.last_error`` when no per-source detail exists.

    ``last_error`` is manager-generated prose, so it is pattern-matched rather
    than stored. A message that matches nothing becomes ``unknown``; the text
    itself is never written.
    """
    if not isinstance(last_error, str) or not last_error.strip():
        return _event(category=CAT_UNKNOWN)

    text = last_error.strip()

    if text.startswith("multi-source fallback exhausted"):
        return _event(category=CAT_FALLBACK_EXHAUSTED)

    if text.startswith("source returned only undated rows"):
        # The manager names the source that produced the rows in
        # ``last_source`` (it is set whenever the chain returned data), so this
        # is evidence, not a guess. The category is deliberately "rejected":
        # the guard did its job.
        return _event(last_source, CAT_UNDATED_ROWS)

    match = _PERSIST_RE.match(text)
    if match:
        return _event(category=CAT_PERSIST_FAILED, exception_class=match.group(1))

    return _event(category=CAT_UNKNOWN)


def derive_events(
    *,
    last_error=None,
    source_errors=None,
    last_source=None,
    http_status=None,
    rate_limited=False,
    exc_class=None,
):
    """Turn one failed attempt's evidence into a list of events to persist.

    Precedence is deliberate and mirrors how the sync loop branches:

    1. ``rate_limited`` -> one event (the throttling status). Not the symbol's
       fault, so it is recorded as its own category rather than a source error.
    2. ``exc_class`` -> one event (an exception escaped the manager; there is
       no per-source evidence for it, and stale evidence is not reused).
    3. ``source_errors`` -> one event per source, preserving the distinction
       instead of collapsing everything into "fallback exhausted".
    4. otherwise -> one event classified from ``last_error``.
    """
    if rate_limited:
        return [_event(category=CAT_RATE_LIMITED, http_status=http_status)]

    if exc_class:
        return [_event(category=CAT_EXCEPTION, exception_class=exc_class)]

    if isinstance(source_errors, dict) and source_errors:
        return [
            classify_source_detail(source, detail)
            for source, detail in sorted(source_errors.items())
        ]

    return [classify_last_error(last_error, last_source)]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def ensure_table(conn):
    """Create the telemetry table and its index if absent (idempotent)."""
    conn.execute(_DDL)
    conn.execute(_INDEX_DDL)


def _apply_retention(conn, symbol):
    """Keep the newest ``KEEP_PER_SYMBOL`` rows for ``symbol``."""
    conn.execute(
        f"DELETE FROM {TABLE} WHERE symbol = ? AND id NOT IN "
        f"(SELECT id FROM {TABLE} WHERE symbol = ? ORDER BY id DESC LIMIT ?)",
        (symbol, symbol, KEEP_PER_SYMBOL),
    )


def record_events(
    db_dir,
    symbol,
    events,
    *,
    run_id=None,
    observed_at=None,
    conn=None,
    keep_per_symbol=KEEP_PER_SYMBOL,
):
    """Insert ``events`` for ``symbol`` atomically. Returns the row count.

    All rows for one attempt share ``observed_at`` and ``run_id`` and are
    committed together, so a reader never sees half an attempt. Raises on
    database errors -- :func:`record_failure` is the swallowing wrapper used by
    the sync loop.
    """
    cleaned_symbol = _clean_symbol(symbol)
    if not cleaned_symbol or not events:
        return 0

    stamp = observed_at or datetime.now(timezone.utc).isoformat()
    run = _clean_run_id(run_id)

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(db_path(db_dir), timeout=10)
    try:
        ensure_table(conn)
        inserted = 0
        for event in events:
            conn.execute(
                f"INSERT INTO {TABLE} "
                "(symbol, observed_at, run_id, source, category, "
                " http_status, exception_class, detail_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cleaned_symbol,
                    stamp,
                    run,
                    event.get("source"),
                    event.get("category", CAT_UNKNOWN),
                    event.get("http_status"),
                    event.get("exception_class"),
                    event.get("detail_code"),
                ),
            )
            inserted += 1
        if keep_per_symbol:
            _apply_retention(conn, cleaned_symbol)
        conn.commit()
        return inserted
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - rollback is best effort
            pass
        raise
    finally:
        if own_conn:
            conn.close()


def record_failure(db_dir=None, symbol=None, **evidence):
    """Record a failed attempt without ever raising.

    This is the entry point the fundamentals sync uses. Any storage problem is
    logged at a sanitised level (symbol only, never the evidence) and reported
    as ``0`` rows so the caller's failure handling is unaffected.
    """
    try:
        run_id = evidence.pop("run_id", None)
        events = derive_events(**evidence)
        return record_events(db_dir, symbol, events, run_id=run_id)
    except Exception:  # noqa: BLE001 - telemetry must never break a refresh
        logger.warning("could not record fundamentals failure telemetry for %s", symbol)
        logger.debug("fundamentals failure telemetry error", exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #


def _ro_connection(db_dir=None):
    """Open the metadata sidecar read-only, or return ``None`` if unavailable."""
    path = db_path(db_dir)
    if not os.path.exists(path):
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None


def summarize(db_dir=None, symbol=None, limit=50):
    """Read-only failure counts grouped by symbol, source and category.

    Answers "what keeps failing, and how" without writing anything. Returns an
    empty list when the database or table does not exist yet.
    """
    conn = _ro_connection(db_dir)
    if conn is None:
        return []
    try:
        where, params = "", []
        if symbol:
            cleaned = _clean_symbol(symbol)
            if not cleaned:
                return []
            where, params = "WHERE symbol = ?", [cleaned]
        sql = (
            f"SELECT symbol, source, category, COUNT(*) AS n, MAX(observed_at) "
            f"FROM {TABLE} {where} GROUP BY symbol, source, category "
            f"ORDER BY n DESC, symbol LIMIT ?"
        )
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "symbol": r[0],
                "source": r[1],
                "category": r[2],
                "count": r[3],
                "last_seen": r[4],
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def recent_failures(db_dir=None, symbol=None, limit=20):
    """Read-only newest-first failure rows, optionally for one symbol.

    This is how an operator answers "what actually failed for this symbol
    during the most recent refresh?" -- rows from one attempt share
    ``observed_at``, so the latest run is the newest distinct timestamp.
    """
    conn = _ro_connection(db_dir)
    if conn is None:
        return []
    try:
        columns = (
            "symbol, observed_at, run_id, source, category, "
            "http_status, exception_class, detail_code"
        )
        params = []
        where = ""
        if symbol:
            cleaned = _clean_symbol(symbol)
            if not cleaned:
                return []
            where, params = "WHERE symbol = ?", [cleaned]
        sql = f"SELECT {columns} FROM {TABLE} {where} ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        names = [c.strip() for c in columns.split(",")]
        return [dict(zip(names, r)) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


__all__ = [
    "TABLE",
    "KEEP_PER_SYMBOL",
    "ALLOWED_CATEGORIES",
    "PAGE_NOTE_CODES",
    "db_path",
    "ensure_table",
    "classify_source_detail",
    "classify_last_error",
    "derive_events",
    "record_events",
    "record_failure",
    "summarize",
    "recent_failures",
]
