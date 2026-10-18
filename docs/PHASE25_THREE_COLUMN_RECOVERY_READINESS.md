# Phase 25 — Three-Column Recovery Manifest and Write-Readiness Gate

**Baseline:** `17fd65a` (Phase 24) · **Phase:** 25 · **Scope:** Option B
preparation only.

## Verdict: `READY FOR EXPLICIT PRODUCTION WRITE AUTHORIZATION`

**Production recovery was NOT executed. Production data is unchanged.**

---

## A. Verified baseline

| Check | Result |
|---|---|
| HEAD / branch / tree | `17fd65a`, `main`, clean, in sync with `origin/main` |
| Production DB path (from config, not filename) | `DB_DIR` = `myra_app/db` → `LibrarianCore.DB_MAP["technical"]` → `myra_app\db\myra_technical.db` |
| `PRAGMA integrity_check` | **ok** |
| `PRAGMA quick_check` | **ok** |
| `PRAGMA foreign_key_check` | no violations |
| `journal_mode` | **wal** |
| Row count | **8,022,725** |
| `sma_50 = close` | **40,431** |
| `sma_200 IS NULL` | **7,965,142** |
| **Baseline scope digest** | `340843ded89247aa3f825cbefe64081a843d505ef201f165e32d0402fd6ba66a` |
| Lake | `data/indicators/precomputed` — **0 files** |
| Scheduler | **no lock**, not started, not started by this phase |

The baseline digest is a deterministic SHA-256 over the ordered
`symbol|date|sma_50|high_52w|low_52w` of all 38,329 target rows — not a file
timestamp.

---

## B. The new three-column manifest

Generated read-only by `tools/f2_recovery_plan_optionb.py`.

| Field | Value |
|---|---|
| **Content SHA-256** | **`c566fad52e8234c81ebcff55ad36d0490d3436f095d8ca3fb1d90add9566f9bb`** |
| Option | B |
| Sessions | 12 (2026-08-17 … 2026-10-13) |
| Write columns | `sma_50`, `high_52w`, `low_52w` |
| Deferred columns | `sma_200`, `delivery_ma_60` |
| Target rows | **38,173** (of 38,329 session rows; 156 already correct) |
| Cell changes | **111,056** — 111,056 value→value, **0 null→value** |
| Unchanged cells (excluded) | 3,931 |
| Baseline scope digest | `340843de…` (embedded in the payload) |
| Calculation-code fingerprint | `2fa01abdf3ca3290a2052b569a374210dcd20185db1d0a8d63a42e1aece94439` |
| Tool version | `phase25-optionB-v1` |
| Read-only columns | symbol, date, close, high, low, delivery, volume, delivery_pct |

**The four-column Phase 23 hash (`5ad81709…`) is not used and cannot authorise
this.** It is a different hash over a different write set, and
`validate_plan(..., expected_columns=<four columns>)` raises `ScopeError`.

The manifest records the calculation rule (window sizes 50/252/252, aggregators
mean/max/min, `min_periods=1`, session-count windows) and the execution contract
(ascending per-symbol ordering, duplicate dates collapsed `keep=last`, warm-up
unchanged, adjusted price basis, no fabricated values). No new minimum-history
rule was introduced and no calendar-day window was substituted.

### Two defects found and fixed while building this

1. **The content hash ignored the write rows.** It covered only the payload, so
   altering a symbol, date or target value did not change it — defeating the
   drift guard entirely. `content_hash` now folds in every row; three tests
   (`test_changing_a_target_value/symbol/date_changes_the_hash`) cover it.
2. **The SQL renderer emitted malformed SET clauses**
   (`SET sma_50, high_52w, low_52w = ...`). Found by the independent validator,
   fixed, and the generated artifact now has exactly one SET shape.

Both changed the hash, so the manifest was regenerated after each fix. It is
stable across repeat generation.

---

## C. Independent calculation validation

The rolling maths was **re-implemented from scratch** (explicit loops over raw
SQL, not `recovery_f2.recompute_frame`) and compared against the plan rows parsed
back out of the generated SQL artifact.

| Column | Agree | Disagree | Max absolute difference |
|---|---|---|---|
| `sma_50` | **38,164** | **0** | 0.0000000000 |
| `high_52w` | **38,164** | **0** | 0.0000000000 |
| `low_52w` | **38,164** | **0** | 0.0000000000 |

9 plan rows could not be independently checked because those symbols have fewer
than 5 sessions of history inside the bounded window.

Also verified:

- **2026-02-01 intact** — 2,791 rows, the declared NSE live trading session,
  untouched.
- **The eight unexplained weekend sessions untouched** — 2025-02-01 (2,548),
  2024-05-18 (2,276), 2024-03-02 (2,200), 2024-01-20 (2,244), 2023-11-12 (2,171),
  2016-10-30 (1,222), 2015-02-28 (1,079), 2014-03-22 (1,005). None is a target
  session, so none is in the write set. They are preserved, not removed.
- **Deferred columns never assigned** — the SQL contains 38,173 UPDATEs with a
  single SET shape: `sma_50 = …, high_52w = …, low_52w = …`. `sma_200` and
  `delivery_ma_60` appear only in a comment naming them as excluded.
- **Transaction framing** — `BEGIN IMMEDIATE;` … `COMMIT;`.

Canonical price basis: the plan reads only `technical_data` raw columns; the
unadjusted OHLCV cache is never read.

---

## D. Backup, transaction and drift-guard rehearsal (isolated temp DB)

`recovery_f2.apply_write_plan` against a temporary WAL database: **21 checks
passed, 0 failed.**

