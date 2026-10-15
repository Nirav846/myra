"""Single-scheduler guard.

At most one background orchestrator may drive the scheduled tasks against the
shared SQLite sidecars. Two independent schedulers (e.g. the embedded one now
owned by the API process *and* a stray ``python run_pipeline.py``) would each
fire the same tasks - doubling upstream traffic and creating avoidable
write contention for no benefit.

The guard is a tiny pid file under ``DB_DIR``. It is advisory and best-effort:
every operation is wrapped so a filesystem problem degrades to "assume free"
rather than taking the app down.

Phase 13 hardening
------------------
The original guard had three failure modes that matter because a *wrong* answer
here either strands the app without a scheduler (no watchdog, no EOD recovery)
or - worse - lets a second scheduler start against the same databases:

1. **Ownership was decided from a pid alone.** A pid can be recycled, so a
   stale lock file could name a pid that now belongs to an unrelated process,
   and the guard would conclude a scheduler was still alive. The lock file now
   also records a *process creation token*; when it is present and differs from
   the live process with that pid, the recorded owner is provably gone and the
   lock is reclaimable.

2. **A corrupt or unreadable lock file looked exactly like a free one.** The
   old code returned "no owner" for both, so a damaged file was silently
   reclaimed. Ownership is now tri-state (``alive`` / ``dead`` / ``unknown``)
   and only a *confirmed dead* owner permits reclamation. ``unknown`` refuses
   the start and reports an actionable reason instead of guessing.

3. **Acquisition was a read-then-write.** Two processes starting together could
   both observe "no owner" and both write themselves in. Acquisition now takes
   the file with ``O_CREAT|O_EXCL`` and, when reclaiming, replaces it atomically
   and then *re-reads to confirm* it still names us - a compare-and-swap, so a
   loser of the race cannot believe it owns the scheduler.

``_pid_alive`` keeps its one-argument signature: :mod:`myra_app.utils.task_lock`
imports it and tests patch it directly.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

LOCK_FILENAME = ".myra_scheduler.pid"

#: Ownership states. ``UNKNOWN`` must never be treated as ``DEAD``.
OWNER_ALIVE = "alive"
OWNER_DEAD = "dead"
OWNER_UNKNOWN = "unknown"
OWNER_ABSENT = "absent"


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


def _process_create_token(pid: int) -> str | None:
    """A value that changes when ``pid`` is recycled, or ``None`` if unknown.

    A pid on its own does not identify a process: Windows (and every POSIX
    system) reuses pids, so "pid 4596 is alive" can be true of a *different*
    process than the one that took the lock. Pairing the pid with the moment the
    process started disambiguates them.

    Best-effort by design: any failure returns ``None``, which callers must read
    as "cannot tell" rather than "dead".
    """
    if not pid or pid <= 0:
        return None
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_t = wintypes.FILETIME()
                kernel_t = wintypes.FILETIME()
                user_t = wintypes.FILETIME()
                ok = kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_t),
                    ctypes.byref(kernel_t),
                    ctypes.byref(user_t),
                )
                if not ok:
                    return None
                return f"{creation.dwHighDateTime:x}{creation.dwLowDateTime:x}"
            finally:
                kernel32.CloseHandle(handle)
        # POSIX: field 22 of /proc/<pid>/stat is the process start time.
        with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as fh:
            fields = fh.read().rsplit(")", 1)[-1].split()
        return fields[19] if len(fields) > 19 else None
    except Exception:  # noqa: BLE001 - "cannot tell", never "dead"
        return None


class LockRecord:
    """Parsed lock file: ``status`` is ``ok`` / ``absent`` / ``corrupt``."""

    __slots__ = ("status", "pid", "acquired_at", "token", "error")

    def __init__(self, status, pid=None, acquired_at=None, token=None, error=None):
        self.status = status
        self.pid = pid
        self.acquired_at = acquired_at
        self.token = token
        self.error = error


def _read_record() -> LockRecord:
    """Read and validate the lock file.

    A file that exists but cannot be parsed is reported as ``corrupt`` - *not*
    as absent - so callers never mistake damage for freedom.
    """
    path = _lock_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip().splitlines()
    except FileNotFoundError:
        return LockRecord("absent")
    except Exception as exc:  # noqa: BLE001
        return LockRecord("corrupt", error=f"unreadable: {exc}")
    if not raw or not raw[0].strip():
        return LockRecord("corrupt", error="empty lock file")
    try:
        pid = int(raw[0].strip())
    except ValueError:
        return LockRecord("corrupt", error=f"unparsable owner {raw[0]!r}")
    if pid <= 0:
        return LockRecord("corrupt", error=f"non-positive owner {pid}")
    acquired_at = None
    if len(raw) > 1:
        try:
            acquired_at = float(raw[1].strip())
        except ValueError:
            acquired_at = None
    token = raw[2].strip() if len(raw) > 2 and raw[2].strip() else None
    return LockRecord("ok", pid=pid, acquired_at=acquired_at, token=token)


def read_owner() -> int | None:
    """Return the pid recorded as the current scheduler owner, or ``None``."""
    record = _read_record()
    return record.pid if record.status == "ok" else None


def owner_state() -> tuple[str, LockRecord]:
    """Tri-state ownership: ``absent`` / ``alive`` / ``dead`` / ``unknown``.

    ``unknown`` covers both a corrupt file and a live-looking pid whose identity
    cannot be confirmed. Callers must not reclaim on ``unknown``.
    """
    record = _read_record()
    if record.status == "absent":
        return OWNER_ABSENT, record
    if record.status == "corrupt":
        return OWNER_UNKNOWN, record
    pid = record.pid
    if pid == os.getpid():
        return OWNER_ALIVE, record
    if not _pid_alive(pid):
        return OWNER_DEAD, record
    # The pid is alive - but is it the *same* process that took the lock? Only
    # answer "dead" when we can prove the identity changed; otherwise stay
    # conservative and say "alive".
    if record.token:
        live_token = _process_create_token(pid)
        if live_token is not None and live_token != record.token:
            logger.warning(
                "scheduler lock pid=%s was recycled (recorded token %s, live %s)",
                pid,
                record.token,
                live_token,
            )
            return OWNER_DEAD, record
    return OWNER_ALIVE, record


def owner_is_alive() -> bool:
    """Whether a *different* live process currently owns the lock."""
    state, record = owner_state()
    return bool(state == OWNER_ALIVE and record.pid != os.getpid())


def _payload() -> str:
    token = _process_create_token(os.getpid()) or ""
    return f"{os.getpid()}\n{time.time()}\n{token}\n"


def _create_exclusive() -> bool:
    """Atomically create the lock file. ``False`` if it already existed."""
    os.makedirs(DB_DIR, exist_ok=True)
    fd = os.open(_lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_payload())
    return True


def _replace_and_verify() -> bool:
    """Reclaim a dead owner's lock atomically, then confirm we still own it.

    ``os.replace`` is atomic, but two processes may both decide the *previous*
    owner is dead and both replace. Re-reading and checking that the file names
    *us* turns the write into a compare-and-swap: exactly one racer can win.
    """
    tmp = f"{_lock_path()}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_payload())
    os.replace(tmp, _lock_path())
    record = _read_record()
    return record.status == "ok" and record.pid == os.getpid()


def acquire_detailed() -> tuple[bool, int | None, str]:
    """Claim the lock, returning ``(acquired, existing_owner_pid, reason)``.

    ``reason`` is a short, operator-safe explanation suitable for a health
    surface. Reclamation happens only for a *confirmed dead* owner; an
    ``unknown`` owner is refused so a corrupt file can never be silently
    clobbered (see the module docstring).
    """
    state, record = owner_state()
    existing = record.pid if record.status == "ok" else None

    if state == OWNER_ALIVE and record.pid != os.getpid():
        return False, existing, f"live owner pid={record.pid}"
    if state == OWNER_UNKNOWN:
        detail = record.error or "owner identity could not be confirmed"
        return False, existing, f"uncertain lock ({detail}); refusing to reclaim"

    try:
        if state == OWNER_ABSENT:
            try:
                _create_exclusive()
                return True, None, "acquired free lock"
            except FileExistsError:
                # Another process created it between our read and our create.
                raced = _read_record()
                return (
                    False,
                    raced.pid if raced.status == "ok" else None,
                    "lost race for a newly created lock",
                )
        # OWNER_DEAD, or we already hold it: replace atomically and verify.
        if state == OWNER_DEAD:
            logger.info(
                "scheduler lock: reclaiming stale lock from dead pid=%s", existing
            )
        if _replace_and_verify():
            return (
                True,
                existing,
                "reclaimed stale lock" if state == OWNER_DEAD else "reacquired",
            )
        raced = _read_record()
        return (
            False,
            raced.pid if raced.status == "ok" else None,
            "lost race while reclaiming a stale lock",
        )
    except Exception as exc:  # noqa: BLE001 - advisory only
        logger.debug("scheduler lock acquire failed: %s", exc)
        return True, None, "acquired (lock write failed; advisory)"


def acquire() -> tuple[bool, int | None]:
    """Claim the scheduler lock.

    Returns ``(acquired, existing_owner_pid)``. A lock left behind by a
    *confirmed dead* process is reclaimed; an uncertain one is refused. Use
    :func:`acquire_detailed` when the refusal reason needs surfacing.
    """
    acquired, existing, _reason = acquire_detailed()
    return acquired, existing


def release() -> None:
    """Release the lock, but only if this process provably owns it.

    Deliberately stricter than "owner is ``None`` or us": a corrupt or
    unreadable file must not be deleted by a process that never held it.
    """
    try:
        record = _read_record()
        if record.status == "absent":
            return
        if record.status == "corrupt":
            logger.warning(
                "scheduler lock not released: file is unreadable (%s)", record.error
            )
            return
        if record.pid != os.getpid():
            logger.warning(
                "scheduler lock not released: owned by pid=%s, not us", record.pid
            )
            return
        os.remove(_lock_path())
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("scheduler lock release failed: %s", exc)
