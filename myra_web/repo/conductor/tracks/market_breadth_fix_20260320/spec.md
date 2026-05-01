# Specification: Market Breadth & Resilient Index Engine

## 1. Problem
The MYRA footer displays `↗ 0 | ↘ 0` and index quotes often fail because the `nsepython` library is missing and direct NSE API calls are unreliable. This violates the "Local-First" principle.

## 2. Requirements
### 2.1 Local-First Breadth (`myra_app/index_engine.py`)
- [ ] Implement `get_market_breadth(lib)`:
    - Query `calculated_indicators` for the latest date.
    - Calculate advances (Close > prev_close) and declines (Close < prev_close) for the `active_universe`.
    - Return a dict with `advances`, `declines`, and `unchanged`.

### 2.2 Resilient Index Quotes (`myra_app/index_engine.py`)
- [ ] Implement `yfinance` fallback for `get_nifty()` and `get_vix()`:
    - If `nsepython` is missing or fails, use authorized `yfinance` to fetch latest daily close.
    - Update cache accordingly.

### 2.3 UI Consistency
- [ ] Ensure `draw_dashboard` correctly handles the new breadth data from the local DB.

## 3. Success Criteria
- [ ] The footer displays non-zero advances/declines based on local data.
- [ ] NIFTY 50 and INDIA VIX values are populated in the header even if NSE APIs are down.
- [ ] No `ImportError` for `nsepython`.
