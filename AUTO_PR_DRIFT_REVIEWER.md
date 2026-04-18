# MYRA PR Drift Audit Report

## 1. Summary of PR Intent
This Pull Request introduces several new standalone scripts and unit tests to validate system functionality. Specifically, it adds:
- `tools/tuner.py`: A data health metrics tool for scanning database price and indicator counts.
- `tools/validate_librarian.py`: A script verifying the Librarian's data retrieval methods across Meta, Technical, Institutional, and Valuation databases.
- `verify_fetch.py`: A verification script for testing data fetchers (insider trades and Bhavcopy).
- `verify_mto_fix.py` & `verify_mto_fix_v2.py`: Unit tests validating the robust MTO parsing logic against ragged CSV files.

## 2. High-Level Impact
- **Modules Affected:** Tooling (`tools/`), Validation / Testing (`verify_*.py`).
- **Type of Change:** Tooling and validation layer additions.
- **Impact Summary:** While primarily intended for diagnostics and testing, these files introduce severe architectural drift by reviving deprecated tooling, querying non-standard database structures, bypassing required API time validations, and using prohibited test-mocking patterns.

## 3. Issues Found

### 🔴 Issue
- **Category:** Tooling
- **What changed:** Introduction of `tools/tuner.py`.
- **Expected (rule):** DuckDB is officially deprecated in the current stack.
- **Actual (in PR):** The tool explicitly relies on `import duckdb` to establish connections.
- **Problem:** Re-introduces deprecated tooling, violating the repository's tooling constraints and complicating the environment.
- **Severity:** HIGH
- **Confidence:** HIGH

### 🔴 Issue
- **Category:** Database / Architecture
- **What changed:** Database connection target in `tools/tuner.py`.
- **Expected (rule):** Database access must strictly follow standardized naming conventions (e.g., `technical.db`, `valuation.db`) and use standard schemas (e.g., `technical_data`).
- **Actual (in PR):** Hardcodes connection to `results/Data/myra_market_data.db` and queries non-standard tables `prices` and `calculated_indicators`.
- **Problem:** Creates a split-brain storage scenario and data format drift, pointing to a legacy or phantom database instead of the source of truth.
- **Severity:** HIGH
- **Confidence:** HIGH

### 🔴 Issue
- **Category:** Architecture
- **What changed:** Data retrieval methodology in `tools/validate_librarian.py`.
- **Expected (rule):** Data components must bypass legacy librarian hooks and connect directly to local SQLite databases using `LibrarianCore.DB_MAP` to comply with the v3.2 standard.
- **Actual (in PR):** Uses deprecated legacy accessors `lib.get_ohlcv()` and `lib.get_fundamentals()`.
- **Problem:** Incomplete transition to the v3.2 direct-connect standard (Migration Drift).
- **Severity:** MEDIUM
- **Confidence:** HIGH

### 🔴 Issue
- **Category:** Validation / Testing
- **What changed:** Mocking strategy in `verify_mto_fix.py` and `verify_mto_fix_v2.py`.
- **Expected (rule):** Test suites must isolate SQLite databases by patching the dictionary directly: `unittest.mock.patch.dict(Librarian.DB_MAP, {'db_key': 'test_db.db'})`.
- **Actual (in PR):** Directly overwrites the class property via `with unittest.mock.patch('myra_app.librarian_core.LibrarianCore') as mock_lib; mock_lib.DB_MAP = ...`.
- **Problem:** Reassigning the core class's internal connection logic is an anti-pattern that creates brittle tests and potential testing state bleed.
- **Severity:** MEDIUM
- **Confidence:** HIGH

### 🔴 Issue
- **Category:** Data Flow
- **What changed:** Logic in `verify_fetch.py`.
- **Expected (rule):** NSE daily data is not published before 6:30 PM IST. Fetch scripts must explicitly verify the time is past 18:30 IST before fetching current-day Bhavcopy.
- **Actual (in PR):** Initiates `fetch_bhavcopy_with_retry(dt)` directly without verifying publication constraints.
- **Problem:** If run during market hours, the fetcher will trigger 404s or process stale data, causing unnecessary load or invalid state generation.
- **Severity:** MEDIUM
- **Confidence:** HIGH

### 🔴 Issue
- **Category:** Code Health
- **What changed:** Import statements in `tools/validate_librarian.py`.
- **Expected (rule):** Unused imports fail `ruff` F401 configuration (which is explicitly unfixable).
- **Actual (in PR):** Includes `import pandas as pd` without ever utilizing it.
- **Problem:** Violates strict static analysis and code health standards.
- **Severity:** LOW
- **Confidence:** HIGH

## 4. Risk Assessment
- **Split-Brain Databases:** Targeting `myra_market_data.db` introduces a severe risk of analyzing outdated or disconnected datasets, leading to completely invalid metrics or false signals.
- **Silent Failures:** The lack of strict time checks for NSE data retrieval (18:30 IST) creates a risk of silent failures or processing of the previous day's incomplete data under the guise of current-day execution.
- **Testing Blindspots:** Brittle database mocking patterns conceal potential schema initialization errors that would otherwise be caught during test execution.

## 5. Architectural Consistency Verdict
❌ **Significant Drift**

**Classification:**
- **Tooling Drift** (Reintroduction of deprecated DuckDB)
- **Data Contract Drift** (Inconsistent database targets and table schemas)
- **Migration Drift** (Reliance on deprecated Librarian v2.x legacy hooks)
- **Validation Drift** (Non-compliant test mocking patterns)