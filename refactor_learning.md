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
