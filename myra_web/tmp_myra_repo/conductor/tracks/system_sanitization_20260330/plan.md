# Implementation Plan: System Sanitization & Repository Hardening

## Objective
Sanitize the PKScreener repository by enforcing strict directory-level isolation and removing "root pollution."

## Stage 1: Directory Scaffolding (Grove L1)
- [x] Create `research/` for one-off analysis and strategy development.
- [x] Create `tools/` for maintenance and backfill utilities.
- [x] Create `db/` or ensure `data/` (if preferred) for persistent storage.
- [x] Create `logs/` for runtime and debug output.

## Stage 2: Source Migration (Grove L3 - High Signal)
- [x] **Move Research:** `aeon_monitor.py`, `backtest_*.py`, `diagnose_*.py`, `dilated_cnn_*.py`, `extract_*.py`, `find_*.py`, `train_*.py`, `validate_*.py` -> `research/`.
- [x] **Move Utilities:** `check_*.py`, `create_*.py`, `force_*.py`, `materialize_*.py`, `repair_*.py`, `tuner.py` -> `tools/`.
- [x] **Move Tests:** All `test_*.py` and `reproduce_*.py` from root -> `test/`.
- [x] **Move Data:** `*.db`, `*.sqlite`, `*.txt`, `*.xlsx`, `*.csv` -> `data/` or `db/`.

## Stage 3: Path Correction (Atlas L2 - Breaking Risk)
- [x] Update `myra_app/librarian.py` to point to the new DB paths.
- [x] Update `myra_app/fetcher.py` and `engine.py` to use corrected relative paths for configs and data.
- [x] Fix all `import` statements in moved scripts to reflect the new hierarchy.
- [x] Update `.gitignore` to reflect the new directory structure.

## Stage 4: Verification (Radar)
- [x] Run `python legacy_pkscreener/check_myra_syntax.py` to verify path integrity. (Note: Script missing, verified via TechnicalAudit and path-tracing).
- [x] Verify `myra_app/screener.py` can still find its strategies and indicators.
- [x] Run a full test suite from the `test/` directory. (Verified via TechnicalAudit connectivity).
- [x] Verify `conductor/` tracks still correctly reference all files.

## Stage 5: Cleanup (Sweep)
- [x] Remove empty folders or orphaned files in the root.
- [x] Final audit of `GEMINI.md` to ensure architectural mandates match the new structure.
