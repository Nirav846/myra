# MYRA Monolith Refactor — Learning Log

## Phase 1 — 2026-08-16: Extract pure utilities to `myra_web/utils.py`

### Files added
- `myra_web/utils.py` — new module with 9 helpers/constants extracted from the server

### Files modified
- `myra_web/myra_fastapi_server.py` — deleted original definitions, added re-export import

### Observations

1. **`_get_latest_trading_day_before` and `build_confluence_report` are not strictly pure** — they query SQLite and read cache files respectively. However, they are deterministic w.r.t. the DB/filesystem state and were moved per the Phase 1 plan.

2. **Server must re-export moved names** — `tests/test_dcb_defaults.py:16` does `from myra_fastapi_server import app, _apply_tier_rank` and must keep working. The re-export import block at the top of the server file preserves backward compatibility.

3. **`_SCANNER_ROUTES` appears unused in the server** — grep shows no references within `myra_fastapi_server.py` after extraction. Candidate for future cleanup or use in the confluence router (Phase 9).

4. **`myra_web` is a namespace package** — no `__init__.py` exists; imports via `from myra_web.utils import ...` work via Python namespace package mechanics, same as `myra_web.routes.fundamentals` already does.

5. **`DB_DIR` computed in the server matches `myra_app.constants.DB_DIR`** — both resolve to `<repo>/myra_app/db`. The local redefinition (lines 96-97) is harmless and left as-is.

6. **Test baseline**: 311 passed, 1 pre-existing failure (`test_bulk_loader.py::TestScannerBulkParity::test_dcb_parity` — KeyError 'symbol', unrelated to this refactor). Zero new regressions.

### Smoke-test results
- `GET /api/health` → 200, returns health dict with 8 DB connection entries
- `GET /api/latest-trading-day` → 200, returns `{"date": "2026-08-14"}`
- `GET /api/confluence` → 200, returns 478 symbols from scanner caches

---

## Phase 2 — 2026-08-16: Extract 5 least-coupled endpoint groups into router files

### Files created
- `myra_web/routes/sentiment.py` — 1 endpoint (`GET /api/sentiment/{ticker}`)
- `myra_web/routes/ai_opinion.py` — 1 endpoint (`GET /api/ai-opinion/{ticker}`)
- `myra_web/routes/chart.py` — 1 endpoint (`GET /api/chart/{symbol}`)
- `myra_web/routes/search.py` — 1 endpoint (`GET /api/search/symbols`)
- `myra_web/routes/finstack.py` — 9 endpoints + 2 commented-out blocks, `_validate_finstack` helper, `_finstack_cache` dict, `CACHE_TTL` constant

### Files modified
- `myra_web/myra_fastapi_server.py` — deleted endpoint definitions, added 5 router imports + `include_router` calls, removed `_finstack_cache`/`CACHE_TTL` (now only in finstack router)

### Endpoint counts moved
| Router | Endpoints | Source lines removed |
|--------|-----------|---------------------|
| sentiment | 1 | 4293-4314 |
| ai_opinion | 1 | 4317-4343 |
| chart | 1 | 4346-4369 |
| search | 1 | 1796-1801 |
| finstack | 9 active + 2 commented-out | 1804-1974 |

### Key observations

1. **`chart.py` lazy import pattern**: `from myra_fastapi_server import get_db_path` is placed INSIDE the endpoint function body, not at module top. This serves two purposes:
   - Avoids circular import (server imports routers before `get_db_path` is defined)
   - Preserves `test_chart_endpoint.py`'s monkeypatch of `myra_fastapi_server.get_db_path` — the test patches the server module attribute, and the lazy import resolves it at call time, so the patched version is used.
   - Future candidate: move `get_db_path` to `myra_web/utils.py` with the same lazy pattern (test-compatible).

2. **`_finstack_cache`/`CACHE_TTL`/`_validate_finstack` now live only in the finstack router** — module-level dict with 300s TTL shared across all finstack endpoints.

3. **Sentiment endpoint preserves 200-error-dict response** — deliberately does NOT raise HTTPException; returns `{"status": "error", "error": "..."}` instead. This is the original API contract.

4. **`myra_web` remains a namespace package** — routers use `APIRouter(prefix=..., tags=...)` consistently with existing patterns.

