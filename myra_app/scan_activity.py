"""Authoritative record of in-flight interactive scanner runs.

Why this exists
---------------
Interactive scanning is MYRA's primary workload and must not be made sluggish by
background maintenance. Every scanner in :mod:`myra_web.routes.scanners` already
tracks its own ``scan_status``, but those dicts are per-scanner and private to
that module, so the background scheduler had no way to know a scan was running
and could start a compute-heavy job mid-scan.

This module is the single, small, authoritative signal shared by the scanners
and the scheduler. It is deliberately event-driven: no polling, no filesystem
checks, no CPU sampling. Callers acquire on entry and release in a ``finally``,
so an exception, cancellation or early exit can never leave a scanner recorded as
active forever (a stuck "active" flag would silently defer all background work).

It must not depend on a browser tab: scanners register here from the worker
thread that actually runs the scan, independent of the HTTP client.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Iterator

#: Guards ``_active``. Held only for dict mutation, never across a scan.
_lock = threading.Lock()

#: scanner name -> epoch seconds when it started. A dict, not a set, so the
#: status surface can report how long a scan has been running.
_active: dict[str, float] = {}


def begin(name: str) -> None:
    """Record that ``name`` started scanning.

    Idempotent per name: a repeated ``begin`` refreshes the start time rather
    than raising, so a scanner that is restarted cannot wedge the registry.
    """
    with _lock:
        _active[name] = time.time()


def end(name: str) -> None:
    """Record that ``name`` stopped scanning. Safe to call when not active."""
    with _lock:
        _active.pop(name, None)


def is_active() -> bool:
    """Whether any interactive scan is currently running."""
    with _lock:
        return bool(_active)


def active_scanners() -> list[str]:
    """Names of the scanners currently running, sorted for stable output."""
    with _lock:
        return sorted(_active)


def snapshot(now: float | None = None) -> dict:
    """Small, JSON-safe summary for status endpoints and dispatch decisions."""
    now = time.time() if now is None else now
    with _lock:
        names = sorted(_active)
        oldest = min(_active.values()) if _active else None
    return {
        "active": bool(names),
        "scanners": names,
        "count": len(names),
        "oldest_age_seconds": (
            None if oldest is None else round(max(0.0, now - oldest), 1)
        ),
    }


def reset() -> None:
    """Clear the registry. Test isolation only — never call from a scan."""
    with _lock:
        _active.clear()


@contextlib.contextmanager
def scanning(name: str) -> Iterator[None]:
    """Mark ``name`` active for the duration of the block, always releasing.

    ``finally`` (not ``except``) is deliberate: a scanner that raises, is
    cancelled, or returns early must still release the flag, otherwise every
    deferrable background task would wait on a scan that no longer exists.
    """
    begin(name)
    try:
        yield
    finally:
        end(name)
