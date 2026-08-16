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