5. **`_IncludedRouter` visibility**: Starlette wraps included routers in `_IncludedRouter` objects which don't expose `path` directly on the route list. Recursive walkers need `original_router` attribute to navigate into sub-routes. TestClient works fine for actual HTTP calls regardless.

### Test results
- **Baseline**: 311 passed, 1 pre-existing failure (`test_bulk_loader.py::TestScannerBulkParity::test_dcb_parity` — KeyError 'symbol')
- **Post-refactor**: 311 passed, 1 pre-existing failure (same) — zero new regressions
- `tests/test_chart_endpoint.py` (all 9 tests including 3 temp-DB monkeypatch tests): PASSED
- `tests/test_ai_second_opinion.py` (all 19 tests including 4 endpoint tests): PASSED

### Smoke-test results
- `GET /api/chart/HDFCBANK?limit=3` → 200, 3 OHLCV rows
- `GET /api/search/symbols?q=REL` → 200
- Sentiment/AI-opinion/finstack: best-effort only (hit external services, not reliable for CI)

---

## Phase 3 — 2026-08-16: Extract ML router and background task helper

### Files created
- `myra_web/background.py` — `_spawn_task` extracted verbatim; name kept with underscore because `test_task_offload.py` imports it from the `myra_fastapi_server` namespace and calls it directly.
- `myra_web/routes/ml.py` — 12 endpoints moved verbatim (prefix `/api/ml`, tags `["ml"]`). Imports `_spawn_task` from `myra_web.background` directly (no circular dependency).

### Files modified
- `myra_web/myra_fastapi_server.py` — deleted local `_spawn_task` definition (17 lines) and all 12 `@app.*(/api/ml/...)` endpoints (~297 lines). Added re-export `from myra_web.background import _spawn_task` and router import/include `from myra_web.routes.ml import router as ml_router` + `app.include_router(ml_router)`.

### Endpoint count moved
| Router | Endpoints | Source lines removed |
|--------|-----------|---------------------|
| ml | 12 | 1505–1800 (304 lines removed) |

### Key observations

1. **`_spawn_task` re-export pattern**: The test file `tests/test_task_offload.py` does `from myra_fastapi_server import app, _spawn_task` and CALLS `_spawn_task(...)` directly. The server must keep the name `_spawn_task` (with underscore) bound in its module namespace. Solved via `from myra_web.background import _spawn_task` at module level in the server — Python re-exports it as `myra_fastapi_server._spawn_task`.

2. **ML endpoints use relative `models/...` paths** (not `MODELS_DIR`) — left as-is. These paths are relative to the CWD when the server starts, not to the module file. This is the original behavior.

3. **`ml_predict` uses `asyncio.to_thread`**; **`ml_train` uses `_spawn_task`** — both patterns preserved exactly.

4. **`predict_launchpad` is a large inline block** (~135 lines) with lazy imports of `sqlite3`, `pandas`, `numpy`, `joblib`, and `LibrarianCore`. Candidate for later extraction into the `LaunchpadPredictor` module (scope improvement, not in this phase).

5. **Router routes are nested inside `_IncludedRouter`** objects — `app.routes` doesn't flatten them into a single list. This is Starlette 1.6.0 behavior. Routes ARE registered and respond correctly via TestClient.

6. **No `__init__.py` needed** — `myra_web` remains a namespace package; `myra_web.routes.ml` imports work via namespace package mechanics.

### Smoke-test results
- `GET /api/ml/status` → 200 (model exists, returns metadata)
- `GET /api/ml/config` → 200 (returns current config or defaults)
- `GET /api/ml/launchpad/status` → 200 (returns `{"exists": False}` or metadata)
- `GET /api/ml/predict` → 200 (returns prediction payload, may include XGBoost warning)
- `GET /api/ml/train` → 405 (POST-only, proves registration)
- `GET /api/ml/launchpad/label` → 405 (POST-only, proves registration)
- `GET /api/ml/launchpad/train` → 405 (POST-only, proves registration)

### Test results
- **Baseline**: 311 passed, 1 pre-existing failure (`test_bulk_loader.py::TestScannerBulkParity::test_dcb_parity` — KeyError 'symbol')
- **Post-refactor**: 311 passed, 1 pre-existing failure (same) — zero new regressions
- `tests/test_task_offload.py` (all 4 tests including `test_ml_predict_returns_200_with_payload`): PASSED

