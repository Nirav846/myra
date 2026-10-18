# Phase 23 — F2 Recovery Preflight and Execution Manifest

**Baseline:** `adf4ba8` (Phase 22) · **Phase:** 23 · **Scope:** preparation and
verification only.

## Verdict: `READY FOR EXPLICIT OWNER AUTHORIZATION`

for **four** of the five affected columns, with one column (`delivery_ma_60`)
deliberately excluded because it cannot be validated. The write scope is exact,
hash-pinned, idempotent and transactional. **No production data was changed.**

**The recovery has NOT been executed.** It requires a separate, explicit owner
authorization, and the owner should read §4 and §7 before granting it.

---

## 1. The exact 12-session target set

| # | Session | Rows | `sma_50=close` | `high_52w=high` | `low_52w=low` | `delivery_ma_60=delivery_pct` | `sma_200` NULL |
|---|---|---|---|---|---|---|---|
| 1 | 2026-08-17 | 3165 | 3164 | 3165 | 3164 | 3165 | 3165 |
| 2 | 2026-08-20 | 3166 | 3166 | 3166 | 3166 | 3166 | 3166 |
| 3 | 2026-08-24 | 3211 | 3210 | 3211 | 3210 | 3211 | 3211 |
| 4 | 2026-08-25 | 3168 | 3168 | 3168 | 3168 | 3168 | 3168 |
| 5 | 2026-08-27 | 3166 | 3166 | 3166 | 3166 | 3166 | 3166 |
| 6 | 2026-08-31 | 3162 | 3161 | 3162 | 3161 | 3162 | 3162 |
| 7 | 2026-09-08 | 3202 | 3201 | 3202 | 3201 | 3202 | 3202 |
| 8 | 2026-09-11 | 3188 | 3187 | 3188 | 3187 | 3187 | 3188 |
| 9 | 2026-09-21 | 3232 | 3231 | 3232 | 3231 | 3231 | 3232 |
| 10 | 2026-09-28 | 3224 | 3223 | 3224 | 3223 | 3223 | 3224 |
| 11 | 2026-10-07 | 3224 | 3223 | 3224 | 3223 | 3223 | 3224 |
| 12 | 2026-10-13 | 3221 | 3220 | 3221 | 3220 | 3220 | 3221 |
| | **Total** | **38,329** | **38,320** | 38,329 | 38,316 | 38,325 | 38,329 |

Symbols in scope: **3,370**. No duplicate `(symbol, date)` rows exist in the
affected window (verified 0). 583 symbols have fewer than 252 sessions of
history by 2026-10-13; their 252-window columns fall back to `min_periods=1`,
which is the contract's warm-up rule, not a defect.

---

## 2. Evidence the defect still exists

Re-measured read-only against the live database and compared with the Phase 20
baseline:

| Metric | Phase 20 | Phase 23 | Result |
|---|---|---|---|
| total rows | 8,022,725 | 8,022,725 | unchanged |
| `sma_50 == close` | 40,431 | 40,431 | unchanged, still defective |
| `sma_200 IS NULL` | 7,965,142 (99.3%) | 7,965,142 | unchanged |

All four degenerate columns still co-occur on all 12 sessions. `myra_technical.db`
mtime is still `2026-10-17 11:55:59`.

---

## 3. Recomputed versus stored — and the validation that made it trustworthy

The first control attempt **failed**: on the clean session 2026-10-16, a naive
"recompute over full history" matched only 1 of 200 symbols. Rather than accept
it, the cause was traced:

- **Two producers write these same columns with different frames.**
  `eod2_sync._compute_derived` rolls over the symbol's history tail.
  `feature_enrichment` rolls over a frame loaded with
  `WHERE date >= date(latest, '-365 days')` (`feature_enrichment.py:202`) — about
  250 sessions, so its "52-week" is a *calendar* window, not 252 sessions.
- **`feature_enrichment` never writes `delivery_ma_60`** at all (the UPDATE at
  `:538-544` covers only the five SMAs and the two 52-week columns), which is why
  that column stays degenerate even on otherwise-clean sessions.

Each field was then validated against **the newest session where that field is
not degenerate**, over the whole table:

