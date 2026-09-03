# DCB Bargain Scanner — Audit Fixes

Source audit: `docs/dcb_scanner_audit.md` (see chat history "DCB Bargain Scanner — Read-Only Audit").

This list tracks fixes for the 8 issues identified by the audit, in priority order (Critical → Medium → Low). Each fix is a separate commit.

## Critical

### Issue 1 — Corporate-action filter uses wrong date column [Done]
- **File**: `myra_app/strategies/dcb_bargain.py` — `_filter_corporate_actions` (around line 960-967)
- **Problem**: Filter compares `ex_date` (stored as NSE `DD-MMM-YYYY` text) against an ISO `YYYY-MM-DD` cutoff. SQLite text comparison makes every `ex_date` starting with '3' sort after any 2024-2026 cutoff → most symbols with recorded CAs are spuriously excluded.
- **Fix**: Change `WHERE ex_date >= ?` to `WHERE date >= ?` and update the parameter binding. The table has a properly formatted ISO `date` column.
- **Verification**:
  - Unit test: build a `corporate_actions` table in a temp DB with mixed dates, run `_filter_corporate_actions`, assert old CAs are NOT excluded and recent ones ARE.
  - `pytest tests/test_dcb_bargain.py -v` (full module).
  - Spot-check against the real DB: count excluded symbols before vs after.
- **Commit**: `fix(dcb): use correct date column for corporate action filter`

### Issue 2 — Guard against `_bulk_data is None` in `scan()` [Done]
- **File**: `myra_app/strategies/dcb_bargain.py` — `scan()` (after line 543)
- **Problem**: The parity test (`tests/test_bulk_loader.py::test_dcb_parity`) monkey-patches `load_ohlcv_for_universe` to return `None`, then the scanner calls `get_df_for_symbol(None, ...)` and crashes with `AttributeError`. In production `bulk_data` is always a dict, but defending against `None` is cheap and unblocks the parity test.
- **Fix**: After the `self._bulk_data = load_ohlcv_for_universe(...)` assignment, add `if self._bulk_data is None: logger.warning(...); return pd.DataFrame()`.
- **Verification**:
  - `pytest tests/test_bulk_loader.py::TestScannerBulkParity::test_dcb_parity` — should now pass.
  - Full module: `pytest tests/test_dcb_bargain.py -v`.
- **Commit**: `fix(dcb): guard against None bulk_data in scan`

## Medium

### Issue 3 — `restrict_to_holdings` is misnamed; reads `fund_traction`, not real holdings [Done]
- **Files**: `myra_app/strategies/dcb_bargain.py`, `myra_app/utils/fund_utils.py`, `myra_web/routes/scanners.py`, frontend API docs.
- **Problem**: The flag's name promises mutual-fund holdings filtering but it actually filters by `fund_traction` membership (latest month). Confusing for users.
- **Fix (Option A — rename + document)**: rename to `restrict_to_traction_universe` and update the API, defaults, and the docstring on `get_holding_symbols`. Add a TODO for Option B (real MF holdings from `cross-fund-holdings-traction/temp_holdings`).
- **Verification**:
  - `pytest tests/test_dcb_bargain.py tests/test_dcb_defaults.py -v` (no regressions).
  - Visual review of `_dcb_parse`, `_dcb_build`, `dcb_bargain_defaults` to confirm the rename.
- **Commit**: `refactor(dcb): rename restrict_to_holdings to restrict_to_traction_universe`

### Issue 4 — Log count of symbols skipped due to missing free-float [Done]
- **File**: `myra_app/strategies/dcb_bargain.py` — after the per-symbol loop (around line 705)
- **Problem**: When `min_ff_mcap > 0` and `ff_pct IS NULL`, the symbol is silently dropped (DEBUG log only). With default params, ~1232/3887 symbols are dropped without operator visibility.
- **Fix**: Add a counter in the per-symbol loop, emit an INFO log after the loop with the count.
- **Verification**:
  - `pytest tests/test_dcb_bargain.py -v` (existing tests still pass).
  - Manual log check: run a scan and confirm the count is logged.
- **Commit**: `feat(dcb): log count of symbols skipped due to missing free_float`

### Issue 5 — Add comments for unvalidated magic numbers [Done]
- **File**: `myra_app/strategies/dcb_bargain.py`
- **Problem**: The audit flagged 6 unvalidated thresholds: `min_high_del_days`, `sanity_mult`, 5% circuit drop, 3-day streak, 20% volume collapse, 1.3× delivery spike, 0.6 close-location, tier score cutoffs, 0.6/0.4 score weighting.
- **Fix**: For each, ensure the existing `TODO: validate with backtest` comment is preserved and, where missing, add a clear comment explaining the choice is empirical.
- **Verification**: `pytest tests/test_dcb_bargain.py -v` (no behavioural changes).
- **Commit**: `docs(dcb): add comments for unvalidated magic numbers`

## Low

### Issue 6 — Add end-to-end test for `timeframe="weekly"` [Done]
- **File**: `tests/test_dcb_bargain.py`
- **Problem**: Only the `_get_weekly_data` helper is tested. The full `scan()` with `timeframe="weekly"` is uncovered.
- **Fix**: Add a test that monkey-patches `load_ohlcv_for_universe` and `get_df_for_symbol` with ≥250 daily rows, runs a scan with `timeframe="weekly"`, and asserts a non-empty weekly-corrected output.
- **Verification**: `pytest tests/test_dcb_bargain.py -v` (new test passes).
- **Commit**: `test(dcb): add end-to-end weekly timeframe test`

### Issue 7 — Document circuit heuristic limitation [Done]
- **File**: `myra_app/strategies/dcb_bargain.py` — `_is_lower_circuit` (lines 287-308) and `_is_likely_circuit_lock` (lines 367-413)
- **Problem**: The 5% heuristic is a proxy for NSE circuit bands (2/5/10/20% per stock). The code already has a comment, but expand it to make the limitation explicit and link to a replacement plan.
- **Fix**: Replace the existing short comment with a longer, more explicit block explaining why the heuristic is approximate and what to replace it with.
- **Verification**: `pytest tests/test_dcb_bargain.py -v` (no behavioural changes).
- **Commit**: `docs(dcb): document circuit heuristic limitation`

### Issue 8 — Add failure count log line in `scan()` [Done]
- **File**: `myra_app/strategies/dcb_bargain.py` — `scan()` (around line 705)
- **Problem**: Per-symbol exceptions are caught and logged at `logger.exception` level but a count is never summarised at the end. Operator visibility is limited if a systemic issue breaks many symbols.
- **Fix**: Add a counter in the per-symbol `except` block and an INFO log at the end with the failure count.
- **Verification**: `pytest tests/test_dcb_bargain.py -v` (no regressions).
- **Commit**: `feat(dcb): log count of failed symbols in scan summary`

## Status
- All items: Done.

## Commit log
1. `c395e23` — `fix(dcb): use correct date column for corporate action filter`
2. `e080e78` — `fix(dcb): guard against None bulk_data in scan`
3. `7fa3189` — `refactor(dcb): rename restrict_to_holdings to restrict_to_traction_universe`
4. `293903c` — `feat(dcb): log count of symbols skipped due to missing free_float`
5. `5a88b7b` — `docs(dcb): add comments for unvalidated magic numbers`
6. `c9cb310` — `test(dcb): add end-to-end weekly timeframe test`
7. `5a94473` — `docs(dcb): document circuit heuristic limitation`
8. `b03d552` — `feat(dcb): log count of failed symbols in scan summary`

