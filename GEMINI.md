# ⚡ MYRA System Prompt - v3.0 (Atomic Trilogy)

## Core Mandate
Language Mandate: All communication, logging, planning, and code comments MUST be strictly in English.
This workspace is the home of **MYRA (Myra Yield & Research Analytics)**. All logic MUST adhere to the **Modular Architecture v3.0 (Atomic Trilogy)**.

## 0. Library-First Research Mandate
**STRICT PRIORITY:** Before searching online or proposing new fetchers, you MUST deeply analyze the locally installed libraries in `pkscreener_env/Lib/site-packages`. 
- **Primary Source:** `PKNSETools` (and its `morningstartools`) is the definitive engine for NSE data and classification.
- **Support Source:** `PKDevTools` contains core utilities, archivers, and fetchers.
- Only if these libraries lack the required logic or data points should you look for external APIs or scrapers.

## 1. Atomic Trilogy Architecture (Modular Sidecars)
DuckDB is DEPRECATED. The system uses specialized SQLite "Sidecars" to prevent file locking and schema contention:
- `db/technical.db`: High-fidelity OHLCV + Delivery + VWAP from Bhavcopies.
- `db/institutional.db`: Insider Trades (> ₹10L materiality) and Large Deals.
- `db/meta.db`: Master Symbol List, Index Constituents, and Benchmarks.
- `db/valuation.db`: Fundamentals and Quarterly Results.
- `db/network_cache.sqlite`: Consolidated Scrapling/Request cache.

## 2. The Indicator Lake (Anti-Error Loop)
STRICT RULE: Never add technical indicator columns (e.g., RSI, SMA, SMC) to the main SQLite databases.
- **Storage:** Results must be saved to `data/indicators/{strategy_id}/{symbol}.parquet`.
- **Isolation:** This ensures new scanners NEVER break existing SQL schemas or trigger "Error Loops."
- **Access:** Use `IndicatorManager` via the `DataAdapter` for unified retrieval.

## 3. Institutional Intelligence Standards
- **Materiality Filter:** Only insider trades > ₹10 Lakhs (0.1Cr) are tracked to eliminate compliance noise.
- **Cost Basis:** Always track the `avg_price` of insider entries.
- **Underwater Signal:** A high-conviction signal triggered when `LTP < Insider_Cost_Basis`.
- **Symbol Mapping:** Use `SymbolMapper` to handle name changes (e.g., LTIM -> LTM) recursively.

## 4. Naming & Casing Standards
- **Database (SQLite)**: All columns must be `lowercase_snake_case`.
- **OHLCV DataFrames**: Columns MUST use `CamelCase` (`Open`, `High`, `Low`, `Close`, `Volume`) for `pandas_ta` and legacy compatibility.
- **Indicator Lake**: Use `lowercase_snake_case` for all calculated metrics.

## 5. Performance & Concurrency
- **Thread Safety:** Background sync must use `check_same_thread=False` and WAL mode in SQLite.
- **Delta Computing:** Technicals are delta-computed and stored in the Parquet Lake. Never re-process full history unless the Lake is purged.

## 6. Development Workflow
- Run `python tools/validate_librarian.py` after any data layer change.
- Run `python tools/mission_control_audit.py` to verify end-to-end scanner mapping.
- Before code hand-off, ensure Scrapling warnings are silenced via the `warnings` filter.
