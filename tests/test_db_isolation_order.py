"""Regression tests for the order-dependent DB isolation defect.

Defect: ``fixture_db_dir`` in ``tests/conftest.py`` was **session-scoped**, so
its ``DB_DIR`` redirect (and the live-access guard it installs) stayed in place
from the first requesting test until session teardown. Every test that ran
afterwards -- isolated or not -- resolved ``DB_DIR`` to the synthetic fixture
directory. Modules that resolve database paths at *runtime*
(``myra_app.db.bulk_loader``, the ``/api/chart`` endpoint) then read fixture
data instead of the live databases. pytest's alphabetical collection order hid
it because ``test_db_isolation`` sorts after ``test_bulk_loader`` and the chart
tests, so the starvation only appeared when ``test_db_isolation`` ran first.

These tests pin the fixed contract:

* the redirect applies only while an isolated test runs, and is fully undone
  afterwards (``DB_DIR``, every import-time binding, and ``sqlite3.connect``);
* the live-access guard is armed for every isolated test, even if a previous
  test removed it;
* the fixture DBs are non-empty and expose the tables the tests rely on, so an
  "exists but empty" fixture cannot make these tests pass vacuously.

The restore checks below run *after* the isolated tests above them in this
module; with the old session-scoped fixture they would observe the leaked
fixture directory and fail.
"""

import os
import sqlite3

import pytest

import myra_app.constants as constants
import myra_app.db.bulk_loader as bulk_loader
from tests.conftest import (
    LIVE_DB_DIR,
    REAL_CONNECT,
    _is_inside,
    _modules_with_db_dir,
    _REQUIRED_FIXTURE_TABLES,
)


def test_fixture_databases_are_populated(fixture_db_dir):
    """Every fixture DB must exist, be non-empty, and carry its key tables."""
    from myra_app.librarian_core import LibrarianCore

    for db_key, filename in LibrarianCore.DB_MAP.items():
        path = os.path.join(fixture_db_dir, filename)
        assert os.path.exists(path), f"missing fixture DB {filename}"
        conn = sqlite3.connect(path)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        assert names, f"fixture DB {filename} has no tables (would pass vacuously)"
        expected = _REQUIRED_FIXTURE_TABLES.get(db_key)
        if expected:
            assert expected in names, f"fixture DB {filename} is missing {expected}"


def test_isolated_test_sees_redirect_and_armed_guard(fixture_db_dir):
    """While isolated: DB_DIR is redirected and live access is blocked."""
    assert _is_inside(constants.DB_DIR, fixture_db_dir)
    assert sqlite3.connect is not REAL_CONNECT, "live-access guard is not armed"

    live_meta = os.path.join(LIVE_DB_DIR, "myra_metadata.db")
    with pytest.raises(Exception) as excinfo:
        sqlite3.connect(live_meta)
    assert "Blocked test access" in str(excinfo.value)


_FIXTURE_DIRS_SEEN = []


def test_fixture_directory_identity_is_stable_across_isolated_tests(fixture_db_dir):
    """The redirect target stays put for the whole session.

    A per-test directory would break callers that cache ``LibrarianCore``
    instances by thread name rather than path (the orchestrator / ingest /
    watchdog pools), so the directory identity is intentionally session-stable
    while the *redirect* is per-test.
    """
    here = os.path.abspath(fixture_db_dir)
    assert os.path.basename(here).startswith("myra_fixture_dbs")
    if _FIXTURE_DIRS_SEEN:
        assert here == _FIXTURE_DIRS_SEEN[0], "fixture dir changed between tests"
    _FIXTURE_DIRS_SEEN.append(here)


# ---------------------------------------------------------------------------
# Restore / no-leak checks -- these MUST run after the isolated tests above.
# ---------------------------------------------------------------------------


def test_globals_are_restored_after_isolated_tests():
    """A non-isolated test after an isolated one must see the live state.

    This is the core regression: under the session-scoped fixture this test saw
    ``DB_DIR`` still pointing at the fixture directory.
    """
    assert os.path.abspath(constants.DB_DIR) == LIVE_DB_DIR
    assert sqlite3.connect is REAL_CONNECT, "guard was left installed after teardown"
    for name, value in _modules_with_db_dir():
        assert "myra_fixture_dbs" not in value, f"{name}.DB_DIR leaked: {value}"


def test_runtime_db_path_resolves_to_live_after_isolation():
    """The exact module that was starved: the bulk loader's runtime path."""
    path = bulk_loader._tech_db_path()
    assert _is_inside(path, LIVE_DB_DIR)
    assert "myra_fixture_dbs" not in path


def test_guard_removed_by_a_test_is_repaired_by_the_next_isolated_test():
    """Mimic ``test_uri_bypass_cannot_escape_the_guard`` restoring real connect.

    That test deliberately installs the guard and then restores the real
    ``sqlite3.connect``. With a session-scoped fixture the process was left
    unguarded for the rest of the session; the next isolated test must re-arm
    it (asserted immediately below).
    """
    sqlite3.connect = REAL_CONNECT
    assert sqlite3.connect is REAL_CONNECT


def test_isolated_test_after_guard_removal_is_protected_again(fixture_db_dir):
    assert sqlite3.connect is not REAL_CONNECT, "guard was not re-armed"
    with pytest.raises(Exception):
        sqlite3.connect(os.path.join(LIVE_DB_DIR, "myra_metadata.db"))