| Check | Result |
|---|---|
| `VACUUM INTO` backup created | PASS |
| Backup `integrity_check` | PASS |
| Backup content digest == source | PASS |
| Scope is exactly Option B | PASS |
| No deferred column in write set | PASS |
| Rows written | PASS (36/36) |
| `sma_200` / `delivery_ma_60` / row count unchanged | PASS |
| Schema unchanged | PASS |
| Rows outside the 12 sessions untouched | PASS |
| Every planned row holds the planned value | PASS (0 mismatches) |
| Idempotency — rerun plans nothing, writes 0 | PASS |
| Partial match refused + table byte-identical | PASS |
| Constraint violation raises + table byte-identical | PASS |
| Stale digest refused | PASS |
| Four-column expectation refused | PASS |
| Tampered column list refused | PASS |
| Write refused while another writer holds the lock | PASS (`OperationalError`) |

### A third defect the rehearsal exposed

Injecting one malformed `(symbol, date)` key made that statement match **zero
rows without raising**, so the batch **committed the other 35 rows**. SQLite does
not error on a non-matching UPDATE. `apply_write_plan` now compares
`cur.rowcount` against the plan size and raises `ScopeError` on a partial
application, aborting the transaction. This is a genuine hardening that would
otherwise have allowed a silently partial recovery.

Note also reproduced here: copying a WAL database's main file without a
checkpoint yields a database with **no tables**. `VACUUM INTO` is mandatory.

---

## E. Execution prerequisites

1. **R0 backup** — `VACUUM INTO` (never a file copy). Verify
   `PRAGMA integrity_check = ok` and compare a content digest to the live
   database before proceeding.
2. **No concurrent writer** — confirm the scheduler is stopped and no ingestion
   holds the lock; `BEGIN IMMEDIATE` then blocks any writer mid-run.
3. **Hash gate** — regenerate the plan immediately before execution; abort if
   `content_sha256 != c566fad52e8234c81ebcff55ad36d0490d3436f095d8ca3fb1d90add9566f9bb`,
   if `baseline_scope_digest != 340843de…`, or if `code_sha256 != 2fa01abd…`.
   Editing `recovery_f2.py` changes the code fingerprint and invalidates the
   plan by design.
4. **Single transaction** — `BEGIN IMMEDIATE` … `COMMIT`, with rollback on any
   exception or partial match.
5. **Post-conditions to assert** — total rows still 8,022,725;
   `sma_200 IS NULL` still 7,965,142 (unchanged by this operation);
   `delivery_ma_60` still degenerate on 4,583,005 rows; rows written = 38,173;
   regenerating the plan yields 0 rows.

---

## F. Outstanding issues — tracked, not expanded into this scope

| Issue | Status |
|---|---|
| Eight unexplained weekend sessions | Open. Not deleted, not rewritten. Four EOD2-owner questions in the Phase 24 report §B. |
| `sma_200` for symbols with <200 sessions | Deferred. 6,299/40,332 pairs (15.62%) lack a full 200-session window; the two producers disagree. Needs a product decision. |
| Historical `sma_200` (~99.3% NULL) | Out of scope. Separate authorization required. |
| `delivery_ma_60` producer | No trustworthy producer and no non-degenerate reference value anywhere. Not written by this operation. |
| 52-week semantics divergence | `eod2_sync` uses 252 sessions, `feature_enrichment` uses a 365-calendar-day frame. This plan uses the validated session-count rule; the producers still disagree. |
| Indicator lake | Empty (0 files). Not built. |
| `MYRA_V25_RANKING_INPUTS` | Disabled. |
| V2.5 score publication | Blocked. Not published. |

---

## G. Prohibited operations — confirmation

- Every live connection used `sqlite3.connect("file:…?mode=ro", uri=True)`.
- `tools/f2_recovery_plan_optionb.py` has **no writable code path**; the
  Phase 23 four-column tool is untouched and still generates its own plan.
- **No recovery executed.** No backup of production was created (the backup
  rehearsal used a temporary directory).
- No ingestion, EOD2 sync, bulk refresh, backfill, migration or lake build. No
  scheduler start or task ownership. No scoring/threshold/formula change, no
  ranking-input flag, no V2.5 score published, no score-blocking weakened.
- The eight unexplained weekend sessions and the 2026-02-01 special session
  were neither deleted, excluded nor rewritten.
- Live-database guards, test isolation and schema checks unchanged.

---

## H. The proposed production operation (NOT RUN)

**What it would do, in order:**

1. Open `myra_app/db/myra_technical.db` read-only; recompute the baseline scope
   digest and abort unless it equals `340843de…`.
2. Regenerate the plan; abort unless `content_sha256` equals `c566fad5…` and
   `code_sha256` equals `2fa01abd…`.
3. `VACUUM INTO` a backup file; verify `integrity_check` and its digest.
4. Confirm no other writer holds the lock.
5. Re-validate the live digest, then open one write connection and apply
   `recovery_f2.apply_write_plan(conn, plan, live_scope_digest=...)` inside a
   single `BEGIN IMMEDIATE` transaction — 38,173 UPDATEs touching only
   `sma_50`, `high_52w`, `low_52w`.
6. Assert the post-conditions in §E.4.

**Entry point:** `myra_app.recovery_f2.apply_write_plan` — the only function
that can write, and it refuses unless validation passes.

**Risks:** the operation is irreversible without restoring the backup; a
concurrent writer would be blocked rather than merged; if the eight unexplained
weekend sessions are later removed, a small number of `high_52w`/`low_52w`
values (Phase 24: 18 and 26 rows) would need a minor adjustment; and the plan is
void if `recovery_f2.py` changes before execution.

**Not run here.** This phase produced and validated the plan only.