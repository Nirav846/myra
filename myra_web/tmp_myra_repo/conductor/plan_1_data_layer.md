# BRAINSTORM PLAN 1: Stealth Data Ingestion Engine
# Inspired by: PNSEA, nsetools, StockScan

## 1. Objective
Enable deep, undetectable access to high-value NSE data points (Option Chain, Pledged Shares, Insider Trading) and create a high-speed bulk historical export engine.

## 2. Key Components
- **Stealth Scraper (PNSEA Style):**
    - Upgrade GhostSession to use 'stealthkit' patterns.
    - Implement automatic TLS fingerprint rotation.
    - Map direct internal JSON endpoints for Long-Term Risk:
        - Corporate Pledged Info: `/api/corporate-pledged-info` (Critical for margin call risk).
        - SAST / Insider Trading: `/api/corporate-insider-trading` (Tracking long-term conviction).
        - FII/DII Activity: `/api/fii-dii` (Tracking institutional flow).
- **Bulk Data Engine (StockScan Style):**
    - Multi-threaded historical OHLCV downloader.
    - Parquet storage for backtesting 1-3 year cycles.
- **Data Normalization Layer:**
    - Standardize Corporate Governance metrics across symbols.

## 3. Implementation Workflow
1.  Verify exact header requirements for the Pledged Info API.
2.  Build a 'Discovery' script to find all internal NSE API paths.
3.  Implement a 'BulkExporter' class that converts full-year Bhavcopies to Symbol-wise Parquet files.

## 4. Success Criteria
- Zero 403 Forbidden errors over 100+ requests.
- Successful fetch of Promoter Pledged % for all Nifty 500 stocks.
- Accurate mapping of SAST Insider transactions to symbols.

