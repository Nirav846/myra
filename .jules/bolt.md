## 2025-04-04 - Vectorized Pandas Operations over iterrows
**Learning:** Pandas `.iterrows()` is a major bottleneck in inner scanner loops (hot paths) for data transformation.
**Action:** Replace `df.iterrows()` inside loops with vectorized Pandas functions such as `np.where()` to build conditional columns, and `.set_index().to_dict('index')` to convert DataFrames to dictionaries efficiently.
**Impact Score:** High
**Reliability:** Stable
