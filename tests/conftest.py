"""Pytest configuration and database-isolation guards for MYRA.

Why this file is careful about DB paths
--------------------------------------
Roughly 99 modules bind ``DB_DIR`` at *import* time::

    from myra_app.constants import DB_DIR   # librarian_core.py:17, task_tracker.py:8,
                                           # task_utils.py:14, ~96 more

Rebinding ``myra_app.constants.DB_DIR`` therefore does **not** redirect those
modules: they keep the value captured when they were first imported. A previous
version of ``fixture_db_dir`` did exactly that, so a test that dropped a table
executed the DROP against the **live** ``myra_app/db/myra_metadata.db``.

Two layers of defence are implemented here:

1. ``fixture_db_dir`` redirects the binding *everywhere* — the constants module
   (so future imports pick it up), every already-imported module that captured
   the production value, and cached connection singletons such as
   ``task_tracker._conn``.
2. A session-wide ``live_db_guard`` autouse fixture fails the session if any
   live database file changes. This is behaviour-neutral for existing tests and
   catches the whole class of "a test wrote to production" bugs, including from
   tests that never requested the isolation fixture.
"""

import hashlib
import os
import re
import shutil
import sqlite3
import sys
from urllib.parse import unquote, urlparse

import pytest

# NOTE: `myra_app.constants` must not be imported before the env is seeded.
# myra_app/fundamental_sync.py:32 reads MORNINGSTAR_TOKEN at import time via
# os.environ[...] (bracket access, not .get()). Developers have a gitignored .env
# so load_dotenv fills it locally; a clean CI runner has no .env, so the
# unguarded read raises KeyError and aborts collection for every test in the
# module. The suite makes no Morningstar call, so the value is never transmitted.
os.environ.setdefault("MORNINGSTAR_TOKEN", "test-placeholder")

import myra_app.constants as constants  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402

#: The production database directory. Anything under this prefix is live data.
LIVE_DB_DIR = os.path.abspath(constants.DB_DIR)

#: The genuine sqlite3.connect, captured before any guard is installed.
#: Only the guard installer and the read-only verification helpers bypass the
#: guard; test code must go through the guarded sqlite3.connect.
REAL_CONNECT = sqlite3.connect


def _is_inside(path: str, root: str) -> bool:
    """True when `path` resolves inside `root` (the containment test we rely on)."""
    try:
        common = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
    except ValueError:  # different drives on Windows
        return False
    return common == os.path.abspath(root)


def _db_files() -> list[str]:
    """Live data files only.

    `-wal` and `-shm` are deliberately excluded: SQLite creates/updates them as a
    side effect of *reading* a WAL-mode database, so including them made this
    guard report a modification when nothing had been written.
    """
    if not os.path.isdir(LIVE_DB_DIR):
        return []
    return [
        os.path.join(LIVE_DB_DIR, name)
        for name in sorted(os.listdir(LIVE_DB_DIR))
        if name.endswith(".db")
    ]


def _db_signatures() -> dict[str, str]:
    """Per-database signature, so a modified run can name the offending file(s).

    Full content hashing is impossible here (technical.db is ~2 GB), so each
    signature is (size, mtime_ns) plus a real content digest of sync_log in
    myra_metadata.db, which is the database the original incident damaged.

    Limitation, stated plainly: a write that lands only in the WAL and does not
    checkpoint would not change a `.db` file's mtime. For myra_metadata.db that
    case is still caught, because the sync_log digest is read through SQLite and
    therefore sees committed WAL content.
    """
    signatures: dict[str, str] = {}
    for path in _db_files():
        name = os.path.basename(path)
        try:
            stat = os.stat(path)
        except OSError:
            signatures[name] = "missing"
            continue
        signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        if name == "myra_metadata.db":
            signature += f":sync_log_digest={_sync_log_digest(path)}"
        signatures[name] = signature
    return signatures


def _fingerprint_of(signatures: dict[str, str]) -> str:
    """Stable hash of a `_db_signatures()` map."""
    blob = "|".join(f"{name}={sig}" for name, sig in sorted(signatures.items()))
    return hashlib.sha256(blob.encode()).hexdigest()


