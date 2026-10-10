"""Persistent TTL cache for the resilient data-source layer.

Backed by the existing ``cache`` table in ``myra_cache_network.db``
(``key TEXT PRIMARY KEY, value BLOB, created_at TEXT``).  The table predates this
module but had no readers/writers; this is its first real use.

Design rules (see ``docs/ENRICHMENT_PIPELINE_PLAN.md``):

* **Never raise** — a cache failure degrades to a miss; it must never take down a
  fetch pipeline.
* Values are JSON-encoded so callers can store dict/list/scalar safely.
* ``created_at`` is stored as a UNIX-epoch float string.  Rows written by other
  tools with ``datetime('now')`` are parsed as a fallback so a mixed table still
  works.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600


def _parse_created_at(raw: Any) -> Optional[float]:
    """Return the epoch seconds for ``raw`` or ``None`` if it cannot be parsed."""
    if raw is None:
        return None
    # Fast path: our own writer stores an epoch float string.
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    # Fallback: SQLite's datetime('now') -> 'YYYY-MM-DD HH:MM:SS' (UTC).
    text = str(raw).strip()
    if not text:
        return None
    import datetime as _dt

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = _dt.datetime.strptime(text[:19] if len(text) >= 19 else text, fmt)
            return dt.replace(tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _decode(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


class TtlCache:
    """Tiny SQLite-backed key/value cache with per-entry age checks."""

    def __init__(
        self,
        db_key: str = "network_cache",
        table: str = "cache",
        db_path: Optional[str] = None,
    ):
        self._db_key = db_key
        self._table = table
        # ``db_path`` override exists for tests; production always resolves
        # through DB_DIR + DB_MAP (project invariant).
        self._db_path = db_path or os.path.join(DB_DIR, LibrarianCore.DB_MAP[db_key])

    # -- internals ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self._table} ("
            "key TEXT PRIMARY KEY, "
            "value BLOB, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )

    # -- public API --------------------------------------------------------

    def age_seconds(self, key: str) -> Optional[float]:
        """Age of ``key`` in seconds, or ``None`` when absent/unparseable."""
        try:
            with self._connect() as conn:
                self._ensure_table(conn)
                row = conn.execute(
                    f"SELECT created_at FROM {self._table} WHERE key = ?", (key,)
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 - cache must never raise
            logger.debug("cache age failed for %s: %s", key, exc)
            return None
        if not row:
            return None
        created = _parse_created_at(row[0])
        if created is None:
            return None
        return max(0.0, time.time() - created)

    def get(self, key: str, ttl_seconds: Optional[int] = DEFAULT_TTL_SECONDS) -> Any:
        """Return the cached value if fresh (or if ``ttl_seconds is None``)."""
        try:
            with self._connect() as conn:
                self._ensure_table(conn)
                row = conn.execute(
                    f"SELECT value, created_at FROM {self._table} WHERE key = ?",
                    (key,),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.debug("cache get failed for %s: %s", key, exc)
            return None
        if not row:
            return None
        if ttl_seconds is not None:
            created = _parse_created_at(row[1])
            # Unparseable or older than TTL => treat as expired.
            if created is None or (time.time() - created) > ttl_seconds:
                return None
        return _decode(row[0])

    def get_stale(self, key: str) -> Any:
        """Return the value regardless of age (used as a last-resort fallback)."""
        return self.get(key, ttl_seconds=None)

    def set(
        self, key: str, value: Any, ttl_seconds: Optional[int] = DEFAULT_TTL_SECONDS
    ) -> None:
        """Upsert ``value`` under ``key``. ``ttl_seconds`` is advisory metadata
        only (freshness is evaluated on read); it is stored alongside the value so
        a future reader can surface the intended lifetime."""
        try:
            payload = sqlite3.Binary(
                json.dumps(value, default=str, ensure_ascii=False).encode("utf-8")
            )
            with self._connect() as conn:
                self._ensure_table(conn)
                conn.execute(
                    f"INSERT INTO {self._table}(key, value, created_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "created_at = excluded.created_at",
                    (key, payload, str(time.time())),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("cache set failed for %s: %s", key, exc)

    def delete(self, key: str) -> None:
        try:
            with self._connect() as conn:
                self._ensure_table(conn)
                conn.execute(f"DELETE FROM {self._table} WHERE key = ?", (key,))
        except Exception as exc:  # noqa: BLE001
            logger.debug("cache delete failed for %s: %s", key, exc)
