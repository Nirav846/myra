# Specification: Librarian Modularization v3.0 (Atomic Trilogy)

## Objective
Decouple MYRA from the monolithic DuckDB (`myra_market_data.db`) by migrating data to specialized SQLite "Sidecars" and moving high-frequency technical indicators to an append-only Parquet Lake.

## 1. Modular Database Schema
The single DuckDB file will be replaced by the following SQLite databases to prevent schema locking and facilitate future growth:

### A. `db/technical.db` (EXISTING)
- **Table:** `technical_data`
- **Fields:** OHLCV + Delivery + Trades + VWAP.
- **Role:** High-fidelity price history from Bhavcopies.

### B. `db/institutional.db` (NEW)
- **Table:** `insider_trades` (Symbol, Name, Category, Type, Mode, Value, Date)
- **Table:** `large_deals` (Symbol, Type, Client, BuySell, Qty, Price, Date)
- **Role:** Smart Money tracking.

### C. `db/meta.db` (NEW)
- **Table:** `symbols_master` (Symbol, FirstSeen, LastSeen, InActiveUniverse, InNifty500, Sector)
- **Table:** `index_constituents` (IndexName, Symbol)
- **Table:** `benchmarks` (Symbol, Date, Close)
- **Role:** The "System Brain."

### D. `db/valuation.db` (NEW)
- **Table:** `fundamentals` (Symbol, PE, ROE, EPS, BookValue, MCap, Sector, LastUpdated)
- **Table:** `quarterly_results` (Symbol, Date, Revenue, NetProfit, EPS, etc.)
- **Role:** Long-term value metrics.

## 2. The Indicator Lake Pattern (Solving "Error Loops")
Instead of modifying the SQL schema when a new scanner or strategy is added, MYRA will use **Strategy-Isolated Parquet Files**:

- **Location:** `data/indicators/<strategy_id>/<symbol>.parquet`
- **Format:** Time-indexed (Date, Indicator_1, Indicator_2, ...)
- **Why:** This allows `Strategy A` and `Strategy B` to have completely different column requirements without ever conflicting or needing a DB migration.

## 3. Librarian Refactoring (The Virtual DB)
The `Librarian` class will be refactored into a **Facade** that routes queries to the correct SQLite sidecar or Parquet file transparently.
- `lib.get_ohlcv()` -> `technical.db`
- `lib.get_insider()` -> `institutional.db`
- `lib.get_active_universe()` -> `meta.db`

## 4. Error-Free Migration Protocol
1. **Extraction:** Read data from DuckDB in chunks.
2. **Sanitization:** Clean `NAType`, `NaN`, and `None` for SQLite compatibility.
3. **Insertion:** Batch insert with `INSERT OR IGNORE` to prevent duplicates.
4. **Validation:** Cross-check row counts between DuckDB and the new SQLite files.