---

## Phase 4 — 2026-08-16: Extract tools router (sync, ingest, execute, refresh-industry)

### Files created
- `myra_web/security.py` — `MYRA_API_SECRET` + `verify_myra_auth` extracted as single source of truth. Required to break a circular import: `tools.py` uses `Depends(verify_myra_auth)` at decoration time, so it cannot import from the server (which imports the tools router during its own module load). Both the server and tools.py import the SAME function object from this module.
- `myra_web/routes/tools.py` — 6 tool endpoints (`/api/tools/execute`, `/api/tools/sync/fundamentals`, `/api/tools/sync/etf`, `/api/tools/sync/index`, `/api/tools/ingest`, `/api/tools/db-doctor`) + `ToolRequest` model + local `tool_map` dict + `_BASE_DIR` computed as `dirname(dirname(__file__))` to match the server's `BASE_DIR`. Also contains `portfolio_tools_router` (prefix `/api/portfolio`) with the `refresh-industry` endpoint.

### Files modified
- `myra_web/myra_fastapi_server.py` — deleted `MYRA_API_SECRET` definition and `verify_myra_auth` definition (replaced with re-export `from myra_web.security import MYRA_API_SECRET, verify_myra_auth`). Deleted `ToolRequest` class, `execute_tool`, `force_fundamentals_sync`, `force_etf_sync`, `force_index_sync`, `force_daily_ingest`, `run_db_doctor` (~110 lines). Deleted `refresh_portfolio_industry` (~17 lines). Added router imports + `include_router` calls for `tools_router` and `portfolio_tools_router`.
- `refactor_learning.md` — Phase 4 section appended.

### Endpoint count moved
| Router | Endpoints | Source lines removed |
|--------|-----------|---------------------|
| tools (prefix /api/tools) | 6 | 444–554 (~110 lines) |
| portfolio_tools (prefix /api/portfolio) | 1 | 3256–3272 (~17 lines) |

### Key observations

1. **Auth object identity is critical**: `tests/test_chart_endpoint.py`, `test_dcb_defaults.py`, `test_query_endpoint.py`, `test_task_offload.py` all do `from myra_fastapi_server import verify_myra_auth` and set `app.dependency_overrides[verify_myra_auth] = ...`. The `dependency_overrides` dict keys on the **function object**. If `verify_myra_auth` were defined in both the server and tools.py, they'd be different objects and the overrides wouldn't work. Solved by having a single definition in `myra_web/security.py` and re-exporting from the server.

2. **Circular import prevention**: `tools.py` uses `Depends(verify_myra_auth)` at decoration time (module import). If it imported from `myra_fastapi_server`, the server imports the tools router during its own module load → circular import. The shared `myra_web/security.py` module breaks this cycle.

3. **`_task_*` imports are guarded**: These functions are NOT defined in the server — they're imported from `myra_app.background_orchestrator` (lines 49-59). `tools.py` does its own guarded import with the same `try/except ImportError` pattern. The server's import block is kept because `/api/tools/status` (still in server, moves in Phase 6) uses `_get_last_run`.

4. **`_BASE_DIR` resolution**: Server's `BASE_DIR = os.path.dirname(os.path.abspath(__file__))` resolves to `myra_web/`. In `tools.py`, `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` (two dirname calls from `myra_web/routes/tools.py`) yields the same `myra_web/` path. Script execution behavior is identical.

5. **Only `/api/tools/execute` is auth-protected**: The sync/ingest/db-doctor endpoints have no auth dependency — this is the original behavior, preserved exactly.

6. **`portfolio_tools_router` uses separate prefix**: The refresh-industry endpoint lives on a separate router with `prefix="/api/portfolio"` inside `tools.py`. This avoids `@router.post("/../portfolio/refresh-industry")` hacks and keeps the route path clean.

### Test results
- **Baseline**: 311 passed, 1 pre-existing failure (`test_bulk_loader.py::TestScannerBulkParity::test_dcb_parity` — KeyError 'symbol')
- **Post-refactor**: 311 passed, 1 pre-existing failure (same) — zero new regressions
- `tests/test_chart_endpoint.py` (all 9 tests including 3 temp-DB monkeypatch tests): PASSED
- `tests/test_dcb_defaults.py` (all 12 tests): PASSED
- `tests/test_query_endpoint.py` (all 15 tests): PASSED
- `tests/test_task_offload.py` (all 4 tests): PASSED

