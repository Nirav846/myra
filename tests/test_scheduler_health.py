"""Phase 13: scheduler *running*, and safe stale-lock reclamation.

The incident this guards against:

1. a process was killed hard, leaving a lock file behind;
2. ``_pid_alive`` failed **closed** (any error => "probably alive"), so the
   restarted API concluded a live scheduler existed and skipped starting one;
3. the API answered ``/api/health`` with HTTP 200 throughout, so "serving" was
   mistaken for "scheduling".

These tests use isolated temp lock files and injected process/DB state. They
must never touch the real ``.myra_scheduler.pid`` or the live databases.
"""

import os

import pytest

from myra_app import scheduler_health as sh
from myra_app.utils import scheduler_lock as sl

# --------------------------------------------------------------------------- #
# Lock hardening: the "uncertain owner" case that caused the incident
# --------------------------------------------------------------------------- #


@pytest.fixture()
def tmp_lock(tmp_path, monkeypatch):
    """Point the lock at a temp dir and clear its memoised state."""
    monkeypatch.setattr(sl, "DB_DIR", str(tmp_path))
    sh.reset_cache()
    yield sl._lock_path()
    sh.reset_cache()


def _write_lock(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestLockHardening:
    def test_dead_owner_is_reclaimed(self, tmp_lock, monkeypatch):
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n")
        monkeypatch.setattr(sl, "_pid_alive", lambda pid: False)
        acquired, existing, reason = sl.acquire_detailed()
        assert acquired is True
        assert existing == 999999
        assert "stale" in reason
        assert sl.read_owner() == os.getpid()

    def test_live_owner_refused(self, tmp_lock, monkeypatch):
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n")
        monkeypatch.setattr(sl, "_pid_alive", lambda pid: True)
        acquired, existing, reason = sl.acquire_detailed()
        assert acquired is False
        assert existing == 999999
        assert "live owner" in reason

    def test_unreadable_lock_is_refused_not_clobbered(self, tmp_lock, monkeypatch):
        """The incident: a file we cannot interpret must not be silently stolen.

        Reclamation requires *confirmed death*; an error must not be read as
        death, because that is exactly how a healthy lock gets destroyed.
        """
        with open(tmp_lock, "wb") as fh:
            fh.write(b"\xff\xfe\x00garbage")
        monkeypatch.setattr(sl, "_pid_alive", lambda pid: True)

        acquired, existing, reason = sl.acquire_detailed()
        assert acquired is False
        assert "uncertain" in reason or "refusing" in reason
        # And the file is still there - not deleted, not overwritten.
        assert os.path.exists(tmp_lock)

    def test_owner_state_reports_absent_for_missing_file(self, tmp_lock):
        state, record = sl.owner_state()
        assert state == sl.OWNER_ABSENT
        assert record.status == "absent"

    def test_owner_state_reports_corrupt(self, tmp_lock):
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("not-an-int\n")
        state, record = sl.owner_state()
        assert state == sl.OWNER_UNKNOWN
        assert record.status == "corrupt"

    def test_backward_compatible_two_line_format(self, tmp_lock, monkeypatch):
        """Pre-existing locks (pid, timestamp only) must still parse."""
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n12345.0\n")
        assert sl.read_owner() == os.getpid()

    def test_acquire_free_lock(self, tmp_lock):
        acquired, existing = sl.acquire()
        assert acquired is True
        assert existing is None
        assert sl.read_owner() == os.getpid()

    def test_release_only_removes_own_lock(self, tmp_lock):
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n")
        sl.release()  # we do not own it -> must survive
        assert os.path.exists(tmp_lock)

    def test_release_refuses_to_delete_corrupt_file(self, tmp_lock):
        with open(tmp_lock, "wb") as fh:
            fh.write(b"\xff\xfebad")
        sl.release()
        assert os.path.exists(tmp_lock)

    def test_release_removes_own_lock(self, tmp_lock):
        sl.acquire()
        assert os.path.exists(tmp_lock)
        sl.release()
        assert not os.path.exists(tmp_lock)

    def test_pid_reuse_is_detected_as_not_ours(self, tmp_lock, monkeypatch):
        """A recycled PID must not be mistaken for the original owner.

        The token records the process *start* time, so a live PID whose start
        time differs from the recorded one is a different process.
        """
        monkeypatch.setattr(sl, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(sl, "_process_create_token", lambda pid: "1111111111.0")
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n9999999999.0\n")
        state, _record = sl.owner_state()
        assert state == sl.OWNER_DEAD

    def test_recorded_token_matching_live_is_alive(self, tmp_lock, monkeypatch):
        """Same PID *and* same start time => genuinely the same process."""
        monkeypatch.setattr(sl, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(sl, "_process_create_token", lambda pid: "1111111111.0")
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n1111111111.0\n")
        state, _record = sl.owner_state()
        assert state == sl.OWNER_ALIVE

    def test_legacy_lock_without_token_stays_alive(self, tmp_lock, monkeypatch):
        """Pre-Phase-13 locks have no token; a live PID must read as alive."""
        monkeypatch.setattr(sl, "_pid_alive", lambda pid: True)
        with open(tmp_lock, "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n")
        state, _record = sl.owner_state()
        assert state == sl.OWNER_ALIVE

    def test_acquire_detailed_survives_write_failure(self, tmp_lock, monkeypatch):
        """A broken lock path must not take the scheduler down with it."""

        def boom(*_a, **_k):
            raise OSError("disk gone")

        monkeypatch.setattr(sl, "_replace_and_verify", boom)
        monkeypatch.setattr(sl, "_create_exclusive", boom)
        monkeypatch.setattr(
            sl, "owner_state", lambda: (sl.OWNER_ABSENT, sl.LockRecord("absent"))
        )
        acquired, _existing, reason = sl.acquire_detailed()
        assert acquired is True
        assert "advisory" in reason


# --------------------------------------------------------------------------- #
# Health states
# --------------------------------------------------------------------------- #


def _state(**kw):
    kw.setdefault("probe", False)
    return sh.get_state(**kw)["state"]


class TestSchedulerHealthStates:
    def test_degraded_when_nothing_owns_the_lock(self):
        assert _state(now=1000.0, ownership=sl.OWNER_ABSENT) == sh.STATE_DEGRADED

    def test_disabled_when_operator_opts_out(self, monkeypatch):
        monkeypatch.setattr(sh.constants, "DISABLE_SCHEDULER", True)
        assert _state(now=1000.0, ownership=sl.OWNER_ABSENT) == sh.STATE_DISABLED

    def test_unknown_when_lock_is_unreadable(self):
        rec = sl.LockRecord("corrupt", error="bad bytes")
        assert (
            _state(now=1000.0, ownership=sl.OWNER_UNKNOWN, record=rec)
            == sh.STATE_UNKNOWN
        )

    def test_unknown_when_registry_is_unreadable(self):
        rec = sl.LockRecord("ok", pid=os.getpid(), acquired_at=900.0)
        assert (
            _state(
                now=1000.0,
                ownership=sl.OWNER_ALIVE,
                record=rec,
                tick_known=False,
            )
            == sh.STATE_UNKNOWN
        )

    def test_running_when_we_own_it_and_heartbeat_is_fresh(self):
        rec = sl.LockRecord("ok", pid=os.getpid(), acquired_at=900.0)
        assert (
            _state(
                now=1000.0,
                ownership=sl.OWNER_ALIVE,
                record=rec,
                last_tick=995.0,
            )
            == sh.STATE_RUNNING
        )

    def test_running_when_another_live_process_owns_it(self):
        rec = sl.LockRecord("ok", pid=999999, acquired_at=900.0)
        assert (
            _state(
                now=1000.0,
                ownership=sl.OWNER_ALIVE,
                record=rec,
                last_tick=995.0,
            )
            == sh.STATE_RUNNING
        )

    def test_starting_within_grace_period(self):
        rec = sl.LockRecord("ok", pid=os.getpid(), acquired_at=1000.0)
        assert (
            _state(now=1010.0, ownership=sl.OWNER_ALIVE, record=rec, last_tick=None)
            == sh.STATE_STARTING
        )

    def test_degraded_when_heartbeat_stalls(self):
        """The original symptom: we hold the lock but the watchdog stopped."""
        rec = sl.LockRecord("ok", pid=os.getpid(), acquired_at=900.0)
        assert (
            _state(
                now=1000.0,
                ownership=sl.OWNER_ALIVE,
                record=rec,
                last_tick=1000.0 - sh.WATCHDOG_STALE_AFTER_SECONDS - 1,
            )
            == sh.STATE_DEGRADED
        )

    def test_degraded_when_watchdog_never_ticked_past_grace(self):
        rec = sl.LockRecord("ok", pid=os.getpid(), acquired_at=1000.0)
        assert (
            _state(
                now=1000.0 + sh.STARTUP_GRACE_SECONDS + 1,
                ownership=sl.OWNER_ALIVE,
                record=rec,
                last_tick=None,
            )
            == sh.STATE_DEGRADED
        )

    def test_degraded_is_not_healthy(self):
        """A serving-but-not-scheduling process must not read as healthy."""
        assert sh.healthy(sh.STATE_DEGRADED) is False
        assert sh.healthy(sh.STATE_UNKNOWN) is False
        assert sh.healthy(sh.STATE_RUNNING) is True
        assert sh.healthy(sh.STATE_DISABLED) is True

    def test_state_never_raises(self, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("registry on fire")

        monkeypatch.setattr(sl, "owner_state", boom)
        sh.reset_cache()
        result = sh.get_state_cached()
        assert result["state"] == sh.STATE_UNKNOWN
        assert "reason" in result


# --------------------------------------------------------------------------- #
# Orchestrator refusal must be observable, not just logged
# --------------------------------------------------------------------------- #


class TestOrchestratorRefusalIsObservable:
    def _bo(self):
        import myra_app.background_orchestrator as bo

        return bo

    def test_disabled_scheduler_records_refusal(self, monkeypatch):
        import myra_app.background_orchestrator as bo

        monkeypatch.setattr(bo, "DISABLE_SCHEDULER", True)
        monkeypatch.setattr(bo, "_started", False)
        monkeypatch.setattr(bo, "_last_start_refusal", None)
        assert bo.start(register_signals=False) is False
        refusal = bo.get_last_start_refusal()
        assert refusal is not None
        assert refusal["reason"] == "disabled"

    def test_held_lock_is_recorded(self, tmp_lock, monkeypatch):
        import myra_app.background_orchestrator as bo

        monkeypatch.setattr(bo, "DISABLE_SCHEDULER", False)
        monkeypatch.setattr(bo, "_started", False)
        monkeypatch.setattr(bo, "_last_start_refusal", None)
        monkeypatch.setattr(
            sl, "acquire_detailed", lambda: (False, 4242, "live owner pid=4242")
        )
        assert bo.start(register_signals=False) is False
        refusal = bo.get_last_start_refusal()
        assert refusal is not None
        assert refusal["reason"] == "lock_held"
        assert "4242" in refusal["detail"]
        assert refusal["pid"] == os.getpid()
