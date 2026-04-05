## 2024-04-05 - [Cache SQLite PRAGMA Metadata in Hot Paths]
**Learning:** Executing `PRAGMA table_info()` inside tight loops for mapping database row columns to dictionaries causes severe disk I/O bottlenecks.
**Action:** Cache the resulting column names in an instance-level variable upon the first call and reuse it for all subsequent calls within the object's lifecycle.
**Impact Score:** High
**Reliability:** Stable