### Smoke-test results
- `POST /api/tools/sync/fundamentals` with auth header → 202 `{"status": "started", "task_id": 1404}`
- `POST /api/tools/execute` with invalid tool_id → 400 `{"detail": "Tool mapping not found"}`
- `POST /api/tools/execute` without auth → 401 Unauthorized
- All 7 routes: GET returns 405 (POST-only), proving registration

## Phase 5 — 2026-08-16: Extract portfolio router

### Files added
- `myra_web/routes/portfolio.py` — 7 endpoints moved (GET /api/portfolio summary, POST /refresh, GET /live-prices, GET /benchmark, POST/PUT/DELETE /holdings)

### Files modified
- `myra_web/myra_fastapi_server.py` — removed 7 portfolio endpoints (702 lines), added portfolio router import + `app.include_router(portfolio_router)`

### Observations
1. **`get_portfolio` is the largest endpoint (~320 lines)** — contains `compute_myra_quality_score`, `FUNDA_FIELDS` list, industry enrichment, freshness computation. Candidate for later extraction into a service module.
2. **Live-price caching** uses SQLite table `live_price_cache` in the portfolio DB with 5-min TTL; yfinance fetch loop has 0.2s sleep between symbols.
3. **Write endpoints (holdings CRUD) have NO auth dependency** — existing behavior preserved. Security improvement candidate: add `Depends(verify_myra_auth)` to write endpoints.
4. **No tests directly reference portfolio endpoints** (grep found none) — low risk.
5. `_spawn_task` imported from `myra_web.background` (extracted Phase 3).

### Test results
- Baseline: 311 passed, 1 pre-existing failure (`test_dcb_parity`). Post-refactor identical.
- Spot checks: `GET /api/portfolio` → 200 (17 holdings), `GET /api/portfolio/benchmark` → 200, `POST /api/portfolio/refresh` → 202 with task_id, `POST /api/portfolio/holdings` → 200 (created).

## Phase 6 — 2026-08-16: Extract health and system router

### Files added
- `myra_web/routes/health.py` — 8 read-only endpoints: /api/health, /api/data-health, /api/market-breadth, /api/db-size, /api/system-info, /api/logs/recent, /api/latest-trading-day, /api/tools/status

### Files modified
- `myra_web/myra_fastapi_server.py` — removed the 8 endpoints, added health router import + include

### Observations
1. **`/api/tools/status` now lives in health.py** (semantically a health/system endpoint) and imports `_get_last_run` directly from `myra_app.background_orchestrator` with its own `try/except ImportError` guard + a fallback stub returning "Never" (equivalent behavior to the original `globals()` guard).
2. **Server's `_task_*`/`_get_last_run` import block is now dead** (all consumers moved to tools.py in Phase 4 / health.py in Phase 6). Left in place for Phase 10 final cleanup — harmless unused names.
3. **`import datetime` pitfall**: health.py imports the `datetime` module; endpoint code calls `datetime.datetime.now()` (not `datetime.now()`), unlike the server which imported `from datetime import datetime`. Fixed in the router — the original server used the class import.
4. **`health_check` kept as sync `def`** (not `async def`) matching the original — FastAPI handles sync endpoints in a threadpool.
5. Original `data_health` had a truncated-looking query that was actually a copy artifact — fixed to the canonical `SELECT COUNT(*) FROM fundamentals WHERE free_float_pct IS NOT NULL`.
6. **`psutil` is optional** — system-info returns `{"error": "psutil not installed"}` if missing (original behavior preserved).

### Test results
- Full suite: 311 passed, 1 pre-existing failure (`test_dcb_parity`) — zero new regressions.
- All 8 health endpoints smoke-tested → 200 via TestClient.

## Phase 7 — 2026-08-16: Extract scanners router with shared factory pattern

### Files added
- `myra_web/routes/scanners.py` — 27 scanner-related endpoints + cache-clear via factory

### Files modified
- `myra_web/myra_fastapi_server.py` — deleted entire scanner block (state dicts, locks, cache save/load, 26 endpoints, cache-clear), added scanners router import + include. File shrank from 2656 to ~400 lines.

