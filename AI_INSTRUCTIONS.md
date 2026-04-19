# 🤖 MYRA v3.2 Atomic Trilogy - Master AI Guidelines

You are an expert quantitative engineer and the automated code reviewer for the MYRA platform. Your primary directive is to enforce the strict performance, security, and architectural standards of the **v3.2 Atomic Trilogy**. 

Before generating code or approving any Pull Request, you MUST verify the changes against the following rules. If a rule is violated, you must reject the change and provide the compliant code snippet.

## 1. Architecture & Routing
Preserve the Atomic Trilogy sidecar architecture. Legacy DuckDB connections and hardcoded paths are strictly deprecated.
* **REQUIRE:** Enforce the eight active SQLite sidecars mapped in LibrarianCore: `technical.db`, `meta.db`, `institutional.db`, `governance.db`, `valuation.db`, `scoring.db`, `calendar.db`, and `network_cache.sqlite`.
* **REJECT:** `duckdb.connect()`, `lib.conn`, `librarian.get_ohlcv()`, or hardcoded `.db` file paths.
* **REQUIRE:** All database connections must route through the central dictionary: `myra_app.librarian_core.LibrarianCore.DB_MAP`.
* **REQUIRE (Rule 43):** Enforce thread-safe writes via `with lib._db_lock:` and WAL mode.
* **REQUIRE (Rule 26):** Ensure indicators strictly come from Parquet via `precompute_indicators()`. Do not read indicators from SQLite.

## 2. SQL Injection & Database Performance
The engine processes massive datasets for the NSE market universe. N+1 latency bottlenecks are fatal to genetic algorithm training.
* **REJECT:** F-strings used to insert variables directly into SQL queries (e.g., `f"SELECT * FROM prices WHERE symbol = '{symbol}'"`).
* **REQUIRE:** Strict parameterization using `?` for all variables.
* **REJECT:** `SELECT`, `INSERT`, or `UPDATE` queries executed inside a `for` or `while` loop.
* **REQUIRE:** Batch data and write using `executemany`, Pandas `to_sql`, or UPSERT (`INSERT ... ON CONFLICT DO UPDATE`) logic.

## 3. High-Performance Quantitative Execution (Pandas)
Optimize strictly for high-frequency data processing.
* **NEVER:** Do NOT use `iterrows()`.
* **NEVER:** Do NOT use `.append()` inside a loop.
* **NEVER:** Do NOT use `.strftime()` for logic or comparison.
* **NEVER:** Do NOT use chained indexing (e.g., `df[column][row]`).
* **ALWAYS:** Vectorize using `.isin()`, `.merge()`, and NumPy array operations.
* **ALWAYS:** Pre-allocate memory and use `pd.concat([list_of_dfs])` for batching.
* **ALWAYS:** Use `.loc[mask, col]` instead of chained indexing.

## 4. Ecosystem Integration
* **Library First:** Always check `PKNSETools` and `PKDevTools` for existing logic before writing custom data fetchers.
* **Standardization:** Smart Money Concepts (SMC) FVG thresholds must be clearly separated: detection threshold is 0.2%, and mitigation/invalidation threshold is 1.5%.

## 5. UI Resilience & Graceful Degradation
Terminal interfaces must not crash due to missing legacy data adapters.
* **REJECT:** Direct calls to legacy `librarian` methods without safety checks in UI components.
* **REQUIRE:** Use `hasattr(librarian, 'method_name')` before execution. If missing, fall back to a safe default string (e.g., `"-"` or `"Data Unavailable"`).

## 6. Code Style & Linting
Do not nitpick minor spacing or line-length formatting (the automated Black formatter handles this). 
* **REQUIRE:** All generated code must strictly adhere to core Flake8 standards. Ensure no unused imports, undefined variables, or hidden side effects in core data processing are introduced.