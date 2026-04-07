# Track: Database Expansion (2021-Present)
**Status**: ACTIVE
**Owner**: Gemini CLI

## Objective
Backfill NSE Bhavcopy and Delivery data starting from January 1, 2021, and prepare a 5-year dataset for Machine Learning model training.

## Files
- `myra_app/librarian.py`
- `myra_app/fetcher.py`
- `force_backfill_2021.py`

## Plan
- [x] Research optimized archival endpoints.
- [x] Implement batch backfill script.
- [~] Run the backfill in background chunks. (Active: Background Process PID 2508).
- [ ] Modify `Librarian` to compute indicators for the full 2000-day window (ML Readiness).
- [ ] Re-calculate indicators across the entire expanded timeline.

## Status Report (2026-03-22 02:10)
- **2021**: 261 files (Complete)
- **2022**: 158/250 files (In Progress)
- **2023**: 260 files (Complete)
- **2024**: 262 files (Complete)
- **Total Progress**: ~85% of total required history fetched.
