# MYRA Architectural Drift Audit Report

## 1. Executive Summary
This architectural drift audit assesses the MYRA (Myra Yield & Research Analytics) platform against its documented internal engineering standards (v3.0/v3.2 Atomic Trilogy). The findings indicate a system caught midway in a major architectural transition. While the intended design clearly mandates decentralized SQLite "sidecar" databases, strict use of Pandas/Numpy over loops, and the removal of deprecated tooling like DuckDB, the actual runtime still heavily relies on legacy components.

The most critical drifts are **Database Drift** (legacy monolithic `myra_market_data.db` usage instead of mapped sidecars) and **Tooling Drift** (widespread usage of deprecated DuckDB and undocumented usage of Polars). Additionally, there are notable **Code Standard Drifts**, including the usage of the strictly banned `.iterrows()` and dynamic string formatting for SQL table names.

Overall, the system health is severely degraded by these mismatches, leading to a high risk of "Silent Corruption" where legacy and modern pipelines unknowingly write to or read from diverging data stores.

---

## 2. Source of Truth Summary
Based on `PROJECT_RULES.md`, `README.md`, `PROJECT_SUMMARY.md`, and memory/logs:

1.  **Architecture (Atomic Trilogy v3.0/v3.2)**:
    *   System must use modular SQLite sidecars: `technical.db`, `institutional.db`, `valuation.db`, `meta.db`, etc. mapped via `LibrarianCore.DB_MAP`.
    *   Legacy monolithic DB (`myra_market_data.db`) is fully deprecated.
    *   Indicators must be isolated to a Parquet Lake (`data/indicators/`).
2.  **Tooling Requirements**:
    *   `duckdb` is officially deprecated in v3.0 in favor of SQLite sidecars.
    *   Data manipulation must rely on vectorised operations (Pandas, Numpy).
3.  **Data Format Standards**:
    *   OHLCV DataFrames MUST use `CamelCase` (`Open`, `High`, `Low`, `Close`, `Volume`).
    *   Indicator Lake outputs MUST use `lowercase_snake_case`.
4.  **Banned Anti-Patterns (Strict Rules)**:
    *   `iterrows()`, `apply()` on large datasets, and `DataFrame.append()` inside loops are strictly banned (Hard Fail).
    *   Dynamic string formatting for table names is banned; safe table operations must use a predefined dictionary mapping (`ALLOWED_QUERIES`) to prevent injection.

---

## 3. Actual System Behavior Summary
Tracing through the codebase reveals significant deviation from the declared rules:

1.  **Actual Databases in Use**:
    *   `myra_app/librarian_core.py` and numerous legacy tools/research scripts still hardcode connections to `results/Data/myra_market_data.db` or `db/myra_market_data.db`.
2.  **Tooling In Use**:
    *   `duckdb` is imported and heavily utilized across `myra_app/librarian_core.py`, `myra_app/gatekeeper.py`, `myra_app/feature_enrichment.py`, and nearly all `tools/` and `research/` scripts.
    *   `polars` is widely used in new ML and engine pipelines (e.g., `myra_app/feature_enrichment.py`, `myra_app/strategy_engine.py`) despite no formal declaration in the tech stack or rules.
3.  **Code Standards Violations**:
    *   `iterrows()` is still active in the main interface loop (`myra_app/tui_app.py`) and ingestion scripts (`test/ingest_all_offline.py`), despite being heavily flagged by `performance_guard.py`.
    *   `myra_app/feature_enrichment.py` ignores the `ALLOWED_QUERIES` dictionary mandate, using dynamic string injection (`f"SELECT * FROM {table_name}"`) and suppressing linter warnings with `# noqa: S608`.

---

## 4. Drift Report

### Drift Item 1: Legacy Database Monolith
- **Category:** Database Architecture
- **Expected:** All operations must route through Atomic Trilogy SQLite sidecars mapped via `LibrarianCore.DB_MAP` (e.g., `myra_technical.db`). Legacy monolithic databases are deprecated.
- **Actual:** `db/myra_market_data.db` and `results/Data/myra_market_data.db` are still hardcoded into fallback logic in `LibrarianCore` and across 15+ tool/research scripts.
- **Drift Type:** Deprecated but still used (Incomplete Refactor).
- **Severity:** HIGH
- **Confidence:** HIGH
- **Where observed:** `myra_app/librarian_core.py`, `myra_app/tuner.py`, `tools/migrate_duck_to_sqlite.py`, multiple `research/` scripts.

### Drift Item 2: Deprecated Tooling (DuckDB)
- **Category:** Tooling
- **Expected:** DuckDB is officially deprecated. SQLite is the designated backend.
- **Actual:** DuckDB remains imported and actively utilized as a core engine in `Gatekeeper`, `FeatureEnrichment`, `LibrarianCore`, and throughout the `tools/` directory.
- **Drift Type:** Deprecated but still used.
- **Severity:** HIGH
- **Confidence:** HIGH
- **Where observed:** `myra_app/gatekeeper.py`, `myra_app/feature_enrichment.py`, `myra_app/librarian_core.py`, `myra_app/tuner.py`, `test/temp_smc_test.py`.

