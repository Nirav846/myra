# Phase 1 — Test, Pytest, CI/CD & Verification Audit

Scope: test infrastructure and the delivery pipeline only. No application
rewrite; application code was touched only to keep the suite green and the
verification trustworthy.

## 1. Environment & baseline (measured, before changes)

| Item | Value |
|------|-------|
| Repo / branch | `Nirav846/myra` @ `main` (start commit `11b329a`) |
| OS / Python | Windows, CPython 3.12.10, venv `pkscreener_env` |
| Test framework | **pytest only** (no unittest/nose/tox) |
| pytest | 9.0.3 (venv) |
| Formatter / linter | black 23.3.0, flake8 6.0.0 (pre-commit hooks) |

Baseline with a plain `pytest` (the authentic command — *not* `-p guardprobe`):

* Collection: **1110 tests** in **14.3 s** (`--collect-only -q`).
* Full run: **1103 passed, 8 errors** in ~**242 s**:
  * 7 errors — `tests/test_institutional_sync.py` setup →
    `ModuleNotFoundError: No module named 'mcp'`.
  * 1 intermittent error — session `live_db_guard` teardown failure
    ("LIVE DATABASE MODIFIED").
* Slowest tests (call time): ~40 % of the run is `tests/test_bulk_loader.py`
  (`test_wyckoff_parity` 42.5 s, `test_accumulation_base_parity` 25.9 s,
  `TestRowsForSymbol` 18.4 s setup) against the live 2 GB `technical.db`.

## 2. Test framework & runner inventory

* `pytest.ini` — `testpaths = tests` only (pins collection away from the
  gitignored `eod2*` archives and stray `myra_web/test_*.py`).
* `tests/conftest.py` (324 lines) — the DB-safety layer:
  * `fixture_db_dir` (session, opt-in) copies `tests/fixtures/*.db` to a tmp dir
    and rebinds **every** imported `DB_DIR` binding, resets cached connections,
    and arms a `sqlite3.connect` guard that fails closed on any live path.
  * `live_db_guard` (session, autouse) fails the run if a live `.db` file changes.
* `tests/fixtures/` — 10 synthetic databases + `build_fixture_db.py`.
* 72 test files in `tests/` (unit, API/route, integration, a few parity/perf).
* Stray non-suite tests at repo root (`test_*.py`) and in `myra_web/` are
  **untracked/gitignored** and never collected — not part of the suite.

## 3. CI/CD inventory

`.github/workflows/ci.yml` (before): jobs `test` (pytest), `frontend-build`
(pnpm build), `audit` (schedule-only: pip-audit + pnpm audit);
`permissions: contents: read`.

Gaps found: no pip cache; no run concurrency; no job timeouts; **no
lint/format gate**; no coverage; an unpinned `pip install pytest`; and a second
`pytest --collect-only` step that printed but asserted nothing (~15 s wasted).

## 4. Verified findings

1. **Env inconsistency (fixed).** The local venv was missing the declared
   `finstack-mcp` (which supplies `mcp`); `myra_app/utils/institutional_sync.py`
   and `myra_app/utils/finstack_bridge.py` import `mcp` at module top, so 7 tests
   errored locally. CI installs it, so local ≠ CI. Fix: installed
   `finstack-mcp==0.10.0` → those tests pass; suite is now **1110 passed, 0 errors**.
2. **Intermittent, unactionable DB guard.** `live_db_guard` fired once during a
   baseline run with a message that gave only fingerprints. It is a session-wide
   observation, so an external writer (a locally-running MYRA scheduler) or a WAL
   checkpoint that moves a `.db` mtime during the ~4 min window trips it. Could
   **not** reproduce a test-caused write. Fix: the failure now **names the changed
   file(s)** and tells the operator to stop a local MYRA.
3. **No CI formatting/lint gate** while commits are often pushed with
   `--no-verify` (bypassing pre-commit). The repo is not clean enough for a
   repo-wide gate (36 black files, 90 flake8 hits), so the gate is scoped to
   `tests/` (clean after reformatting 7 files).
4. **Redundant CI work** (double collection) removed.
5. `slow` marker was used but unregistered (`PytestUnknownMarkWarning`).
6. Parallelism is **not** safe (see §7).

