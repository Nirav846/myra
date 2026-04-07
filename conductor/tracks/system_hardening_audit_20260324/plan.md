# Implementation Plan: System Hardening & Resilience Audit

## Objective
Harden the MYRA v2.5 architecture against security, concurrency, and performance regressions.

## Stage 1: Sentinel (Security)
- [ ] Scan `fetcher.py` and `librarian.py` for raw f-string SQL queries (prevent SQLi).
- [ ] Verify `.env` and `myra_sources.json` are properly ignored/protected.
- [ ] Implement data-type sanitization for incoming NSE JSON payloads.

## Stage 2: Specter (Concurrency)
- [ ] Audit `librarian.py` for thread-safe database connections (background sync vs main process).
- [ ] Check `engine.py` multiprocessing pool for zombie processes or memory bloat.
- [ ] Resolve the "Database is locked" issue permanently with a robust retry/wait mechanism.

## Stage 3: Radar (Reliability)
- [ ] Create `test/regression_v25.py` to verify FVG and SMC-2 calculations.
- [ ] Test the Hybrid Data Loader with "missing indicator" stock samples.
- [ ] Verify UI auto-adjustment across different terminal widths.

## Stage 4: Tuner (Performance)
- [ ] Analyze EXPLAIN plans for `update_indicator_history` (the heaviest query).
- [ ] Add covering indices for the `prices` table to speed up 3-year lookbacks.
- [ ] Optimize memory usage in `ml_engine.py` during bulk inference.