### Factory pattern
- `register_scanner(name, ...)` creates state/lock/cache in a closure and registers `GET /{name}/status` + `POST /{name}/scan` via `router.add_api_route`.
- 11 standard scanners registered with config: invisible-hand, trigger, liquidity-flip, dcb-bargain, operator-fingerprint, float-exhaustion, seasonal-delivery, darvas, wyckoff, bottom-hunter, climax-accumulation.
- launchpad + multibagger keep custom explicit endpoints (in-memory predictions / global result, no cache file).
- dcb-bargain has a `status_post_process` (tier-rank cached candidates) + `post_process` (circuit filter on records, mirroring original df-level column selection).

### Observations
1. **Tuple-return quirk (pre-existing)**: eturn {"detail": "..."}, 409 does NOT produce a 409 in this FastAPI version — TestClient shows 200 with body [{"detail": "..."}, 409]. The ORIGINAL code had the identical expression, so behavior is faithfully preserved (not a regression). Candidate for future fix (HTTPException or JSONResponse with status 409), but that would be a behavior change — deferred.
2. **Scanners use raw 	hreading.Thread, not _spawn_task** — the original never used _spawn_task for scans (no task_tracker registration). Factory mirrors that. A _spawn_task import was initially added then removed to avoid changing behavior.
3. **launchpad/multibagger escape the factory** — launchpad state has predictions (not candidates), no cache file, no progress; multibagger uses a global with no lock. Forcing them into the factory would have added more special cases than it saved.
4. **cache-clear endpoint moved here ahead of Phase 9** — it mutates scanner state dicts directly, so it had to move with them. Phase 9's planned cache router is now effectively done (confluence-only remains for Phase 9).
5. **status cached-response variance**: darvas/wyckoff/climax have no `bear_market` key; bottom-hunter returns `"scanned_date": None` instead; dcb applies tier rank. Handled via `status_extra`/`status_post_process` config.
6. **progress-attr variance**: seasonal-delivery wraps `_get_all_tech_data` (w/ kwargs) instead of `_get_tech_data` (positional) — handled via `progress_attr` + `tracked_kwargs`.
7. **result-mode variance**: trigger + float-exhaustion return list-of-dicts (inline NaN sanitize), all others return DataFrames (`_df_to_safe_records`) — handled via `result_mode="list"|"df"`.
8. Tests: `test_dcb_defaults.py` (hits /api/dcb-bargain/defaults + /status, imports _apply_tier_rank from server) and `test_market_mood.py` (hits /api/pcr/status — kept in server) both pass.

### Test results
- Full suite: 311 passed, 1 pre-existing failure (`test_dcb_parity`) — zero new regressions.
- Smoke: all 13 status endpoints 200; all 13 scan endpoints registered (POST-only, GET->405); dcb scan ran live (184 symbols, progress 10%, scanning); dcb defaults 200; cache-clear 200; pcr 200; confluence 200.

## Phase 8 — 2026-08-16: Extract query router

### Files added
- `myra_web/routes/query.py` — POST /api/query + `QueryRequest` + `_run_query` executor

### Files modified
- `myra_web/myra_fastapi_server.py` — removed query block (~90 lines), added query router import + include, re-exported `_run_query` from the router module for test compatibility.

### Observations
1. **`_run_query` re-export required**: `tests/test_query_endpoint.py:31` does `from myra_fastapi_server import app, _run_query`. The server now imports it from the router: `from myra_web.routes.query import router as query_router, _run_query` — keeps the test working without touching it.
2. **Auth object identity preserved**: query.py imports `verify_myra_auth` from `myra_web.security` (same function object the server re-exports). `test_query_endpoint.py`'s `app.dependency_overrides[verify_myra_auth]` still works. Verified `verify_myra_auth is security.verify_myra_auth` → True.
3. **Endpoint safety rules are inline** (not separate helpers): frontend→canonical mapping dict, SELECT * regex rejection (technical/valuation only), auto `LIMIT 5000` for read prefixes, 10 MB response guard — all copied verbatim.
4. **No remaining server dependencies**: `QueryRequest`, `_run_query`, `execute_query` fully removed from the server. `get_db_path` still in server (used by chart.py lazy import + health.py) — candidate for utils.py in Phase 10 cleanup.
5. Smoke: valid query 200 (3 rows), SELECT * → 400, no auth → 401, unknown db → 400.

