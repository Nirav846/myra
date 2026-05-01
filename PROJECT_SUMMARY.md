# PROJECT_SUMMARY: MYRA (Myra Yield & Research Analytics)

## Description
MYRA is an atomic trading system for the National Stock Exchange (NSE) of India. It combines high-fidelity technical analysis, institutional activity tracking, and a factor-based positional scoring engine (v2.5) for 1-24 month holdings.

## Tech Stack
- **Languages:** Python 3.10+
- **Data Layer:** SQLite (modular sidecars: `technical.db`, `institutional.db`, `meta.db`, `valuation.db`)
- **Storage:** Parquet (Indicator Lake for strategy results)
- **Libraries:** pandas, numpy, pandas_ta, xgboost, tensorflow
- **UI/CLI:** rich, myra_log (minimalist terminal experience)
- **NSE Data:** myra_core (localized from PKScreener), morningstartools

## Core Features
- **v2.5 Positional Engine:** Factor-based ranking with trend, stability, delivery, liquidity, base, and fundamental scores
- **Modular Factors:** BaseFactor abstract class with DeliveryFactor, RSFactor, IASFactor implementations
- **Strategy Framework:** BaseStrategy with market mood detection, Kelly criterion sizing, and AI hooks
- **Institutional Intelligence:** Insider trades (> ₹10L), large deals, delivery divergence scoring
- **SMC & VSA:** Smart Money Concepts with Fair Value Gaps, absorption, and tightness analysis
- **ML Integration:** AEON Agent (Deep Evolution Strategies), Surpriver v2 (multi-window anomaly detection)
- **Resilient Data Pipeline:** Watchdog for stuck scans, process timeouts, adaptive source selection

## Architecture
- **Engine (UNIVERSAL SQL v12):** Unified precompute for standard and piped scans, multiprocessing worker pool
- **PositionalScorer:** Vectorized scoring with regime adjustment and drawdown filtering
- **Librarian Modularization:** Decomposed into Core, Intelligence, Ingestor, Sync, and Schema modules
- **Atomic Trilogy:** SQLite sidecars prevent file locking; Parquet Lake isolates strategy indicators
- **Thread Safety:** Global locks, WAL mode, retry mechanisms for concurrent access

## Strategy Ecosystem
- **Surpriver v2:** Multi-window (5/10/15/20/30-day) institutional accumulation detection
- **AEON Agent:** Evolutionary Strategy optimization for SMC entry/exit timing
- **Alpha Strategies:** Delivery clusters, liquidity vacuums, supply absorption, RS leaders
- **Scanners:** Bottom hunter, multibagger early detection, institutional structural flow

## External Integrations
- **NSE India:** Bhavcopies, insider trades, corporate actions
- **Yahoo Finance:** Fallback for index quotes (NIFTY, VIX)
- **Morningstar:** Fundamental data