| Column | Reference session | Rule | Match | Mismatch | Verdict |
|---|---|---|---|---|---|
| `sma_50` | 2026-10-16 | 50-session rolling mean | **150/150** | 0 | validated |
| `sma_200` | 2026-10-16 | 200-session rolling mean | **143/150** | 7 | validated¹ |
| `high_52w` | 2026-04-07 | 252-session rolling max | **130/130**² | 0 | validated |
| `low_52w` | 2026-08-04 | 252-session rolling min | **124/124**² | 0 | validated |
| `delivery_ma_60` | **none exists** | — | — | — | **not recoverable** |

¹ The seven are symbols with fewer than 200 sessions inside the enrichment frame
but more in full history, where the two frames necessarily diverge. The
full-history rule is the one the contract states ("200-session close mean").

² Excluding rows whose stored value is NULL. The 365-calendar-day alternative
was tested and rejected: it missed 7–8 per 150 on both 52-week columns, while the
session-count rule produced **zero** false matches.

**`delivery_ma_60` is excluded from this recovery.** No session anywhere in the
table holds a non-degenerate value, so no rule can be checked against stored
output, and its only producer is best-effort. It is structurally excluded in
`myra_app/recovery_f2.py` (`EXCLUDED_COLUMNS`) and a test asserts it can never be
written by this path. It needs its own decision.

**Determinism:** recomputing twice produced an identical SHA-256
(`736f97c73a13cc7a…`).

---

## 4. Exact write scope

Generator: `python tools/f2_recovery_manifest.py` (read-only; it has no code path
that opens a writable connection).

| Column | Rows that would change | Of which NULL → value |
|---|---|---|
| `sma_50` | 38,164 | 0 |
| `sma_200` | **38,329** | **38,329** |
| `high_52w` | 36,395 | 0 |
| `low_52w` | 36,497 | 0 |
| **cell-level changes** | **149,385** | |
| **UPDATE statements** | **38,329** | |

The 165 rows not counted for `sma_50`, and ~1,930 each for the 52-week columns,
are rows where the stored value is **already correct** — legitimately, e.g. a
young listing whose short window's extreme *is* today's price. They are excluded
from the plan, so unaffected rows are never written.

**Drift detection.** The manifest carries a SHA-256 over the ordered
`symbol|date` keys of the target set:

```
target_sha256 = 5ad81709748c2641a3439a39a2669344046f4531bad6faaf1f82d80ee2643e74
```

Recompute it immediately before an authorised execution. **If it differs, the
live target set has moved and this plan is stale — stop and re-review.**

### Proposed execution command

The implementation supports a safely scoped, reviewable step. `apply_plan`
exists and is unit-tested, but **no CLI wires it to the live database in this
phase** — shipping an unauthorised production-write path would be the wrong
default. The authorised step would be, conceptually:

```sql
BEGIN IMMEDIATE;
-- the 38,329 statements emitted by:
--   python tools/f2_recovery_manifest.py --sql-out plan.sql
COMMIT;
```

**R0 — backup (prerequisite).** `myra_technical.db` is ~2.0 GB in **WAL** mode
(`journal_mode=wal`, `synchronous=2`, `page_size=4096`). A plain file copy is not
a safe backup of a WAL database: it can capture a torn state. The backup must use
`VACUUM INTO 'backup.db'` or the SQLite backup API, **with no writer running**
(the scheduler is currently down, which makes this the right moment), and the
backup must be checksum-verified and shown to open before the run proceeds.

---

## 5. Transaction, idempotency and failure behaviour

| Property | How it is satisfied | Evidence |
|---|---|---|
| Transaction boundary | One `BEGIN IMMEDIATE` … `COMMIT` for the whole run | `apply_plan` |
| Atomicity | Any exception triggers `rollback()`; an interrupted run leaves the pre-recovery state | test `test_a_failed_apply_rolls_back_completely` (injects a mid-batch constraint violation, asserts the table is byte-identical afterwards) |
| Idempotency | Only rows whose value actually differs are planned, so a rerun plans **zero** work | test `test_apply_plan_is_idempotent` |
| Duplicate prevention | Keyed on the existing `PRIMARY KEY(symbol, date)`; the rule collapses duplicate dates `keep="last"` | test `test_duplicate_and_unsorted_dates_are_normalised` |
| No fabricated values | The rule reads only raw `date/high/low/close`; a missing column raises rather than defaulting | tests `test_only_raw_columns_are_required`, `test_missing_columns_are_refused_rather_than_defaulted` |
| Unrelated rows preserved | UPDATE is keyed on `(symbol, date)`; a non-target symbol's full row is asserted unchanged | test `test_unrelated_rows_are_untouched` |
| Recovery on interruption | Restore the R0 backup, or simply re-run — the plan is idempotent | — |
| Determinism | Same inputs → same values, verified by repeat hashing | §3 |

