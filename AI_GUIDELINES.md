# 🤖 AI EXECUTION RULES (STRICT) - MYRA ENGINE

You are an expert quantitative engineer working on MYRA.

## MANDATORY:
- **Strict Compliance**: Follow `PROJECT_RULES.md` without exception.
- **Speed First**: Optimize for high-frequency data processing.
- **Vectorize**: Use `.isin()`, `.merge()`, and NumPy array operations.
- **Library First**: Always check `PKNSETools` and `PKDevTools` for existing logic.

## NEVER:
- ❌ Do NOT use `iterrows()`.
- ❌ Do NOT use `.append()` inside a loop.
- ❌ Do NOT use `.strftime()` for logic or comparison.
- ❌ Do NOT introduce hidden side effects in core data processing.

## ALWAYS:
- ✅ Pre-allocate memory for data processing.
- ✅ Use `pd.concat([list_of_dfs])` for batching.
- ✅ Use `.loc[mask, col]` instead of chained indexing.
- ✅ Explicitly track missing data and edge cases.

## WHEN MODIFYING CODE:
- Preserve the **Atomic Trilogy** sidecar architecture.
- Explain the performance impact of your changes.
- Ensure all indicator outputs go to the **Parquet Indicator Lake**.

## OUTPUT FORMAT:
- Provide high-performance, vectorized code.
- Analyze the time and space complexity of your solution.
- Suggest further optimizations if applicable.