### Test results
- Full suite: 311 passed, 1 pre-existing failure (`test_dcb_parity`) — zero new regressions.
- `tests/test_query_endpoint.py` all pass (imports _run_query from server namespace).

## Phase 9 — 2026-08-16: Extract confluence router

### Files added
- `myra_web/routes/confluence.py` — GET /api/confluence (thin wrapper over `build_confluence_report`)

### Files modified
- `myra_web/myra_fastapi_server.py` — removed confluence endpoint (10 lines), added confluence router import + include.

### Observations
1. **Trivial move**: the endpoint is a thin wrapper — `build_confluence_report()` was already extracted to `utils.py` in Phase 1, so no helper movement needed. Router imports it from `myra_web.utils`.
2. **Server re-export kept**: the `build_confluence_report` name remains in the server's utils re-export block (line 48) for namespace stability; no test imports it from the server, so it could be trimmed in Phase 10.
3. Cache-clear already moved to scanners.py in Phase 7 — nothing else to do for this phase.

### Test results
- Smoke: GET /api/confluence → 200 (423 symbols, generated_at present).
- Full suite follows (expect 311 passed + pre-existing test_dcb_parity failure).

## Phase 10 — 2026-08-16: Final cleanup

### Files added
- `myra_web/routes/pipeline.py` — GET /api/pipeline/status + /api/pipeline/events (task_tracker-backed)

### Files modified
- `myra_web/myra_fastapi_server.py` — reduced to pure wiring: app creation, CORS, global exception handler, 15 `app.include_router(...)` calls + 4 test-compat re-exports. 302 lines -> 77 lines.
- `myra_web/utils.py` — added `get_db_path` (moved from server; server re-exports it so `test_chart_endpoint.py`'s monkeypatch of `myra_fastapi_server.get_db_path` still works).
- `myra_web/routes/fundamentals.py` — added `GET /live/{symbol}` endpoint (URL preserved at /api/fundamentals/live/{symbol}; NOT full_fundamentals which has a different prefix).
- `myra_web/routes/health.py` — added `GET /pcr/status` (read-only system status fits here).

### Final state
- Server: 77 lines, zero endpoint definitions. All endpoints live in 15 routers:
  fundamentals, full_fundamentals, sentiment, ai_opinion, chart, search, finstack, ml, tools (+portfolio_tools), portfolio, health, scanners, query, confluence, pipeline.
- Total refactor: 4,571-line monolith -> 77-line wiring file.

### Observations
1. **User plan said "full_fundamentals router" for live endpoint — wrong target**: full_fundamentals has prefix `/api/full-fundamentals`; placing live there yields `/api/full-fundamentals/live/{symbol}` (breaks frontend URL). fundamentals.py has prefix `/api/fundamentals` -> correct home. Deviation logged.
2. **get_db_path in utils, NOT removed**: `test_chart_endpoint.py` monkeypatches `myra_fastapi_server.get_db_path` and chart.py lazy-imports `from myra_fastapi_server import get_db_path` at request time — the server re-export keeps both working. The function definition now lives in utils (single source).
3. **Dead code removed from server**: `pipeline_dashboard` import (was never included), `_task_*`/`_get_last_run` guarded import (consumers in tools.py/health.py), unused stdlib imports, `BASE_DIR`/`DB_DIR` redefinition (constants module is the source now).
4. **Re-exports kept for tests**: `verify_myra_auth` + `MYRA_API_SECRET` (4 test modules use dependency_overrides), `_apply_tier_rank` (test_dcb_defaults), `_spawn_task` (test_task_offload), `get_db_path` (test_chart_endpoint monkeypatch), `_run_query` (test_query_endpoint). All no-qa'd with `# noqa: E402`.
5. **Room for further improvement**: `_run_query` could live in a non-router module (e.g., `myra_web/db.py`) since it's a pure executor, not a route; scanner duplication is reduced but launchpad/multibagger remain custom.

### Test results
- Full suite: 311 passed, 1 pre-existing failure (`test_dcb_parity`) — zero new regressions.
- Smoke (21 checks): health, pipeline/status+events, fundamentals/live, fundamentals, 3 scanner statuses, confluence, pcr, chart, search, portfolio+benchmark, ml/status, latest-trading-day, tools/status, query (200/400/401), tools/execute 400 — all pass.
