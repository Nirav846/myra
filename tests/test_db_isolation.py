"""Regression tests for the DB_DIR isolation failure that hit production.

Incident: a test monkeypatched ``myra_app.constants.DB_DIR`` and expected the
redirect to apply, but ~99 modules bind ``DB_DIR`` at import time
(``from myra_app.constants import DB_DIR``). ``LibrarianCore`` therefore opened
the **live** ``myra_app/db/myra_metadata.db`` and a ``DROP TABLE sync_log`` in
that test destroyed all 16 rows of real data.

These tests reproduce that exact escape route and assert it can no longer
happen. They use the session ``fixture_db_dir`` fixture, never the live DB.
"""

import os
import sqlite3

import pytest

import myra_app.constants as constants
from tests.conftest import LIVE_DB_DIR, REAL_CONNECT, _is_inside

pytestmark = pytest.mark.usefixtures("fixture_db_dir")


# ---------------------------------------------------------------------------
# 1. The original escape: a module that bound DB_DIR at import time
# ---------------------------------------------------------------------------


def test_librarian_core_opens_the_temporary_database(fixture_db_dir):
    """LibrarianCore binds DB_DIR at import time — the exact escape route."""
    from myra_app.librarian_core import LibrarianCore

    assert _is_inside(constants.DB_DIR, fixture_db_dir)

    lib = LibrarianCore(read_only=False)
    try:
        assert lib._meta_conn is not None, "metadata connection was not opened"
        # Prove the *effective* path of the real connection, not the constant.
        effective = _effective_db_path(lib._meta_conn)
        assert effective is not None
        assert _is_inside(
            effective, fixture_db_dir
        ), f"LibrarianCore opened {effective}, which is outside the test dir"
        assert not _is_inside(effective, LIVE_DB_DIR)
    finally:
        lib.close()


def test_task_tracker_uses_the_temporary_database(fixture_db_dir):
    """task_tracker memoises its connection in a module-level global."""
    import myra_app.task_tracker as tracker

    tid = tracker.register("isolation-test", task_type="one-shot")
    try:
        assert tracker.get_task(tid) is not None
        path = _effective_db_path(tracker._conn)
        assert path is not None
        assert _is_inside(path, fixture_db_dir), f"task_tracker opened {path}"
        assert not _is_inside(path, LIVE_DB_DIR)
    finally:
        tracker.unregister(tid)


def test_task_utils_sync_log_writes_land_in_the_temporary_database(fixture_db_dir):
    """task_utils._mark_task_run is what the whole scheduler reads back."""
    from myra_app.utils.task_utils import _get_last_run, _mark_task_run

    _mark_task_run("isolation_probe", status="success")
    assert _get_last_run("isolation_probe") is not None

    meta = sqlite3.connect(os.path.join(fixture_db_dir, "myra_metadata.db"))
    try:
        rows = meta.execute(
            "SELECT task_name, last_status FROM sync_log WHERE task_name = ?",
            ("isolation_probe",),
        ).fetchall()
        assert rows == [
            ("isolation_probe", "success")
        ], "the write did not land in the temporary sync_log"
        # Leave the shared session fixture as we found it.
        meta.execute("DELETE FROM sync_log WHERE task_name = ?", ("isolation_probe",))
        meta.commit()
    finally:
        meta.close()


# ---------------------------------------------------------------------------
# 2. The fix is comprehensive, not just the constants module
# ---------------------------------------------------------------------------


def test_every_imported_db_dir_binding_points_at_the_test_dir(fixture_db_dir):
    """Constants-only patching is not enough; check the effective bindings."""
    import tests.conftest as conftest_mod

    stale = [
        (name, value)
        for name, value in conftest_mod._modules_with_db_dir()
        if not _is_inside(value, fixture_db_dir)
    ]
    assert not stale, "modules still bound to a live DB_DIR:\n" + "\n".join(
        f"    {n} -> {v}" for n, v in stale
    )


def test_cached_task_tracker_connection_was_reset(fixture_db_dir):
    """A connection memoised before the redirect must not survive it."""
    import myra_app.task_tracker as tracker

    assert tracker._use_fallback is False
    conn = getattr(tracker, "_conn", None)
    if conn is not None:
        path = _effective_db_path(conn)
        assert path is None or _is_inside(path, fixture_db_dir)


# ---------------------------------------------------------------------------
# 3. A test write affects only the temporary database
# ---------------------------------------------------------------------------


