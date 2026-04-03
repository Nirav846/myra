# PROJECT_SUMMARY: MYRA (Myra Yield & Research Analytics)

## Description
MYRA is a specialized stock screening and research analytics platform designed for the National Stock Exchange (NSE) of India. It emphasizes high-fidelity technical analysis (OHLCV, Delivery, VWAP), institutional activity tracking (Insider Trades, Large Deals), and modular data management.

## Tech Stack
- **Languages:** Python (Primary).
- **Data Layers:** SQLite (Atomic Trilogy Sidecars: technical.db, institutional.db, meta.db, valuation.db).
- **Storage:** Parquet (Indicator Lake for strategy results to prevent schema contention).
- **Libraries:** PKNSETools, morningstartools, PKDevTools (Authoritative sources for NSE data).
- **Frameworks:** Likely FastAPI or Flask for core services (initial investigation points to a CLI/service-oriented architecture).
- **Analytics:** pandas, pandas_ta, DuckDB (legacy/internal use).

## Core Features
- **Atomic Trilogy Architecture:** Specialized SQLite sidecars to prevent file locking and schema contention.
- **Indicator Lake:** Strategy-specific results saved to isolated Parquet files to isolate technical indicators from the main SQL schemas.
- **Institutional Intelligence:** Tracking insider trades (> ₹10L materiality) and calculating the 'Underwater Signal' (LTP < Insider_Cost_Basis).
- **Self-Healing Data Layer:** Automatic retrieval and backfill of missing metrics via the `DataAdapter` and `Librarian`.
- **Advanced Scanning:** Smart Money Concepts (SMC), Volume Spread Analysis (VSA), and multi-timeframe technical screening.

## Architecture
- **Modular Architecture v3.0:** A decoupled, sidecar-based system where data (SQL), indicators (Parquet), and logic (Engine) are isolated to ensure stability and performance.
- **Unified Data Access:** The `DataAdapter` and `IndicatorManager` provide a single interface for all data operations, abstracting away the underlying SQL/Parquet split.

## External Integrations
- **NSE India:** Primary data source for Bhavcopies, insider trades, and corporate actions.
- **Open-Source Libraries:** nsepython, OpenStockIndia, yfinance (used as fallback or for reference implementation via `myra-github-reader`).