## 5. Decisions & rationale

* Did **not** reformat the 36 legacy (non-test) files — pre-commit fixes them
  per-file; a repo-wide reformat is churn outside this phase.
* Did **not** add a coverage threshold — the honest baseline is 41 %; any
  threshold would be either meaningless or immediately brittle. Coverage is
  reported for visibility only.
* Did **not** adopt `pytest-xdist` — it broke isolation (see §7).
* Kept `fixture_db_dir` opt-in; it is security-sensitive and widely relied on.

## 6. Changes

| File | Change |
|------|--------|
| `pytest.ini` | register `slow`; `addopts = -ra --strict-markers` |
| `tests/conftest.py` | guard reports the changed DB file(s) + actionable hint |
| `tests/test_db_isolation.py` | +`test_guard_message_names_the_changed_database`; black-format |
| `requirements-dev.txt` (new) | pytest, pytest-cov, black==23.3.0, flake8==6.0.0 |
| `.github/workflows/ci.yml` | pip cache, concurrency, timeouts, `lint` job, coverage, drop redundant collect; frontend `pnpm lint` type-check |
| `CONTRIBUTING.md` | documented fast/full test paths |
| 6 test files | black-reformatted so the CI format gate passes |

Tests removed: none. Tests added: 1 (guard diagnostics). Tests modified: 7
(formatting only).

## 7. Parallel execution — evaluated and rejected

`pytest -n 4 --dist loadfile`: **13 failed, 13 errors** (vs 0 serial). Failures are
`fixture_db_dir` setup `_assert_isolated` breaches and route tests returning 404 —
the conftest isolation model relies on **process-global** rebinding of `DB_DIR` and
`sqlite3.connect`, which does not compose with multiple workers (and the fixture is
session-scoped per worker). Parallel is documented as unadopted until the isolation
layer is worker-aware.

## 8. Before / after (measured)

| Scenario | Before | After |
|----------|--------|-------|
| Collection | 1110 in 14.3 s | 1110 in ~14 s |
| Full `pytest` | 1103 passed, **8 errors**, ~242 s | **1110 passed, 0 errors**, ~198 s |
| Fast `pytest -m "not slow"` | n/a | **1106 passed, 4 deselected**, ~134 s |
| Full + coverage | n/a | 1110 passed, ~306 s, **41 %** (20330 stmts) |

Coverage: `--cov=myra_app` → **41 %** total (informational; not gated). Thin areas
tracked for Phase 3+ rather than papered over.

## 9. Commands

```bash
# one-time
python tests/fixtures/build_fixture_db.py

# FAST (dev loop) — NOT full verification
pytest -m "not slow"

# FULL verification
pytest

# FULL + coverage (what CI runs)
pytest tests/ -rs --durations=15 --cov=myra_app --cov-report=term-missing:skip-covered

# lint gate (scoped to tests/)
black --check tests/ && flake8 tests/
```

## 10. Remaining risks / not done

* **CI lint gate is scoped to `tests/`.** Application code remains subject to
  pre-commit only, which `--no-verify` bypasses. A repo-wide gate needs a one-time
  bulk `black` (36 files) + flake8 cleanup — deferred.
* **Intermittent `live_db_guard` false alarm** when a local MYRA process writes
  during a run. Now actionable (names the file) but not eliminated.
* **No coverage threshold** and 41 % coverage.
* **Requires force-add:** `requirements-dev.txt` and this doc are matched by the
  local `.git/info/exclude` (`*.txt`, `*.md`); they were committed with
  `git add -f`. `.gitignore`/`exclude` were **not** modified.
* Branch protection, repo secrets and provider-side settings were not inspected
  (not available locally) — CI permissions stay least-privilege (`contents: read`).

## 11. Recommended next steps

1. Make the DB isolation layer worker-aware, then enable `pytest -n auto` for a
   large, safe speedup.
2. Bulk-format the application tree once (`black`), clear the 90 flake8 hits, and
   widen the CI lint gate to the whole repo.
3. Raise coverage on the highest-risk modules (scanning/scoring, ingestion
   freshness, enrichment fallback) with the fixture DBs already in place.
