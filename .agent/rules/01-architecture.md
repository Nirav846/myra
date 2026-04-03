# Architecture Principles (v2.5)
The system is built on a strictly isolated Seven-Layer Orchestration:
- `myra_app/screener.py`: Central coordinator and strategy pipeline execution.
- `myra_app/librarian.py`: Data lake manager (Exclusive gatekeeper for DuckDB).
- `myra_app/engine.py`: Universal Turbo-SQL engine and quantitative math layer.
- `myra_app/fetcher.py`: Smart Proxy & Multi-Source acquisition.
- `myra_app/index_engine.py`: Native NSE Index & VIX Engine.
- `myra_app/fundamental_manager.py`: Multi-factor data orchestration.
- `myra_app/myra.py`: Pure UI & Navigator.

# Universe Tiering & Capital Management
- **Universe Mastery:** USE `symbols_master` to prioritize Tier 1 (NIFTY 100), Tier 2 (NIFTY 500), and Tier 3 (Active Universe).
- **Risk-Weighted Sizing:** Apply **RA%** position sizing using ATR and Conviction Stars.
- **Index Boosting:** Automatically boost conviction for NIFTY 100 (+2) and NIFTY 500 (+1) stocks.
- **Funda_Score:** Maintain the 0-100 semantic pattern combining Growth, Quality, Valuation, and Risk.