### Drift Item 3: Undocumented Tooling (Polars)
- **Category:** Tooling / Pipeline
- **Expected:** Core tech stack specifies `pandas` and `numpy` for analytics and data manipulation.
- **Actual:** `polars` is heavily relied upon in core enrichment and strategy engine layers.
- **Drift Type:** Missing implementation (Documentation drift / Unapproved Tooling).
- **Severity:** MEDIUM
- **Confidence:** HIGH
- **Where observed:** `myra_app/feature_enrichment.py`, `myra_app/strategy_engine.py`, `myra_app/utils/feature_enricher.py`.

### Drift Item 4: Banned Anti-Pattern (.iterrows)
- **Category:** Code Standards / Performance
- **Expected:** `.iterrows()` is strictly banned and should cause a PR rejection.
- **Actual:** `.iterrows()` is actively used in UI data table population and offline ingestion tools.
- **Drift Type:** Contradictory implementation.
- **Severity:** MEDIUM
- **Confidence:** HIGH
- **Where observed:** `myra_app/tui_app.py`, `test/ingest_all_offline.py`.

### Drift Item 5: Security / Dynamic SQL Interpolation
- **Category:** Validation / Security
- **Expected:** Safe table operations must use a dictionary (`ALLOWED_QUERIES`) to prevent injection and avoid dynamic string formatting for table names.
- **Actual:** `process_enrichment_pipeline` uses direct string interpolation for table names and bypasses linter security rules with `# noqa: S608`.
- **Drift Type:** Contradictory implementation / Silent bypass.
- **Severity:** HIGH
- **Confidence:** HIGH
- **Where observed:** `myra_app/feature_enrichment.py`.

---

## 5. Root Cause Analysis

1.  **Incomplete Migration (CONFIRMED):** The transition from v2 to v3.2 (Atomic Trilogy) was never finished. Migration scripts (`tools/migrate_duck_to_sqlite.py`) exist but legacy hooks were left in `LibrarianCore` to keep old tests and research scripts functional.
2.  **Lack of Enforcement Mechanisms (LIKELY):** While `PROJECT_RULES.md` declares strict bans (e.g., `iterrows()`), the presence of `# noqa` comments and un-fixed violations implies CI/CD pipelines or pre-commit hooks are either bypassed or misconfigured.
3.  **Performance Trade-offs (LIKELY):** Polars was likely introduced to handle high-frequency/large dataframe operations where Pandas was too slow, but the architecture documentation was never updated to reflect this new standard.

---

## 6. Risk Assessment (Prioritized)

1.  **HIGH: Data Split-Brain (Silent Corruption)**
    *   *Cause:* Concurrent use of `myra_market_data.db` (via DuckDB) and SQLite Sidecars.
    *   *Impact:* Research models trained on DuckDB data will differ from production pipelines reading SQLite. Indicators will mismatch, leading to catastrophic financial signal failure.
2.  **HIGH: Security Vulnerabilities**
    *   *Cause:* `# noqa: S608` bypasses in `feature_enrichment.py`.
    *   *Impact:* Allows potential SQL injection if table variables are ever polluted.
3.  **MEDIUM: Maintainability & Dependency Bloat**
    *   *Cause:* Mixing Pandas, Polars, DuckDB, and SQLite in the same workflow.
    *   *Impact:* Vastly increases the cognitive load for engineers, inflates container sizes, and causes dependency conflicts.
4.  **LOW/MEDIUM: Performance Bottlenecks**
    *   *Cause:* Lingering `iterrows()` in TUI and ingestion.
    *   *Impact:* Blocks the UI thread or slows down batch processing, violating the O(N) scaling mandate.

---

## 7. Architectural Weak Points

*   **LibrarianCore Facade:** `LibrarianCore` is attempting to bridge two entirely different database paradigms simultaneously (DuckDB and SQLite). This module is highly fragile.
*   **Research & Tools Coupling:** The `research/` and `tools/` directories are deeply hardcoded to the legacy architecture, meaning any further core refactors will break all analytical capability.

---

## 8. Observability Gaps

*   **Silent Fallbacks:** When `LibrarianCore` fails to find a SQLite DB, it silently falls back to DuckDB logic without raising a critical alert.
*   **Missing Linter Enforcement:** The existence of `test/ingest_all_offline.py` with `iterrows` suggests the test suite is excluded from `performance_guard.py` checks.

---

## 9. Improvement Recommendations (Process & Enforcement)

1.  **Process:** Officially declare a "burn the boats" day. Remove the `duckdb` fallback logic from `LibrarianCore` entirely. Force all research scripts to fail, then port them to SQLite.
2.  **Validation:** Enforce `ruff` and `performance_guard.py` in the pre-commit hook with zero exemptions for `iterrows()`. Remove all `# noqa: S608` tags and implement the `ALLOWED_QUERIES` dictionary.
3.  **Documentation:** Update `PROJECT_RULES.md` and `README.md` to officially bless and govern the usage of `polars`.

---

## 10. Unknowns / Uncertain Areas

*   **Polars Migration Extent:** It is UNCLEAR if the intention is to completely replace Pandas with Polars, or if Polars is meant to be an isolated tool for specific ML pipelines.
*   **Parquet Lake Integrity:** While the Indicator Lake is documented, it is UNCLEAR if the legacy DuckDB pipelines are correctly writing to it, or if they are dumping data back into the monolithic DB.
