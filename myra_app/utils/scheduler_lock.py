"""Single-scheduler guard.

At most one background orchestrator may drive the scheduled tasks against the
shared SQLite sidecars. Two independent schedulers (e.g. the embedded one now
owned by the API process *and* a stray ``python run_pipeline.py``) would each
fire the same tasks — doubling upstream traffic and creating avoidable
write contention for no benefit.

The guard is a tiny pid file under ``DB_DIR``. It is advisory and best-effort:
every operation is wrapped so a filesystem problem degrades to "assume free"
rather than taking the app down.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

LOCK_FILENAME = ".myra_scheduler.pid"


def _lock_path() -> str:
    return os.path.join(DB_DIR, LOCK_FILENAME)


def _pid_alive(pid: int) -> bool:
    """Best-effort "is this OS process still running" check."""
    if not pid or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001 - never fail the caller
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_owner() -> int | None:
    """Return the pid recorded as the current scheduler owner, or ``None``."""
    try:
        with open(_lock_path(), "r", encoding="utf-8") as fh:
            raw = fh.read().strip().splitlines()
        return int(raw[0]) if raw and raw[0] else None
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("scheduler lock read failed: %s", exc)
        return None


def owner_is_alive() -> bool:
    owner = read_owner()
    return bool(owner and owner != os.getpid() and _pid_alive(owner))


def acquire() -> tuple[bool, int | None]:
    """Claim the scheduler lock.

    Returns ``(acquired, existing_owner_pid)``. A stale lock left by a crashed
    process is silently reclaimed.
    """
    existing = read_owner()
    if existing and existing != os.getpid() and _pid_alive(existing):
        return False, existing
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        with open(_lock_path(), "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n{time.time()}\n")
        return True, existing
    except Exception as exc:  # noqa: BLE001 - advisory only
        logger.debug("scheduler lock acquire failed: %s", exc)
        return True, None


def release() -> None:
    """Release the lock, but only if this process owns it."""
    try:
        owner = read_owner()
        if owner in (None, os.getpid()):
            os.remove(_lock_path())
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("scheduler lock release failed: %s", exc)
