## 2024-04-05 - [Cache SQLite PRAGMA Metadata in Hot Paths]
**Learning:** Executing `PRAGMA table_info()` inside tight loops for mapping database row columns to dictionaries causes severe disk I/O bottlenecks.
**Action:** Cache the resulting column names in an instance-level variable upon the first call and reuse it for all subsequent calls within the object's lifecycle.

## 2025-04-04 - Vectorized Pandas Operations over iterrows
**Learning:** Pandas `.iterrows()` is a major bottleneck in inner scanner loops (hot paths) for data transformation.
**Action:** Replace `df.iterrows()` inside loops with vectorized Pandas functions such as `np.select()` and `np.where()` to build conditional columns, and `.set_index().to_dict('index')` to convert DataFrames to dictionaries efficiently.
**Impact Score:** High
**Reliability:** Stable