def _live_db_fingerprint() -> str:
    """Cheap, stable fingerprint of every live database file.

    Retained as a no-argument helper (the isolation regression suite calls it
    directly); `_db_signatures()` carries the per-file detail.
    """
    return _fingerprint_of(_db_signatures())


def _changed_databases(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """One readable line per database whose signature changed."""
    return [
        f"    {name}: {before.get(name)} -> {after.get(name)}"
        for name in sorted(set(before) | set(after))
        if before.get(name) != after.get(name)
    ]


def _sync_log_digest(metadata_path: str) -> str:
    """Content digest of sync_log — the table the incident destroyed."""
    try:
        # Read-only URI mode: never creates or writes the database. Uses
        # REAL_CONNECT so it still works while the live-access guard is armed.
        conn = REAL_CONNECT(f"file:{metadata_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT task_name, last_run, last_status, error_message "
                "FROM sync_log ORDER BY task_name"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # unreadable is itself worth fingerprinting
        return f"error:{type(exc).__name__}"
    blob = "\n".join("|".join("" if v is None else str(v) for v in row) for row in rows)
    return f"{len(rows)}:{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# Session-wide guard: fail if any live database is modified during the run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def live_db_guard():
    """Fail the session if a test modifies any live MYRA database file.

    Deliberately behaviour-neutral: it only observes. Its purpose is to make
    the "a test wrote to production" failure mode loud and immediate rather
    than discovered days later.
    """
    before = _db_signatures()
    yield
    after = _db_signatures()
    if before != after:
        changed = "\n".join(_changed_databases(before, after))
        pytest.fail(
            "LIVE DATABASE MODIFIED DURING TEST RUN.\n"
            f"  changed files:\n{changed}\n"
            f"  fingerprint before: {_fingerprint_of(before)}\n"
            f"  fingerprint after : {_fingerprint_of(after)}\n"
            f"  live dir          : {LIVE_DB_DIR}\n"
            "A test opened or wrote a production database. Request the "
            "`fixture_db_dir` fixture (tests/conftest.py) so DB_DIR and any "
            "cached connections are redirected to a temporary directory. If "
            "MYRA is running locally, stop it first: the app's scheduler also "
            "writes to these files and will trip this guard."
        )


# ---------------------------------------------------------------------------
# Isolation fixture
# ---------------------------------------------------------------------------


def _reset_cached_connections() -> None:
    """Drop cached SQLite handles so nothing keeps a stale DB open.

    Two kinds of cached handle outlive a redirect:

    * ``myra_app.task_tracker`` memoises its connection in a module-level global
      (``_conn``). Redirecting DB_DIR is not enough if that handle was already
      created by an earlier test — it would keep pointing at the live file.
    * ``myra_app.background_orchestrator`` and the ``tasks.ingest`` /
      ``tasks.watchdog`` modules keep thread-keyed ``LibrarianCore`` pools
      (``_connection_pool``). A pool entry built while DB_DIR was redirected
      would otherwise be reused *after* the redirect is undone, silently reading
      the synthetic database from an unrelated test.

    Only modules that are already imported are touched, so this never triggers
    a heavy import during collection.
    """
    tracker = sys.modules.get("myra_app.task_tracker")
    if tracker is not None:
        conn = getattr(tracker, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        tracker._conn = None
        tracker._use_fallback = False
        tracker._fallback_tasks = []

    for mod_name in (
        "myra_app.background_orchestrator",
        "myra_app.tasks.ingest",
        "myra_app.tasks.watchdog",
    ):
        module = sys.modules.get(mod_name)
        pool = getattr(module, "_connection_pool", None) if module else None
        if not isinstance(pool, dict):
            continue
        for lib in list(pool.values()):
            try:
                lib.close()
            except Exception:
                pass
        pool.clear()


def _modules_with_db_dir() -> list[tuple[str, object]]:
    """Every imported module that carries a DB_DIR attribute.

    Deliberately not filtered by package prefix: test modules themselves do
    `from myra_app.constants import DB_DIR` at import/collection time, and so do
    scripts under tools/. Only the attribute matters.
    """
    found = []
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        value = getattr(module, "DB_DIR", None)
        if isinstance(value, str):
            found.append((name, value))
    return found


def _redirect_db_dir(dest: str) -> list[str]:
    """Point every DB_DIR binding at `dest`. Returns modules that were rebound."""
    original = constants.DB_DIR
    constants.DB_DIR = dest

    # Modules already imported captured the production value at import time.
    rebound: list[str] = []
    for name, value in _modules_with_db_dir():
        if value == original:
            sys.modules[name].DB_DIR = dest
            rebound.append(name)
    _reset_cached_connections()
    return rebound


def _stale_db_dir_modules(allowed: str) -> list[tuple[str, str]]:
    """Any imported module still holding a DB_DIR outside the test directory."""
    return [
        (name, value)
        for name, value in _modules_with_db_dir()
        if not _is_inside(value, allowed)
    ]


def _assert_isolated(allowed: str, context: str) -> None:
    """Fail closed if any DB_DIR binding escapes the permitted directory."""
    stale = _stale_db_dir_modules(allowed)
    if stale:
        detail = "\n".join(f"    {name} -> {value}" for name, value in stale)
        pytest.fail(
            f"TEST DB ISOLATION BREACH ({context}): these modules still resolve "
            f"DB_DIR outside the test directory {allowed}:\n{detail}"
        )


class _LiveDbAccessError(RuntimeError):
    """Raised when test code tries to open a live MYRA database."""


def _candidate_paths(database) -> list[str]:
    """Every plausible filesystem path a sqlite3.connect() target could mean.

    Rather than trusting one interpretation of the target string (the failure
    mode that let `file:///C:/...` slip past a naive prefix check), return all
    of them and require every candidate to be outside the live directory.

    The `file:` URI is parsed with urlsplit so the leading `/` of an absolute
    POSIX path survives. An earlier version did `lstrip("/")`, which turned
    `/home/runner/.../myra_metadata.db` into a *relative* path that matched no
    live-directory prefix — silently disabling the guard on Linux CI while it
    still passed on Windows, where the path is `C:/...` and has no leading `/`.
    """
    target = getattr(database, "path", database)
    if isinstance(target, (bytes, bytearray)):
        target = target.decode("utf-8", "replace")
    if isinstance(target, os.PathLike):
        target = os.fspath(target)
    if not isinstance(target, str) or not target:
        return []

    candidates = [target]
    # `file:////abs/path` yields a doubled leading slash; collapse it so the
    # containment check sees the same string os.path.abspath produces.
    collapsed = re.sub(r"^/{2,}", "/", target)
    if collapsed != target:
        candidates.append(collapsed)

    if target.startswith("file:"):
        parsed = urlparse(target)
        uri_path = unquote(parsed.path)
        # Keep the authority for UNC-style URIs (file://server/share).
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            uri_path = f"//{parsed.netloc}{uri_path}"
        candidates.append(uri_path)
        candidates.append(re.sub(r"^/{2,}", "/", uri_path))
        # Naive variant retained for belt-and-braces coverage.
        naive = target[len("file:") :].split("?", 1)[0].lstrip("/")
        candidates.append(naive)
        if os.sep == "\\":
            candidates.append(uri_path.replace("/", "\\"))
            candidates.append(naive.replace("/", "\\"))
    return [c for c in candidates if c]


def _install_live_db_guard() -> None:
    """Wrap sqlite3.connect so opening a live DB during isolation fails closed.

    Checks every candidate interpretation of the target so that URI forms
    (`file:...`, `file:///...?...`) cannot be used to bypass the guard.
    """
    real_connect = REAL_CONNECT

    def guarded_connect(database, *args, **kwargs):
        for candidate in _candidate_paths(database):
            if _is_inside(candidate, LIVE_DB_DIR):
                raise _LiveDbAccessError(
                    f"Blocked test access to live MYRA database {candidate!r} "
                    f"(from target {database!r}). Tests must operate on the "
                    "temporary database directory."
                )
        return real_connect(database, *args, **kwargs)

    sqlite3.connect = guarded_connect


#: Fixture DB keys that must expose a specific table. An "exists but empty"
#: fixture DB would let an isolated test read nothing and still pass — the
#: exact shape of failure that let the original isolation defect hide.
_REQUIRED_FIXTURE_TABLES = {
    "meta": "sync_log",
    "technical": "technical_data",
    "valuation": "fundamentals",
}


def _validate_fixture_dbs(dest: str) -> None:
    """Fail closed if a copied fixture DB is empty or missing a key table.

    ``os.path.exists`` is not enough: a zero-table database file would make
    every isolated test read nothing and could pass vacuously.
    """
    for db_key, filename in LibrarianCore.DB_MAP.items():
        path = os.path.join(dest, filename)
        try:
            conn = REAL_CONNECT(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            pytest.fail(f"fixture DB {filename} is unreadable: {exc}")
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        if not tables:
            pytest.fail(
                f"fixture DB {filename} has no tables; run "
                "tests/fixtures/build_fixture_db.py (an empty DB would let "
                "isolated tests pass vacuously)"
            )
        expected = _REQUIRED_FIXTURE_TABLES.get(db_key)
        if expected and expected not in tables:
            pytest.fail(
                f"fixture DB {filename} is missing required table {expected!r} "
                f"(tables: {sorted(tables)})"
            )


@pytest.fixture(scope="session")
def _pristine_fixture_dbs(tmp_path_factory):
    """One pristine copy of the fixture DBs, shared for the whole session.

    Deliberately a *session-scoped directory* rather than a session-scoped
    redirect: several callers key cached resources by thread, not by path (the
    thread-local ``LibrarianCore`` pools in ``background_orchestrator`` /
    ``tasks.ingest`` / ``tasks.watchdog``), so a directory that changed on every
    test could leave those caches pointing at an earlier temporary directory.
    The directory identity is stable; only the DB_DIR *redirect* is scoped.
    """
    fixtures_src = os.path.join(constants.PROJECT_ROOT, "tests", "fixtures")
    dest = str(tmp_path_factory.mktemp("myra_fixture_dbs"))

    for db_key, filename in LibrarianCore.DB_MAP.items():
        src = os.path.join(fixtures_src, f"{db_key}_fixture.db")
        if not os.path.exists(src):
            pytest.skip(
                f"missing fixture DB (run tests/fixtures/build_fixture_db.py): {src}"
            )
        shutil.copy2(src, os.path.join(dest, filename))

    _validate_fixture_dbs(dest)
    return dest


@pytest.fixture
def fixture_db_dir(_pristine_fixture_dbs):
    """Redirect **every** DB_DIR binding to the fixtures for ONE test only.

    Opt-in (non-autouse): tests that request it run against synthetic fixtures;
    everything else keeps the production DB_DIR.

    Function-scoped on purpose. When this fixture was session-scoped it
    performed the redirect once and only undid it at session teardown, so
    *every* test that ran afterwards — whether or not it asked for isolation —
    resolved ``DB_DIR`` to the synthetic directory. Tests that resolve database
    paths at runtime (the bulk loader, the ``/api/chart`` endpoint) then read
    the fixture DB instead of the live one. Alphabetical collection order hid
    it because ``test_db_isolation`` sorts after those modules. Scoping the
    redirect to the requesting test means unrelated tests always see the real
    ``DB_DIR`` again, and the live-access guard is (re)installed for each
    isolated test instead of being left in whatever state a previous test left
    it (``test_uri_bypass_cannot_escape_the_guard`` restores the real
    ``sqlite3.connect`` when it finishes).

    The redirect covers three escape routes:
      * modules that bind ``from myra_app.constants import DB_DIR`` at import time
      * cached connection singletons / thread-local pools (``task_tracker._conn``,
        the orchestrator ``_connection_pool``s)
      * direct ``sqlite3.connect`` calls to a live path, which now raise
    """
    dest = _pristine_fixture_dbs
    original_db_dir = constants.DB_DIR

    _redirect_db_dir(dest)
    _assert_isolated(dest, "fixture setup")

    real_connect = REAL_CONNECT
    _install_live_db_guard()
    try:
        yield dest
    finally:
        # Teardown runs with the guard still active so cleanup code cannot
        # quietly operate on a live database either.
        sqlite3.connect = real_connect
        constants.DB_DIR = original_db_dir
        _reset_cached_connections()
        for name, value in _modules_with_db_dir():
            if value == dest:
                sys.modules[name].DB_DIR = original_db_dir
