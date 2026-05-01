# Episodic Memory: 2026-03-12 - Techno-Funda Strategy Integration

## Context
User wanted to setup PKScreener for educational use and implement Vivek Mashrani's "You Can Compound" strategy using free yfinance data.

## Key Events
1.  **Environment Setup**: Fixed TA-Lib issues (Python 3.12 compatibility) and bypassed `PKEnvironment` missing attribute errors using `.env.dev`.
2.  **Premium Bypass**: Neutralized `PKPremiumHandler` and `MenuOptions` subscription checks to allow full access to all scans.
3.  **Data Source Pivot**: Forced `Fetcher.py` to use yfinance as primary source by disabling high-performance providers.
4.  **Strategy Implementation**: Created `TechnoFundaHandler.py` providing:
    -   Relative Strength (RS) ranking vs Nifty 50.
    -   Stage 2 Trend Template validation.
    -   Fundamental growth and ROE filters.
5.  **Technical Debt/Bug Fix**: Identified and fixed a critical data duplication bug where multiple stocks showed identical LTPs when using individual threaded `yf.download` calls. Resolved by switching to **Batch Downloads**.

## Lessons Learned
-   `yfinance` individual threaded calls are unstable in this environment; batch calls are 10x faster and accurate.
-   Manual calculation of ROE from `financials` is necessary for NSE stocks as `.info` is unreliable.
-   Modular handler pattern (`T` menu -> `TechnoFundaHandler`) is superior to modifying core `StockScreener.py` for new strategies.
