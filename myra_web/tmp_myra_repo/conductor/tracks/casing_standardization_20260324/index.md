# Track: Casing Standardization (Snake_Case vs CamelCase)
**Status**: ACTIVE
**Owner**: Gemini CLI (Zen & Conductor)

## Objective
Standardize naming conventions across the MYRA ecosystem to eliminate bugs caused by inconsistent column casing (e.g., `close` vs `Close`, `d_poc` vs `D-POC`).

## Proposed Standards (The MYRA Way)
1.  **Database (DuckDB)**: All columns MUST be `lowercase_snake_case`.
2.  **Internal DataFrames**: OHLCV columns will use `CamelCase` (`Open`, `High`, `Low`, `Close`, `Volume`) to maintain compatibility with `pandas_ta` and legacy `PKScreener` logic. All other technical indicators will use `snake_case`.
3.  **UI Payloads / Hero Columns**: Consistent `Title_Case` or `snake_case` as defined in the strategy mapping.

## Implementation Plan
- [ ] Phase 1: Deep Consistency Audit (Zen Skill).
- [ ] Phase 2: Standardize `fetcher.py` and `librarian.py` (Persistence Layer).
- [ ] Phase 3: Standardize `engine.py` and `scanners/` (Processing Layer).
- [ ] Phase 4: Standardize `results_manager.py` and `myra.py` (UI Layer).

## Documents
- [Implementation Plan](./plan.md)
