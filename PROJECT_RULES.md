# 🚨 MYRA ENGINEERING RULES (MANDATORY) - v3.2

## ⚡ Core Philosophy
- **Performance > Readability > Convenience**: Quant engines must be fast.
- **Vectorization > Python loops**: Always prefer Pandas/Numpy vectorized operations.
- **Precompute > Recompute**: Cache indicators in the Parquet Lake.
- **Batch > Row-wise**: Never process data row-by-row if batching is possible.

---

## 🚫 BANNED PATTERNS (HARD FAIL)

### Pandas Anti-Patterns
- ❌ `iterrows()`: Extremely slow. Use `.itertuples()` or vectorization.
- ❌ `apply()` on large DataFrames: Hidden Python loop. Use vectorized maps.
- ❌ `.iloc`/`.loc` inside loops: High overhead. Filter first, then process.
- ❌ `DataFrame.append()` in loops: O(N²) complexity. Use `pd.concat([list_of_dfs])`.
- ❌ `.strftime()` for comparisons: Slow and error-prone. Use native `datetime64[ns]`.
- ❌ Chained indexing: `df[df['x'] > 0]['y']`. Use `df.loc[df['x'] > 0, 'y']`.

### Algorithmic & DB
- ❌ O(N²) loops without justification.
- ❌ Direct DB access in strategy layer: Use `DataAdapter`.
- ❌ Recomputing same indicator per row: Use the **Indicator Lake** (Parquet).
- ❌ Query inside loops: Batch your queries.

---

## ✅ REQUIRED PATTERNS (ATOMIC TRILOGY)

### Data Processing
- ✅ Use vectorized operations (`.isin`, `.map`, `.merge`).
- ✅ Use boolean masking for filtering.
- ✅ Use `groupby().agg()` instead of manual loops.
- ✅ OHLCV DataFrames MUST use `CamelCase` (`Open`, `High`, `Low`, `Close`, `Volume`).
- ✅ Indicator Lake outputs MUST use `lowercase_snake_case`.

### Date Handling
- ✅ All dates must be `datetime64[ns]`.
- ✅ Use direct datetime comparison (NO strings).

### Iteration (If Unavoidable)
- ✅ Use `itertuples(index=False)` if you MUST iterate.
- ✅ Limit iteration to the smallest possible filtered subset.

---

## 🏗️ ARCHITECTURE RULES
- **Librarian First**: Check `PKNSETools` and `PKDevTools` before adding new fetchers.
- **Sidecar Isolation**: The Atomic Trilogy architecture utilizes eight active databases mapped in LibrarianCore: `technical.db`, `meta.db`, `institutional.db`, `governance.db`, `valuation.db`, `scoring.db`, `calendar.db`, and `network_cache.sqlite`. Never add columns directly to SQL; use Parquet files in `data/indicators/`.
- **Rule 26**: Indicators MUST come strictly from Parquet via `precompute_indicators()`. Do not read technical indicators from SQLite.
- **Rule 43**: Thread-safe writes MUST use `with lib._db_lock:` and WAL mode.
- **SMC FVG**: The Smart Money Concepts (SMC) standardization rule is: detection threshold is 0.2%, and mitigation/invalidation threshold is 1.5%.
- **Materiality**: Institutional signals must respect the ₹10 Lakhs materiality filter.

---

## 🧪 TESTING & PR CRITERIA
- All transformations must be deterministic.
- PRs introducing loops MUST include a benchmark.
- PRs will be REJECTED if they contain `iterrows()`, `append()` in loops, or `strftime()` logic.
