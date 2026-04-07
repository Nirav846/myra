## 2024-04-05 - [Cache SQLite PRAGMA Metadata in Hot Paths]
**Learning:** Executing `PRAGMA table_info()` inside tight loops for mapping database row columns to dictionaries causes severe disk I/O bottlenecks.
**Action:** Cache the resulting column names in an instance-level variable upon the first call and reuse it for all subsequent calls within the object's lifecycle.

## 2025-04-04 - Vectorized Pandas Operations over iterrows
**Learning:** Pandas `.iterrows()` is a major bottleneck in inner scanner loops (hot paths) for data transformation.
**Action:** Replace `df.iterrows()` inside loops with vectorized Pandas functions such as `np.select()` and `np.where()` to build conditional columns, and `.set_index().to_dict('index')` to convert DataFrames to dictionaries efficiently.
**Impact Score:** High
**Reliability:** Stable

## 2026-04-05 - Avoid set() creation and iterrows() in Pandas loops
**Learning:** Re-creating a Python `set()` inside a Pandas `.iterrows()` loop causes a severe O(N^2) bottleneck. Additionally, `.iterrows()` is inefficient for large row counts.
**Action:** Hoist the `set()` creation outside the loop. Use `.dt.strftime()` combined with `.isin()` to vectorize the date filtering. Use `.itertuples(index=False)` for mapping rows into the required output tuple format instead of `.iterrows()`.
**Impact Score:** High
**Reliability:** Stable
