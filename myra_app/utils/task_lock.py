"""Named, cross-process task lock (Phase 9).

The Phase 8 Screener.in backfill refused a duplicate run with an **in-process**
lock only. That protects against two callers inside one server, but the manual
entry points are genuinely separate processes: ``run_pipeline.py --enrich-screener``
run by hand while the API server is up would double-fetch every symbol and
double-write the table — the exact "duplicate simultaneous runs of the same
logical task" the directive forbids.

This is deliberately the smallest thing that closes the gap: a pid file per
logical task name, under ``DB_DIR``, following the same advisory
best-effort rules as :mod:`myra_app.utils.scheduler_lock` (a filesystem problem
degrades to "assume free" rather than taking the app down, and a stale lock left
by a crashed process is reclaimed).

It is **not** a new scheduler, service or framework — just a lock file. Scope is
one named task, released in a ``finally``, so a crash cannot wedge it.
"""

from __future__ import annotations

import logging
import os
import time

from myra_app.constants import DB_DIR
from myra_app.utils.scheduler_lock import _pid_alive

logger = logging.getLogger(__name__)

_LOCK_PREFIX = ".myra_task_"


def _lock_path(name: str) -> str:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "._-")
    return os.path.join(DB_DIR, f"{_LOCK_PREFIX}{safe}.pid")


def read_owner(name: str) -> int | None:
    """Pid recorded as the current owner of ``name``, or None."""
    try:
        with open(_lock_path(name), "r", encoding="utf-8") as fh:
            raw = fh.read().strip().splitlines()
        return int(raw[0]) if raw and raw[0] else None
    except (FileNotFoundError, ValueError, IndexError):
        return None
    except Exception as exc:  # noqa: BLE001 - advisory only
        logger.debug("task lock read failed for %s: %s", name, exc)
        return None


def acquire(name: str) -> tuple[bool, int | None]:
    """Claim the named lock.

    Returns ``(acquired, existing_owner_pid)``. A lock left behind by a dead
    process is silently reclaimed. If the filesystem refuses the write we return
    ``True`` (advisory: never block a legitimate run because of a disk quirk).
    """
    existing = read_owner(name)
    if existing and existing != os.getpid() and _pid_alive(existing):
        return False, existing
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        with open(_lock_path(name), "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n{time.time()}\n")
        return True, existing
    except Exception as exc:  # noqa: BLE001 - advisory only
        logger.debug("task lock acquire failed for %s: %s", name, exc)
        return True, None


def release(name: str) -> None:
    """Release the named lock, but only if this process owns it."""
    try:
        owner = read_owner(name)
        if owner in (None, os.getpid()):
            os.remove(_lock_path(name))
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("task lock release failed for %s: %s", name, exc)
