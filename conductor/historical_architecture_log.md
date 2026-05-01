# MYRA Historical Architecture Log

This document synthesizes architectural decisions, bug fixes, and completed milestones from conductor/tracks/ (2026-03-18 to 2026-05-01).

---

## Architecture Evolution

### v2.5 Positional Engine & Factor-Based Ranking
- **Librarian Modularization**: Decomposed 900-line God Class into specialized modules:
  - `librarian_core.py`: Base class and locking logic
  - `librarian_intelligence.py`: Turbo-SQL indicator computation
  - `librarian_ingestor.py`: Bhavcopy ingestion
  - `librarian_sync.py`: Background thread management
  - `librarian_schema.py`: Table creation and migration
- **Modular Database Schema**: Migrated from monolithic DuckDB to SQLite sidecars:
  - `technical.db`: OHLCV + Delivery
  - `institutional.db`: Insider trades, large deals
  - `meta.db`: Symbols master, benchmarks
  - `valuation.db`: Fundamentals, quarterly results
- **Parquet Lake Pattern**: Strategy-isolated indicator storage to prevent schema conflicts

### Data Pipeline Resilience (v9.0)
- **Fault Tolerance**: Process wrappers with hard timeouts, Requests-First fetching, SQLite WAL mode
- **Data Integrity**: Validation gates, global safety fuses, NSE retry loops
- **Intelligent Decision Engine**: Confidence-based quality scoring, best-source selection
- **Adaptive Learning**: Source reliability memory with recency-deay weights
- **Market Context**: Holiday shields, dynamic thresholding, post-holiday cooldowns
- **Truth Validation**: Cross-day consistency checks, sector coverage guards, cache integrity hashes

### System Hardening (2026-03-24)
- **Security**: Parameterized all SQL queries to prevent injection
- **Concurrency**: Thread locks and retry mechanisms for database access
- **Reliability**: Regression test suite (`test/regression_v25.py`)
- **Performance**: Covering indices for heavy queries (3-year lookbacks)

### Repository Sanitization (2026-03-30)
- **Directory Isolation**: Moved 60+ root scripts to `research/`, `tools/`, `test/`
- **Data Organization**: Isolated `.db`, `.sqlite`, `.txt` files to `data/` and `db/`
- **Path Corrections**: Updated all imports and relative paths

### Decoupling from PKScreener
- **myra_core Package**: Localized PKDevTools, PKNSETools, PKBrokers dependencies
- **Import Refactoring**: Replaced all external imports with myra_core
- **Dependency Removal**: Removed root `pkscreener/` folder and PK* libraries

---

## Bug Fixes

### Scanner & UI Stability
- **Blinking Footer**: Cached DB stats in Librarian, removed conflicting global Live context
- **Silent Exit Bug**: Fixed missing `smc_phase` and `d_poc` in worker's funda_map
- **Input Blocking**: Disabled auto_refresh, lowered refresh rate
- **Progress Bar**: Implemented minimalist single-line progress with `\r` throttling

### SMC Logic Refinements
- **Tightness Calculation**: Fixed 0.0% values by ensuring `std20` persistence and proper division
- **Phase 1 Criteria**: Stricter tightness threshold (<1.5%), volume dry-up check
- **Column De-duplication**: Fixed duplicate Stage/Confluence columns in discovery tables
- **Fundamental Bypass**: SMC scans (126, 30) now skip fundamental enrichment

### Data Pipeline Fixes
- **Market Breadth**: Local-first calculation from `calculated_indicators`, YFinance fallback for index quotes
- **Indicator Staleness**: Optimized 300-day refresh for stale indicators
- **Casing Standardization**: Database columns use `lowercase_snake_case`, OHLCV uses `CamelCase`

---

## Completed Milestones

### AEON ML Agent (Strategy 31)
- **Neural Core**: EvolutionaryAgent class with weight gene mapping
- **Environment**: SMCEnvironment with vectorized evaluation
- **Training**: DeepEvolutionStrategy (NES-style) gradient estimation
- **Inference**: AEONEngine in `ml_engine.py` with joblib weights
- **UI Polish**: Restored D-POC column, universal 2-decimal precision formatting

### NSE Surpriver v2 (Strategy 34)
- **Multi-Window Consistency**: 5, 10, 15, 20, 30-day lookback analysis
- **Delivery-Weighted Z-Score**: Anomaly detection logic
- **Supply Absorption**: Buying Wick filter
- **Multi-Scale Volatility**: Tightness compression metrics

### Database Expansion (2021-Present)
- **Backfill**: 85% of 5-year dataset fetched (2021-2026)
- **ML Readiness**: 2000-day indicator window configuration
- **Progress**: 2021 (complete), 2022 (in progress), 2023-2024 (complete)

### CLI Design Audit
- **Categorized Headers**: Technical vs Institutional vs Tactical groupings
- **Trust Loop**: Sparkline-style indicators for recent performance
- **Color Palette**: Reduced vibrancy fatigue
- **Glossary**: Simplified definitions for SMC metrics

### Performance Optimization
- **Database Indexing**: Added `idx_calc_ind_sym_date`, `idx_prices_date`, `idx_insider_sym_date`
- **Application**: Replaced `iterrows()` with `to_dict('records')` for 10x+ speedup

---

## Active/Incomplete Tracks

### AEON Performance Audit
- 3-month backtest for Strategy 31 (Dec 2025 - March 2026)
- Success criteria: Hit Rate > 60%, Profit Factor > 1.5

### Casing Standardization
- Ongoing effort to standardize naming conventions across the ecosystem
- Standards: DB (snake_case), DataFrames (CamelCase for OHLCV), UI (Title_Case/snake_case)

### Glossary Simplification
- Compact definitions for D-POC, Absorption, Tightness, RDV, AEON
- Rich markup fixes applied

---

## Key Files Modified

- `myra_app/librarian.py` → Split into modular components
- `myra_app/fetcher.py` → Resilience layers, scoring, reliability memory
- `myra_app/engine.py` → Multiprocessing, safety fuses, tightness fixes
- `myra_app/myra.py` → UI polish, glossary, strategy mappings
- `myra_app/results_manager.py` → Precision formatting, categorized headers
- `myra_app/index_engine.py` → Local breadth, YFinance fallback
- `myra_app/scanners/primitives.py` → SMC logic refinements
- `myra_app/strategies/surpriver_v2.py` → New anomaly detection strategy
- `myra_app/ml_engine.py` → AEON inference engine

---

## Technical Debt Addressed

- **God Class**: Librarian decomposed into 4 specialized modules
- **Root Pollution**: 60+ scripts moved to proper directories
- **Schema Locking**: Modular SQLite sidecars prevent conflicts
- **SQL Injection**: All queries parameterized
- **Concurrency**: Thread locks and retry mechanisms
- **Performance**: Covering indices and vectorized operations
- **External Dependencies**: PKScreener fully localized to myra_core

---

*Generated: 2026-05-01*
*Source: conductor/tracks/ markdown files*
