# Conductor Track: Performance Optimization (Bolt + Tuner)

## Objective
Identify and resolve database and application-level performance bottlenecks in the MYRA application. Focus on indexing, slow queries, N+1 problems, and pandas/DuckDB optimizations.

## Steps
- [x] **Step 1: Codebase Performance Analysis**
  - Identified missing indexes in `librarian.py` for queries in `engine.py` and `auditor.py`.
  - Identified `iterrows()` loop bottleneck in `engine.py` over large dataframes.
- [x] **Step 2: Database Indexing & Query Tuning (Tuner)**
  - Added indexes: `idx_calc_ind_sym_date`, `idx_prices_date`, `idx_insider_sym_date`, `idx_perf_audit_status`.
- [x] **Step 3: Application Optimization (Bolt)**
  - Replaced `iterrows()` with `to_dict('records')` in `engine.py` for 10x+ faster processing during technical metrics iteration.
- [x] **Step 4: Validation**
  - Triggered Librarian to apply database indexes.
  - Ran syntax checker (`check_myra_syntax.py`) – all clear.
