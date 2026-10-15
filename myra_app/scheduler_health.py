"""Observable scheduler health (Phase 13).

The Phase 12 incident: the API answered ``/api/health`` with HTTP 200 and every
database "connected", while the background scheduler and the EOD recovery
watchdog were **not running at all**.  HTTP liveness said everything was fine.

This module answers a different question - *is the scheduler actually driving
the schedule?* - and answers it from evidence that already exists:

* the scheduler lock file (who owns the scheduler right now), and
  :mod:`myra_app.utils.scheduler_lock`'s tri-state ownership check;
* the watchdog's own heartbeat - it rewrites ``task_registry.updated_at`` every
  60 s while it is alive, so a frozen timestamp means a frozen watchdog.

Nothing here starts, stops or repairs a scheduler.  It is read-only, and it
never deletes a lock file (see the stale-lock policy in
``docs/PHASE13_FUNDAMENTAL_RECOVERY_AND_SCHEDULER_HEALTH.md``).

States
------
``running``   the scheduler owns the lock and its heartbeat is advancing
``starting``  the scheduler owns the lock but has not ticked yet, within grace
``degraded``  the API is up but no live scheduler owns the lock, or the
              heartbeat has stopped advancing
``disabled``  ``DISABLE_SCHEDULER`` is set, so this is intentional
``unknown``   not enough evidence (unreadable lock, unreadable registry)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Any

from myra_app import constants
from myra_app.utils import scheduler_lock

logger = logging.getLogger(__name__)

STATE_RUNNING = "running"
STATE_STARTING = "starting"
STATE_DEGRADED = "degraded"
STATE_DISABLED = "disabled"
STATE_UNKNOWN = "unknown"

#: The watchdog waits 60 s between ticks (``myra_app/tasks/watchdog.py``).
WATCHDOG_TICK_SECONDS = 60.0
#: A heartbeat older than three ticks is stale - it tolerates one missed tick
#: plus scheduling drift without crying wolf.
WATCHDOG_STALE_AFTER_SECONDS = 3 * WATCHDOG_TICK_SECONDS
#: How long after taking the lock we still call the scheduler "starting"
#: rather than "degraded", because the first tick has not been due yet.
STARTUP_GRACE_SECONDS = 2 * WATCHDOG_TICK_SECONDS

#: The heartbeat row the watchdog maintains.
WATCHDOG_TASK_NAME = "Background sync watchdog"

#: Health is polled by the UI (default every 10 s).  The lock is a single small
#: file, but the registry probe is a DB read, so memoise the computed state
#: briefly instead of rescanning on every poll.
_CACHE_TTL_SECONDS = 5.0
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _metadata_db_path() -> str:
    return os.path.join(constants.DB_DIR, "myra_metadata.db")


def _watchdog_last_tick(db_path: str | None = None) -> float | None:
    """Epoch seconds of the watchdog's last heartbeat, or ``None`` if unknown.

    Raises :class:`sqlite3.Error` so the caller can distinguish "no heartbeat
    row" (returns ``None``) from "could not read the registry at all".
    """
    path = db_path or _metadata_db_path()
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM task_registry WHERE name = ?",
            (WATCHDOG_TASK_NAME,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        # updated_at is a naive local timestamp (datetime.now().isoformat()).
        import datetime as _dt

        return _dt.datetime.fromisoformat(row[0]).timestamp()
    except (TypeError, ValueError):
        return None


def get_state(
    now: float | None = None,
    db_path: str | None = None,
    record: "scheduler_lock.LockRecord | None" = None,
    ownership: str | None = None,
    disabled: bool | None = None,
    last_tick: float | None = None,
    tick_known: bool = True,
    probe: bool = True,
) -> dict[str, Any]:
    """Compute the scheduler health state.

    Every input is injectable so tests can exercise all five states without a
    real lock file, a real process table or the live databases.  Pass
    ``probe=False`` together with ``last_tick`` to supply the heartbeat directly
    (``last_tick=None`` then means "the watchdog has never ticked").
    """
    now = time.time() if now is None else now
    is_disabled = constants.DISABLE_SCHEDULER if disabled is None else disabled

    if probe and (record is None or ownership is None):
        ownership, record = scheduler_lock.owner_state()
    if record is None:
        record = scheduler_lock.LockRecord("absent")
    if ownership is None:
        ownership = scheduler_lock.OWNER_ABSENT

    if probe and tick_known:
        try:
            last_tick = _watchdog_last_tick(db_path)
        except sqlite3.Error as exc:
            logger.debug("scheduler health: registry read failed: %s", exc)
            tick_known = False

    age = None if last_tick is None else max(0.0, now - last_tick)
    heartbeat_fresh = age is not None and age <= WATCHDOG_STALE_AFTER_SECONDS

    result: dict[str, Any] = {
        "owner_pid": record.pid if record.status == "ok" else None,
        "lock": ownership,
        "watchdog_last_tick_age_seconds": None if age is None else round(age, 1),
        "watchdog_tick_seconds": WATCHDOG_TICK_SECONDS,
        "stale_after_seconds": WATCHDOG_STALE_AFTER_SECONDS,
    }

    if is_disabled:
        result.update(
            state=STATE_DISABLED,
            reason="DISABLE_SCHEDULER is set; the scheduler is intentionally off",
        )
        return result

    if ownership == scheduler_lock.OWNER_UNKNOWN:
        result.update(
            state=STATE_UNKNOWN,
            reason=record.error or "scheduler lock could not be read",
        )
        return result

    if not tick_known:
        result.update(state=STATE_UNKNOWN, reason="task registry could not be read")
        return result

    holds_lock = ownership == scheduler_lock.OWNER_ALIVE and record.pid == os.getpid()
    other_owner = ownership == scheduler_lock.OWNER_ALIVE and record.pid != os.getpid()

    if other_owner:
        result.update(
            state=STATE_RUNNING,
            reason=f"another process (pid={record.pid}) owns the scheduler",
        )
        return result

    if not holds_lock:
        result.update(
            state=STATE_DEGRADED,
            reason=(
                "no live scheduler owns the lock; scheduled ingest and the EOD "
                "recovery watchdog are not running"
            ),
        )
        return result

    # We own the lock.  Distinguish "still warming up" from "stopped ticking".
    if heartbeat_fresh:
        result.update(state=STATE_RUNNING, reason="watchdog heartbeat is advancing")
        return result

    acquired_at = record.acquired_at
    within_grace = (
        acquired_at is not None
        and (now - acquired_at) <= STARTUP_GRACE_SECONDS
        and last_tick is None
    )
    if within_grace:
        result.update(
            state=STATE_STARTING,
            reason="scheduler owns the lock; first watchdog tick not due yet",
        )
        return result

    if last_tick is None:
        reason = "scheduler owns the lock but the watchdog has never reported a tick"
    else:
        reason = (
            f"watchdog heartbeat is stale by {age:.0f}s "
            f"(> {WATCHDOG_STALE_AFTER_SECONDS:.0f}s)"
        )
    result.update(state=STATE_DEGRADED, reason=reason)
    return result


def get_state_cached(now: float | None = None) -> dict[str, Any]:
    """TTL-cached :func:`get_state` for the health endpoint.

    The UI polls ``/api/health`` every ~10 s; memoising for a few seconds keeps
    that from turning into a registry read per poll without hiding a state
    change for long.
    """
    current = time.time() if now is None else now
    with _cache_lock:
        cached = _cache.get("value")
        if cached is not None and (current - _cache["at"]) < _CACHE_TTL_SECONDS:
            return dict(cached)
    try:
        value = get_state(now=current)
    except Exception as exc:  # noqa: BLE001 - health must never raise
        logger.debug("scheduler health computation failed: %s", exc)
        value = {
            "state": STATE_UNKNOWN,
            "reason": f"scheduler health unavailable: {exc}",
        }
    with _cache_lock:
        _cache["at"] = current
        _cache["value"] = dict(value)
    return value


def reset_cache() -> None:
    """Test isolation: drop the memoised state."""
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["value"] = None


def healthy(state: str) -> bool:
    """Whether ``state`` should read as healthy on a status surface.

    ``degraded`` and ``unknown`` are deliberately *not* healthy: a serving API
    with a dead scheduler must not look green.
    """
    return state in (STATE_RUNNING, STATE_DISABLED)
