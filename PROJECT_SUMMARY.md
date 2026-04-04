# PROJECT_SUMMARY: MYRA (Myra Yield & Research Analytics)

## Description
MYRA is a specialized stock screening and research analytics platform designed for the National Stock Exchange (NSE) of India. It emphasizes high-fidelity technical analysis (OHLCV, Delivery, VWAP), institutional activity tracking (Insider Trades, Large Deals), and modular data management for the v3.0 era.

## Tech Stack
- **Languages:** Python (Primary).
- **Data Layers:** SQLite (Atomic Trilogy Sidecars: technical.db, institutional.db, meta.db, valuation.db).
- **Storage:** Parquet (Indicator Lake for strategy results to prevent schema contention).
- **Libraries:** PKNSETools, morningstartools, PKDevTools (Authoritative sources for NSE data).
- **Analytics:** pandas, numpy, xgboost, tensorflow (Dilated CNN), pandas_ta.
- **UI/CLI:** rich, tqdm (High-fidelity CLI terminal experience).

## Core Features
- **Atomic Trilogy Architecture:** Specialized SQLite sidecars to prevent file locking and schema contention.
- **Indicator Lake:** Strategy-specific results saved to isolated Parquet files to isolate technical indicators from main SQL schemas.
- **Institutional Intelligence:** Tracking insider trades (> ₹10L materiality) and calculating the 'Underwater Signal' (LTP < Insider_Cost_Basis).
- **Self-Healing Data Layer:** Automatic retrieval and backfill of missing metrics via the `DataAdapter` and `Librarian`.
- **Advanced Scanning:** Smart Money Concepts (SMC), Volume Spread Analysis (VSA), and multi-timeframe technical screening.
- **Evolutionary ML:** AEON Agent using Deep Evolution Strategies (DES) and Dilated CNNs for sequence-to-sequence forecasting.

## Architecture
- **Modular Architecture v3.0 (Atomic Trilogy):** A decoupled, sidecar-based system where data (SQL), indicators (Parquet), and logic (Engine) are isolated to ensure stability and performance.
- **Absolute Root Anchoring:** High-resilience pathing logic used across all tools and research scripts to ensure cross-directory execution.
- **Vectorized ML Conviction:** High-performance feature reconstruction for 680-stock scanning on AMD APU/low-resource systems.
- **Unified Data Access:** The `DataAdapter` and `IndicatorManager` provide a single interface for all data operations, abstracting the underlying SQL/Parquet split.

## External Integrations
- **NSE India:** Primary data source for Bhavcopies, insider trades, and corporate actions.
- **Yahoo Finance:** Fallback data source for global symbols and basic technicals.
- **Open-Source Libraries:** IndStocks, indstocks_source (fallback or for reference implementation).