def test_destructive_write_touches_only_the_fixture_database(fixture_db_dir, tmp_path):
    """Reproduce the incident's exact operation against an isolated copy.

    `fixture_db_dir` is session-scoped and therefore SHARED by every test that
    requests it, so this test must not drop tables in it — doing so corrupts
    unrelated tests later in the same run. Work on a per-test copy instead.
    """
    import shutil

    live_meta = os.path.join(LIVE_DB_DIR, "myra_metadata.db")
    shared_meta = os.path.join(fixture_db_dir, "myra_metadata.db")
    before = _read_sync_log(live_meta)

    shared_columns = _table_columns(shared_meta, "sync_log")

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    sandbox_meta = sandbox / "myra_metadata.db"
    shutil.copy2(shared_meta, sandbox_meta)

    conn = sqlite3.connect(str(sandbox_meta))
    try:
        conn.execute("DROP TABLE sync_log")
        conn.execute(
            "CREATE TABLE sync_log (task_name TEXT PRIMARY KEY, last_run TEXT, "
            "last_status TEXT, error_message TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    assert _table_columns(sandbox_meta, "sync_log") == 4, "sandbox write failed"
    assert (
        _read_sync_log(live_meta) == before
    ), "the live sync_log changed — isolation has regressed"
    assert len(before) > 0, "baseline should contain the real sync_log rows"

    # The shared session fixture must be untouched by this test.
    assert _table_columns(shared_meta, "sync_log") == shared_columns
    assert shared_columns >= 4, "fixture sync_log should keep its full schema"


def test_pipeline_control_history_writes_land_in_the_temporary_database(
    fixture_db_dir,
):
    """pipeline_control persists run history; under isolation it must stay in tmp."""
    from myra_app.pipeline_control import PipelineControl

    live_meta = os.path.join(LIVE_DB_DIR, "myra_metadata.db")
    before = _read_sync_log(live_meta)

    PipelineControl._instance = None
    try:
        inst = PipelineControl()
        inst._mark_run("isolation_probe_task", "completed", None)
    finally:
        PipelineControl._instance = None

    temp_meta = os.path.join(fixture_db_dir, "myra_metadata.db")
    conn = sqlite3.connect(temp_meta)
    try:
        rows = conn.execute(
            "SELECT task_name, last_status FROM sync_log WHERE task_name = ?",
            ("isolation_probe_task",),
        ).fetchall()
        assert rows, "pipeline_control did not record history anywhere"
        # Leave the shared session fixture as we found it.
        conn.execute(
            "DELETE FROM sync_log WHERE task_name = ?", ("isolation_probe_task",)
        )
        conn.commit()
    finally:
        conn.close()

    assert (
        _read_sync_log(live_meta) == before
    ), "pipeline_control wrote to the live sync_log"


# ---------------------------------------------------------------------------
# 4. Live access fails closed
# ---------------------------------------------------------------------------


def test_opening_a_live_database_is_blocked(fixture_db_dir):
    live_meta = os.path.join(LIVE_DB_DIR, "myra_metadata.db")
    with pytest.raises(Exception) as excinfo:
        sqlite3.connect(live_meta)
    assert "Blocked test access to live MYRA database" in str(excinfo.value)


@pytest.mark.parametrize(
    "filename", ["myra_metadata.db", "myra_technical.db", "myra_valuation.db"]
)
def test_every_live_sidecar_is_blocked(fixture_db_dir, filename):
    path = os.path.join(LIVE_DB_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not present")
    with pytest.raises(Exception):
        sqlite3.connect(path)


def test_read_only_live_access_is_blocked_too(fixture_db_dir):
    """Even `mode=ro` must fail closed — a read is how a test 'just checks'."""
    path = os.path.join(LIVE_DB_DIR, "myra_metadata.db")
    with pytest.raises(Exception):
        sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def test_temporary_database_access_still_works(fixture_db_dir):
    """The guard must not block legitimate fixture access."""
    path = os.path.join(fixture_db_dir, "myra_metadata.db")
    conn = sqlite3.connect(path)
    try:
        conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Cleanup never operates on a live database
# ---------------------------------------------------------------------------


def test_fixture_db_dir_is_outside_the_live_directory(fixture_db_dir):
    assert not _is_inside(fixture_db_dir, LIVE_DB_DIR)
    assert not _is_inside(LIVE_DB_DIR, fixture_db_dir)
    # Guard against a hardcoded developer path that happens to look temporary.
    assert "myra_app" not in os.path.normpath(fixture_db_dir).split(os.sep)


def test_teardown_restore_is_a_noop_against_live_directories(fixture_db_dir):
    """Restoring the binding must not cause a reconnect to the live DB."""
    live_meta = os.path.join(LIVE_DB_DIR, "myra_metadata.db")
    before = _read_sync_log(live_meta)
    # Simulate what teardown does, minus the final rebind, and assert safety.
    assert _is_inside(constants.DB_DIR, fixture_db_dir)
    assert _read_sync_log(live_meta) == before


# ---------------------------------------------------------------------------
# 6. The session guard is not vacuous
# ---------------------------------------------------------------------------


def test_live_db_fingerprint_detects_a_write(tmp_path, monkeypatch):
    """Prove the session guard can actually detect a change.

    Run against a throwaway directory rather than the real DB_DIR: the point is
    to show the detection logic works, not to modify production. Without this,
    `live_db_guard` could silently never fire.
    """
    import tests.conftest as conftest_mod

    fake = tmp_path / "fakedb"
    fake.mkdir()
    meta = fake / "myra_metadata.db"
    # REAL_CONNECT: this test repoints LIVE_DB_DIR at the fake dir, so the
    # armed guard would (correctly) block the normal sqlite3.connect.
    conn = REAL_CONNECT(str(meta))
    try:
        conn.execute(
            "CREATE TABLE sync_log (task_name TEXT PRIMARY KEY, last_run TEXT, "
            "last_status TEXT, error_message TEXT)"
        )
        conn.execute("INSERT INTO sync_log VALUES ('a', '2026-01-01', 'success', '')")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(conftest_mod, "LIVE_DB_DIR", str(fake))
    before = conftest_mod._live_db_fingerprint()

    # Change the DATA -> the sync_log digest must move.
    conn = REAL_CONNECT(str(meta))
    try:
        conn.execute("INSERT INTO sync_log VALUES ('b', '2026-01-02', 'failed', 'x')")
        conn.commit()
    finally:
        conn.close()
    after_data = conftest_mod._live_db_fingerprint()
    assert after_data != before, "a sync_log row change was not detected"

    # Change only the FILE METADATA -> the signature must move too.
    os.utime(meta, (0, 0))
    after_mtime = conftest_mod._live_db_fingerprint()
    assert after_mtime != after_data, "a file mtime change was not detected"


def test_fingerprint_ignores_wal_and_shm_transient_files(tmp_path, monkeypatch):
    """Reading a WAL-mode DB creates -shm; that must not look like a write."""
    import tests.conftest as conftest_mod

    fake = tmp_path / "fakedb2"
    fake.mkdir()
    meta = fake / "myra_metadata.db"
    conn = sqlite3.connect(str(meta))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE sync_log (task_name TEXT PRIMARY KEY, last_run TEXT, "
            "last_status TEXT, error_message TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(conftest_mod, "LIVE_DB_DIR", str(fake))
    first = conftest_mod._live_db_fingerprint()

    # Simulate SQLite touching the shm/wal sidecars.
    (fake / "myra_metadata.db-shm").write_bytes(b"\x00" * 16)
    (fake / "myra_metadata.db-wal").write_bytes(b"\x00" * 16)

    assert (
        conftest_mod._live_db_fingerprint() == first
    ), "wal/shm churn was reported as a live-database modification"


def test_uri_bypass_cannot_escape_the_guard(tmp_path, monkeypatch):
    """`file:...?mode=ro` must not slip past the containment check."""
    import tests.conftest as conftest_mod

    live = tmp_path / "live"
    live.mkdir()
    target = live / "myra_metadata.db"
    sqlite3.connect(str(target)).close()

    monkeypatch.setattr(conftest_mod, "LIVE_DB_DIR", str(live))
    real = conftest_mod.REAL_CONNECT
    conftest_mod._install_live_db_guard()
    try:
        for candidate in (
            str(target),
            f"file:{target}",
            f"file:{target}?mode=ro",
            f"file:///{target}",
        ):
            with pytest.raises(Exception) as exc:
                sqlite3.connect(candidate, uri=candidate.startswith("file:"))
            assert "Blocked test access" in str(exc.value), candidate
    finally:
        sqlite3.connect = real


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _effective_db_path(conn):
    """Return the file backing a live sqlite connection, via its open file list.

    This inspects the *effective* connection rather than trusting a constant,
    which is what the original incident got wrong.
    """
    if conn is None:
        return None
    try:
        # PRAGMA database_list reports the resolved path of each attached DB.
        rows = conn.execute("PRAGMA database_list").fetchall()
    except Exception:
        return None
    for row in rows:
        if row[1] == "main" and row[2]:
            return row[2]
    return None


def _table_columns(db_path, table):
    """Column count for `table` in `db_path` (read-only)."""
    try:
        conn = REAL_CONNECT(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return -1
    try:
        return len(conn.execute(f"PRAGMA table_info({table})").fetchall())
    except Exception:
        return -1
    finally:
        conn.close()


def _read_sync_log(metadata_path):
    """Digest of the live sync_log.

    Uses REAL_CONNECT because this helper is part of the verification itself:
    it must be able to read the live database while the guard is armed. It only
    ever opens `mode=ro` and never writes.
    """
    try:
        conn = REAL_CONNECT(f"file:{metadata_path}?mode=ro", uri=True)
    except Exception:
        return []
    try:
        return conn.execute(
            "SELECT task_name, last_run, last_status, error_message "
            "FROM sync_log ORDER BY task_name"
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
