# Episodic Log: Complete Data Flow Analysis
**Date**: Friday, 13 March 2026

## Objective
Analyze and document the complete data flow for stock data within PKScreener to provide a foundational reference for future modifications and architectural enhancements.

## Findings

### Data Acquisition Layers
1.  **High-Performance / Real-Time**: `Fetcher.py` prioritizes `PKDataProvider` (PKBrokers) for in-memory candle stores during market hours.
2.  **GitHub Synchronization**: `AssetsManager.py` synchronizes with the `actions-data-download` branch to fetch pre-computed `.pkl` files and `ticks.json` for rapid data loading without API overhead.
3.  **Fallback (yfinance/NSE)**: `Fetcher.py` and `PKNSETools` serve as fallback mechanisms when caches are unavailable or stale.

### Data Flow Pipeline
-   **Orchestration**: `DataLoader.py` initializes the dictionaries and manages the transition between historical (Primary) and intraday (Secondary) data.
-   **Transformation**: `StockScreener.py` converts the raw dictionary data (in "split" format) into `pandas.DataFrame`, resamples as needed, and sorts in descending order (index `0` is most recent).
-   **Execution**: `ScreeningStatistics.py` and `Pktalib.py` perform the technical and fundamental analysis, generating the final signals.

## Data Structure
-   **Format**: `to_dict("split")` for storage (columns, index, data).
-   **Standard Columns**: `open`, `high`, `low`, `close`, `volume`, `Adj Close`.
-   **Orientation**: Validation functions expect descending chronological order (Latest at index 0).

## Call Graph
```mermaid
graph TD
    A[MainApplication / CLI] --> B[DataLoader]
    B --> C[AssetsManager]
    C --> D[Fetcher]
    C --> E[GitHub / Remote Server]
    D --> F[yfinance / PKNSETools]
    E --> G[ticks.json / stock_data_*.pkl]
    B --> H[PKMultiProcessorClient]
    H --> I[StockScreener (Worker Process)]
    I --> J[ScreeningStatistics]
    J --> K[Pktalib / advanced_ta]
    I --> L[CandlePatterns]
    J --> M[morningstartools]
    I --> N[ResultsManager / AssetsManager.promptSaveResults]
    N --> O[Excel / CSV / Console]
```
