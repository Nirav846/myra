---
name: myra-sandbox
description: Local execution of DuckDB SQL and math in a sandboxed environment. Use when implementing complex Smart Money Concepts (SMC), new quantitative metrics (VaR, MDD), or updating the Turbo-SQL engine in engine.py/librarian.py.
---

# MYRA Sandbox Skill

This skill provides a secure, local execution environment to test and validate complex mathematical and database logic before committing it to the main MYRA engine.

## Core Capabilities
- **DuckDB Validation**: Run raw SQL queries against sample data.
- **Math/Quant Verification**: Test algorithms for `engine.py` using NumPy/pandas/Polars.
- **SMC Simulation**: Prototype Smart Money Concept (SMC) logic (e.g., Relative Delivery Volume, Accumulation Detection) to ensure no data skew.

## Core Workflow: "Sample, Execute, Validate, Integrate"

### 1. Create Sample (Data Isolation)
Before running logic, isolate the data:
- Use `librarian.py`'s API to extract a sample (e.g., 10 stocks, 6 months of data) into a temporary in-memory DuckDB instance or a `.parquet` file.
- **MANDATE**: Never run untested SQL directly against `myra.db` in a production-like environment without a sandbox.

### 2. Execute Logic (Sandbox Run)
- Create a `sandbox_test.py` script.
- Implement the proposed SQL or math logic.
- Execute the script using `run_shell_command`.

### 3. Validate Results (The "Truth" Test)
Compare the output against expected values:
- Check for `NaN` or `None` values.
- Verify that performance metrics (e.g., Return on Assets) are within plausible ranges.
- Confirm that the SQL query performance is optimized (Turbo-SQL).

### 4. Integrate (The MYRA Context)
- Once the logic is proven, port it into:
    - `myra_app/engine.py`: For quantitative and indicator logic.
    - `myra_app/librarian.py`: For database-level abstractions.
    - `myra_app/fundamental_manager.py`: For factor scoring.

## Example Trigger Phrases
- "Test the new Relative Delivery Volume SQL query in the sandbox."
- "Verify the math for the Maximum Drawdown (MDD) calculation before adding it to engine.py."
- "Execute the 'Relative Strength (RS)' rating logic against sample DuckDB data."
- "Prototype the 'Volume Spread Analysis (VSA)' indicator in the sandbox."

## Guidelines
- **No Data Contamination**: Ensure the sandbox script does not write back to `myra.db`.
- **Performance Analysis**: Use DuckDB's `EXPLAIN ANALYZE` for complex queries.
- **English Only**: All test scripts, logs, and comments must be in English.
- **Resource Management**: Truncate output to prevent context window overflow while still showing sufficient validation data.
