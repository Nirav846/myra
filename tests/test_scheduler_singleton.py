"""Single-scheduler + operator-pause tests.

These cover the "no conflicting pipeline" guarantees: one scheduler per process
(and per machine), and a persisted pause flag that the executor honours.
"""

import sqlite3

import pytest

from myra_app.utils import scheduler_lock
from myra_app.utils import task_utils


class TestSchedulerLock:
    @pytest.fixture(autouse=True)
    def _tmp_db_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_lock, "DB_DIR", str(tmp_path))

    def test_acquire_when_free_then_release(self):
        acquired, existing = scheduler_lock.acquire()
        assert acquired is True
        assert existing is None
        assert scheduler_lock.read_owner() is not None
        scheduler_lock.release()
        assert scheduler_lock.read_owner() is None

    def test_refuses_when_live_owner(self, monkeypatch):
        # Pretend another live process owns the lock.
        with open(scheduler_lock._lock_path(), "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n")
        monkeypatch.setattr(scheduler_lock, "_pid_alive", lambda pid: True)
        acquired, existing = scheduler_lock.acquire()
        assert acquired is False
        assert existing == 999999

    def test_reclaims_stale_lock(self, monkeypatch):
        with open(scheduler_lock._lock_path(), "w", encoding="utf-8") as fh:
            fh.write("999999\n0\n")
        monkeypatch.setattr(scheduler_lock, "_pid_alive", lambda pid: False)
        acquired, existing = scheduler_lock.acquire()
        assert acquired is True
        assert existing == 999999
        assert scheduler_lock.read_owner() != 999999

    def test_owner_is_alive_false_when_absent(self):
        assert scheduler_lock.owner_is_alive() is False


class TestOrchestratorSingleStart:
    def test_claim_start_is_idempotent(self, monkeypatch):
        import myra_app.background_orchestrator as bo

        monkeypatch.setattr(bo, "_started", False)
        assert bo._claim_start() is True
        assert bo._claim_start() is False  # second claim refused

    def test_claim_start_allowed_again_after_reset(self, monkeypatch):
        import myra_app.background_orchestrator as bo

        monkeypatch.setattr(bo, "_started", False)
        assert bo._claim_start() is True
        monkeypatch.setattr(bo, "_started", False)
        assert bo._claim_start() is True

    def test_attach_pause_event_is_shared(self, monkeypatch):
        import threading

        import myra_app.background_orchestrator as bo

        ev = threading.Event()
        monkeypatch.setattr(bo._CTX, "pause_event", None)
        bo.attach_pause_event(ev)
        assert bo._CTX.pause_event is ev


class TestSchedulePausePersistence:
    @pytest.fixture(autouse=True)
    def _tmp_meta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(task_utils, "DB_DIR", str(tmp_path))
        monkeypatch.setattr(
            task_utils, "_schedule_pause_cache", {"at": 0.0, "value": False}
        )
        conn = sqlite3.connect(task_utils._metadata_db_path())
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

    def test_default_is_not_paused(self):
        assert task_utils.schedule_paused(fresh=True) is False

    def test_set_true_round_trips(self):
        task_utils.set_schedule_paused(True)
        assert task_utils.schedule_paused(fresh=True) is True
        task_utils.set_schedule_paused(False)
        assert task_utils.schedule_paused(fresh=True) is False

    def test_reads_legacy_json_value(self):
        conn = sqlite3.connect(task_utils._metadata_db_path())
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (task_utils.SCHEDULE_PAUSE_META_KEY, "true"),
        )
        conn.commit()
        conn.close()
        assert task_utils.schedule_paused(fresh=True) is True


class TestExecutorHonoursPause:
    def _spec(self):
        from myra_app.tasks.registry import TaskSpec

        return TaskSpec(
            module="myra_app.tasks.ingest", label="fake_task", interval_days=1
        )

    def test_should_fire_false_when_paused(self, monkeypatch):
        from myra_app.tasks import executor

        monkeypatch.setattr(executor, "schedule_paused", lambda *a, **k: True)
        monkeypatch.setattr(executor, "_is_task_due", lambda *a, **k: True)
        monkeypatch.setattr(executor, "attempt_allowed", lambda *a, **k: True)
        assert executor.should_fire(self._spec()) is False

    def test_should_fire_true_when_due_and_unpaused(self, monkeypatch):
        from myra_app.tasks import executor

        monkeypatch.setattr(executor, "schedule_paused", lambda *a, **k: False)
        monkeypatch.setattr(executor, "_is_task_due", lambda *a, **k: True)
        monkeypatch.setattr(executor, "attempt_allowed", lambda *a, **k: True)
        assert executor.should_fire(self._spec()) is True
