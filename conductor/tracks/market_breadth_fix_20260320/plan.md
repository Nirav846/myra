# Implementation Plan: Market Breadth & Resilient Index Engine

## Phase 1: Local Breadth Implementation
- [x] Modify `IndexEngine.get_market_breadth` in `myra_app/index_engine.py`:
    - Accept `librarian` as an argument (optional, fallback to local query).
    - Query `calculated_indicators` for ADV/DEC counts of `active_universe`.
- [x] Update `myra.py` call to `get_market_breadth(screener.lib)`.

## Phase 2: YFinance Fallback
- [x] Update `IndexEngine.get_nifty()` and `IndexEngine.get_vix()`:
    - Add `try-except` block for `nse_get_index_quote`.
    - Implement `yf.download()` fallback for `^NSEI` and `^INDIAVIX`.
- [x] Add `yfinance` and `requests` imports to `index_engine.py`.

## Phase 3: Validation
- [ ] Run `python myra_app/myra.py` and verify Header (NIFTY/VIX) and Footer (Status/Breadth).
- [ ] Check logs for any data acquisition failures.