### Postconditions to assert after an authorised run

1. `SELECT COUNT(*) FROM technical_data` is still **8,022,725**.
2. `sma_200 IS NULL` drops from 7,965,142 by exactly 38,329 → 7,926,813.
3. `sma_50 = close` drops from 40,431 by the planned count for `sma_50`.
4. No session in the 12 has `high_52w = high` or `low_52w = low` above the
   legitimate young-listing count.
5. Re-running the manifest yields `planned_statements = 0`.

### Failure conditions — abort if any occurs

- The manifest `target_sha256` differs from the value recorded here.
- The R0 backup fails to open or its checksum does not match.
- Any other process holds a write lock on `technical_data` (the scheduler must be
  confirmed stopped, not merely absent).
- The row count changes between backup and execution.
- `technical_data`'s schema differs from what this phase inspected.

---

## 6. Tests

`tests/test_phase23_f2_recovery.py` — **23 tests**, synthetic series and temporary
SQLite files only. No live market data, no network; `live_db_guard` and the
schema-drift guard untouched.

Rule correctness (trailing-window means, 52-week max/min, warm-up on short
series, determinism, duplicate/unsorted dates, missing columns refused, unparseable
dates rejected) · write-scope containment (`delivery_ma_60` excluded by
construction, only four columns in scope, exactly the 12 sessions) · manifest
properties (hash stable and order-independent, hash changes when the target set
changes, a stale manifest plans nothing) · application (repairs the degenerate
shape, idempotent, rolls back completely on failure, leaves unrelated rows
untouched, empty plan writes nothing) · Phase 22 semantics preserved (raw
adjusted columns only; no output is ever an input to its own recovery).

**Results:** focused 23 passed under `-W error::FutureWarning`; **full suite
1820 passed** (was 1797); `black` (pinned 26.10.1), `flake8`, MYRA Performance
Guard and schema-drift guard all pass.

---

## 7. Remaining risks

1. **`delivery_ma_60` stays broken.** Excluded by necessity. Its only producer is
   best-effort and nothing validates it. Needs a product decision — this is not a
   defect the recovery can honestly fix.
2. **`sma_200` for the other 99.3% of history stays NULL.** Out of scope by
   instruction and by prudence: a ~8M-row backfill deserves its own authorization.
3. **The 9 weekend-dated sessions are untouched**, so windows spanning them
   (2026-02-01 is inside every recent 200-session window) still include a phantom
   session. This recovery does **not** fix that and cannot.
4. **Rule/producer divergence remains.** `eod2_sync` (252 sessions) and
   `feature_enrichment` (365 calendar days) still compute `high_52w`/`low_52w`
   differently. This recovery writes the validated session-count rule, so the
   divergence persists until one producer is aligned — worth a follow-up.
5. **7 `sma_200` symbols** will receive the contract's full-history mean rather
   than the enrichment frame's truncated mean. This is deliberate and documented.

---

## 8. Confirmation that production data was not changed

- Every live connection in this phase used
  `sqlite3.connect("file:…?mode=ro", uri=True)`; `tools/f2_recovery_manifest.py`
  has no writable code path.
- `myra_technical.db` mtime unchanged: `2026-10-17 11:55:59`.
- Aggregate re-verification: 8,022,725 rows / 40,431 degenerate / 7,965,142 NULL
  — identical to the Phase 20 baseline.
- No backfill, ingestion, bulk refresh, migration, indicator-lake build or
  scheduler operation. Lake still **0 files**; OHLCV cache still 3,320 files at
  2026-05-02; scheduler down; `MYRA_V25_RANKING_INPUTS` disabled; TriggerScanner
  operational; no V2.5 score published.

The 38,329-row plan was **generated and inspected only**. It was not applied.

---

## 9. What the owner is being asked to authorize

Nothing has been executed. Authorization would mean: take the R0 backup, then
apply the hash-pinned 38,329-row plan to `sma_50`, `sma_200`, `high_52w` and
`low_52w` for the 12 named sessions, asserting the postconditions in §5 — and
accepting that `delivery_ma_60`, the historical `sma_200` NULLs and the weekend
sessions would remain untouched.