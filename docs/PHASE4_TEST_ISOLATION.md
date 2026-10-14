# Phase 4 - Order-independent DB test isolation

Starting point: `7eae58d` (Phase 3 on `origin/main`, full suite 1172 passed).
Scope: make the test suite independent of module collection order. No
application scoring/screening behaviour, no production data, and no live-DB
guard weakening.

## Symptom

Running an isolated test module *before* the modules that read the live database
starved the latter:

```
pytest tests/test_db_isolation.py tests/test_bulk_loader.py tests/test_chart_endpoint.py
# before: 13 failed, 29 passed
```

`test_bulk_loader.py` saw an empty bulk frame (`{}` -> `StopIteration`) and five
`test_chart_endpoint.py` shape/limit/order assertions failed. The default
(alphabetical) order passed because `test_bulk_loader` (`b`) and
`test_chart_endpoint` (`c`) are collected **before** `test_db_isolation` (`d`).

## Root cause

`fixture_db_dir` in `tests/conftest.py` was **session-scoped**.

- `_redirect_db_dir(dest)` sets `constants.DB_DIR = dest` and rebinds every
  already-imported module that did `from myra_app.constants import DB_DIR` at
  import time (~99 modules).
- Its teardown restored those bindings only at **session end**.

So from the first `fixture_db_dir` request to the end of the session, `DB_DIR`
resolved to the synthetic fixture directory for *every* test, whether or not it
asked for isolation. Modules that resolve DB paths at runtime -
`myra_app.db.bulk_loader._tech_db_path()`, the `/api/chart` endpoint - then read
fixture data. Module-level `TECH_DB` snapshots taken at collection time still
pointed at the live path (collection imports everything first), which is why the
failure looked like "empty data" rather than a wrong path.

## Fix

Split the session-scoped fixture into two:

| Fixture | Scope | Responsibility |
|---------|-------|----------------|
| `_pristine_fixture_dbs` | session | Copy the fixture DBs once, validate them, hold the stable directory |
| `fixture_db_dir` | **function** | Redirect + install the live-DB guard for one test; restore everything in teardown |

The redirect target stays a single session-stable directory on purpose:
`background_orchestrator`, `tasks.ingest` and `tasks.watchdog` cache
`LibrarianCore` instances in thread-keyed `_connection_pool`s (not path-keyed),
so a directory that changed per test could leave those pools pointing at an
earlier temp directory. Only the *redirect* is scoped per test.

Teardown restores, in order: `sqlite3.connect` (the live-access guard),
`constants.DB_DIR`, cached connections/pools, and every module binding that
still points at the fixture directory.

### Options considered

- **A - fresh temp directory per test.** Rejected: the thread-keyed connection
  pools would strand `LibrarianCore` instances on a previous directory.
- **B - session-stable directory, function-scoped redirect (chosen).** Smallest
  change that removes the leak without touching pool keying.
- **C - autouse reset of `DB_DIR` only.** Rejected: would still leave the
  live-access guard and cached connections in whatever state the previous test
  left them.

## Defects found while fixing

1. **Guard left uninstalled for the session.**
   `test_uri_bypass_cannot_escape_the_guard` installed the guard, then restored a
   bare `sqlite3.connect` in its `finally` and never re-armed it. With a
   session-scoped fixture that disabled live-DB protection for the rest of the
   session. It now re-arms the guard.

2. **Pre-existing `DB_DIR` leak in `tests/test_market_calendar_guard.py`.**
   The `cal` fixture redirected `DB_DIR` for modules that already existed, but
   several tests do `from myra_app import ingest_bhavcopy` *inside* the test
   body. The late import snapshotted the temp `DB_DIR` and monkeypatch could not
   restore a module it never patched, so `myra_app.ingest_bhavcopy.DB_DIR` stayed
   on a deleted temp directory. This was invisible while `_assert_isolated` ran
   once per session; the function-scoped fixture re-checks it every isolated
   test and surfaced it. Fixed with a teardown finalizer that releases any
   binding left pointing at the temp directory.

3. **Fixture integrity.** `_validate_fixture_dbs()` now fails the run if a
   fixture DB has no tables or is missing a key table (`sync_log`,
   `technical_data`, `fundamentals`), so an "exists but empty" fixture cannot
   make isolated tests pass vacuously.

## Regression coverage

`tests/test_db_isolation_order.py` (7 tests):

- every fixture DB is non-empty and carries its key tables;
- while isolated: `DB_DIR` is redirected and the live DB is blocked;
- the fixture directory identity is stable across isolated tests;
- after an isolated test, `DB_DIR`, all import-time bindings, and
  `sqlite3.connect` are restored (the core order regression);
- the bulk loader's runtime path resolves to the live directory again;
- a guard removed by one test is re-armed for the next isolated test.

Verified genuine: with `fixture_db_dir` temporarily reverted to session scope,
3 of these fail (`DB_DIR` leaked, bulk-loader path under the fixture dir, guard
not re-armed).

## Verification

| Check | Before | After |
|-------|--------|-------|
| `test_db_isolation` + 8 starved modules | 13 failed, 119 passed | **132 passed** |
| `test_db_isolation` + bulk + chart | 13 failed, 29 passed | **42 passed** |
| Control order (bulk + chart + db_isolation) | 42 passed | 42 passed |
| Order A (regression module first) | - | 30 passed |
| Order B (chart + db_isolation + bulk) | - | 42 passed |
| Full suite | 1172 passed | **1179 passed** / 0 errors (255 s) |

`black` 26.10.1 and `flake8` clean on `tests/`.

## Remaining limitations

- `test_bulk_loader.py`'s live-parity tests still read the real technical DB
  read-only; they skip when it is absent (CI without fixtures skips them).
- The suite remains single-process. Enabling `pytest-xdist` is still gated on
  proving worker-level safety for the thread-keyed connection pools and the
  module-global `sqlite3.connect` guard; not attempted here.
