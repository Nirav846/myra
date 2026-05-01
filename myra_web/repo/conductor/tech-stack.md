# Tech Stack

## Core
- **Language:** Python 3.12
- **Data Engine:** DuckDB (Exclusive via `librarian.py`)
- **Data Processing:** Pandas, Polars
- **Math & Logic:** Universal Turbo-SQL & Quantitative Math (via `engine.py`)
- **ML Engine:** XGBoost Ensemble Forecaster (via `ml_engine.py`)
- **Global Sorting:** ResultsManager-level "Best First" Gatekeeper.
- **Scoring:** Composite Smart Money Scoring (RDV + Delivery % + Squeeze)
- **Institutional Intensity:** Value-weighted deal aggregation relative to Market Cap.
- **VSA Intensity:** (Rel_Spread / Rel_Vol) * Delivery_Percent logic.
- **Floor Mapping:** Multi-year support floors (1Y, 2Y, 3Y) via SQL window functions.

## Networking & Fetching
- **Service:** Multi-Source Fetcher with Smart Proxy (via `fetcher.py`)
- **Intelligence:** 24h fallback cache & Smart Proxy unification

## Interface & UI
- **Navigator:** Pure UI & CLI (via `myra.py`)
- **Framework:** Rich-based "Mission Control" Layout & Tactical Grid

## Architecture
- **Version:** MYRA v2.5 Modular Orchestration
- **Layers:** 7-Layer strictly isolated orchestration (Screener, Librarian, Engine, Fetcher, Index Engine, Fundamental Manager, Myra)
