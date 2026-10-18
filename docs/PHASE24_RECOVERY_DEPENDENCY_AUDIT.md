# Phase 24 — Recovery Dependency Audit and Execution-Order Decision

**Baseline:** `d6b3024` (Phase 23) · **Phase:** 24 · **Nature:** read-only
investigation and decision.

## Verdict: `READY FOR NARROWLY SCOPED OWNER AUTHORIZATION`

**Recover `sma_50`, `high_52w` and `low_52w` for the 12 sessions. Defer
`sma_200`.** Nothing was executed. **No production data was changed.**

---

## A. Verified current state

Repository, CI and data were checked against the running system — content-level,
not by file timestamp alone.

| Check | Result |
|---|---|
| HEAD / branch / tree | `d6b3024`, `main`, **clean**, in sync with `origin/main` |
| CI / CodeQL | `39107898341` success · `39107898757` success |
| `technical_data` rows | **8,022,725** — matches Phase 23 |
| `sma_50 = close` | **40,431** — matches |
| `sma_200 IS NULL` | **7,965,142** — matches |
| Target-session value digest | `fc6bfcdb5626abaf…` over all 38,329 target rows |
| **Manifest hash** | `5ad81709748c2641a3439a39a2669344046f4531bad6faaf1f82d80ee2643e74` — **identical to Phase 23; zero drift** |
| Planned statements | 38,329 (unchanged) |

No drift. The Phase 23 plan is still valid as a *description*; only its column
scope changes (§E).

---

## B. Weekend-session provenance

**This overturns a Phase 20/21 finding.**

`technical_data` holds 7,862 distinct dates, 37 of them weekend-dated (31
Saturday, 6 Sunday). Nine are universe-wide:

| Date | Weekday | Rows | Also in the eod2 source CSV? | Declared special session? |
|---|---|---|---|---|
| **2026-02-01** | **Sunday** | 2,791 | yes | **YES** |
| 2025-02-01 | Saturday | 2,548 | yes | no |
| 2024-05-18 | Saturday | 2,276 | yes | no |
| 2024-03-02 | Saturday | 2,200 | yes | no |
| 2024-01-20 | Saturday | 2,244 | yes | no |
| 2023-11-12 | Sunday | 2,171 | yes | no |
| 2016-10-30 | Sunday | 1,222 | yes | no |
| 2015-02-28 | Saturday | 1,079 | yes | no |
| 2014-03-22 | Saturday | 1,005 | yes | no |

**2026-02-01 is a legitimate NSE live trading session.** eod2's
`meta.json` records `"special_sessions": ["2026-02-01T00:00:00"]`, populated by
`check_special_sessions()` (`eod2/src/defs/defs.py:699-757`), which scans NSE
circulars and records any whose subject contains "**live trading session**"
(`:730`). NSE traded on that Sunday. The row is real market data, correctly
dated — and it is the one date Phase 20 called a "phantom" and Phase 21 asked
the source owner to resolve.

Consequences: **2026-02-01 must never be deleted**, and a test now pins that.
Phase 20's claim that it duplicated the database's 2026-01-02 close is not
supported by the current data.

**For the other eight**, the evidence is:

- All are present in the eod2 source CSVs, with distinct, plausible prices —
  so they are **not** artefacts of MYRA ingestion.
- **No legitimate trading session is missing.** For every one, the preceding
  Friday and the following Monday both exist. Every absent neighbouring date is
  itself a weekend day. So these are *extra* rows, not mislabelled ones.
- They are not duplicates of the prior session (0–9 identical rows out of
  1,005–2,548).
- Nothing in `meta.json` mentions them: not as special sessions, not as
  holidays, not in `equityActions`.

They are therefore unexplained **extra sessions** — not shifted, not copied.
Their origin cannot be settled from this repository.

### Questions for the eod2 source owner

1. For each of the eight dates, was NSE actually open? If not, which pipeline
   step emitted the row?
2. Do these rows correspond to real trades, or are they interpolated?
3. Should they be removed at source, or relabelled to their true session date?
4. Was `special_sessions` consulted when these rows were written? If it is
   authoritative, the eight should have been rejected.

---

## C. Rolling-window impact

Window definitions were traced to the implementations. All four are
**session-count** rolling windows over the row sequence (Phase 23's validated
rule), *not* calendar-day windows — except `feature_enrichment`, which loads a
365-calendar-day frame and therefore computes ~250-session equivalents.

Recomputing all 40,332 (symbol, session) pairs twice — variant **A** with the
weekend rows present, variant **B** with the eight *unexplained* weekend rows
removed (2026-02-01 correctly kept, being legitimate):

| Column | Pairs differing | % | Max absolute difference | Representative example |
|---|---|---|---|---|
| `sma_50` | 6 / 37,946 | 0.016% | **0.0170** | MBECL 2026-09-21: 90.3116 → 90.3286 |
| `sma_200` | 126 / 37,946 | 0.332% | **3.6345** | (see §D) |
| `high_52w` | 18 / 37,946 | 0.047% | **17.05** | KKVAPOW_SME `low_52w`: 259.65 → 247.30 |
| `low_52w` | 26 / 37,946 | 0.069% | **12.35** | as above |

Every one of the 12 sessions is touched by at least one column; per-session
counts are in the Phase 24 evidence run. The weekend dates inside the 252-row
windows vary by session (e.g. 2026-10-13's window contains 2024-01-20,
2024-03-02, 2024-05-18, 2025-02-01 and 2026-02-01).

**Technical conclusion, not a hand-wave:** the contamination is confined to a
small minority of rows and is bounded in magnitude. For `sma_50` the largest
divergence is 0.017 on a series in the tens — a rounding-scale effect. For the
52-week extremes the largest divergence is a few rupees on a handful of
illiquid SME names whose window extreme happens to *be* one of the questionable
rows — which is precisely the case where including it is wrong today and would
become right if the row is removed. Recovering those rows now produces a value
correct *given current data*; if the eight sessions are later removed, at most
18 `high_52w` and 26 `low_52w` values would need a small adjustment.

**No difference is claimed harmless without that reasoning**: the numbers above
are the justification, and they are why `sma_200` — with by far the largest
exposure at 126 rows and a 3.63 maximum — is treated differently (§D, §E).

**Does the current manifest include rows whose values depend on the unresolved
sessions?** Yes — that is precisely what the 176 differing pairs measure. The
manifest is not blind to this; Phase 23 did not quantify it and this phase does.

---

## D. SMA200 mismatch analysis

Phase 23 observed 7 mismatches out of 150 at the 2026-10-16 reference. All seven
are now explained:

| Symbol | stored | full-history | total rows | rows in 365-day frame |
|---|---|---|---|---|
| AARADHYA_SME | 108.7192 | 113.3968 | 225 | **180** |
| ABAN | 21.9195 | 22.1913 | 4,942 | **197** |
| AGARWALFT_SME | 36.5366 | 42.3855 | 713 | **160** |
| AGSTRA | 3.2557 | 3.3112 | 1,110 | **190** |
| AGUL_SME | 65.2428 | 59.6525 | 545 | **104** |
| AISL_SME | 65.0494 | 70.0367 | 1,504 | **164** |
| ALCODIS_SME | 85.9246 | 94.6168 | 1,368 | **169** |

**All seven have fewer than 200 rows inside the producer's 365-day frame**, while
having 225–4,942 rows of total history. They are *infrequently-trading* symbols,
not short-history listings. Their stored value is the mean of whatever rows
existed inside the frame (`min_samples=1`); the full-history rule averages the
last 200 **sessions**. 31 of the 150 sampled symbols have <200 rows in that
frame, so the divergence class is 7 of 31 — not 7 of 150.

**Cause: insufficient observations within the producer's window definition, not
missing dates, duplicates, weekend contamination or source differences.**

**Warm-up rule.** The contract states a 200-session mean; both producers use
`min_samples/min_periods=1`, so a symbol with 104 observations still receives a
value that is *not* a 200-session mean. Across the 40,332 target pairs:

| History depth | Pairs | Share |
|---|---|---|
| < 50 sessions | 3,295 | 8.17% |
| **< 200 sessions** | **6,299** | **15.62%** |
| < 252 sessions | 7,430 | 18.42% |

At 2026-10-13, 491 symbols have <200 sessions. No minimum-history rule has been
invented here to improve a match rate; the honest statement is that **15.62% of
the planned `sma_200` writes would store a mean over fewer than 200
observations, and the two producers demonstrably disagree about what such a
symbol's `sma_200` should be.** That is an unresolved definition, not a
recoverable value.

---

## E. Recovery recommendation — **Option B**

### Option A — recover all four columns
*Advantages:* completes the repair; removes all four degenerate shapes at once.
*Risks:* writes ~38,329 `sma_200` values of which 6,299 rest on fewer than 200
observations and across a definition the two producers disagree on; locks in
values for 126 rows that would shift if the eight weekend sessions are removed.
*Downstream:* inert (see §F), but it would bake a contested definition into
production.

### Option B — recover `sma_50`, `high_52w`, `low_52w`; defer `sma_200` **(RECOMMENDED)**
*Advantages:* removes the reported defect (`sma_50 == close`, `high_52w == high`,
`low_52w == low`) on all 38,329 rows; exposure to the unresolved calendar defect
is 6, 18 and 26 rows respectively (≤0.07%) with bounded magnitudes; the rule for
each was validated at 150/150, 130/130 and 124/124; `sma_200` — the one column
with both the largest contamination and a live definitional conflict — is left
alone until it can be decided properly.
*Risks:* a future `sma_200` backfill must revisit these same rows; the four
columns will be at different vintages.
*Downstream:* no incoherence — §F.
*Approvals:* the existing Phase 23 authorization, **with `sma_200` removed from
the plan and the manifest hash regenerated**.

### Option C — defer everything
*Rejected.* The defect is real and measured, the rule is validated, and no
consumer reads the affected rows (§F). Deferral would leave a known-fabricated
`base_score`-adjacent column set in place for no safety gain.

### Exactly what a future authorization would cover
Sessions: the same 12 (2026-08-17 … 2026-10-13).
Columns: `sma_50`, `high_52w`, `low_52w` **only**.
Excluded: `sma_200` (deferred), `delivery_ma_60` (never recoverable).
Scope: ~38,329 rows; ~38,164 / 36,395 / 36,497 cell changes respectively.
A **new manifest hash must be generated** for the reduced scope — the Phase 23
hash covers all four columns and must not be reused.

---

## F. Cross-field and downstream consistency

The four columns are consumed together in many places, so this was checked
rather than assumed. The decisive facts:

- **`myra_web/routes/fundamentals.py:125`**, **`myra_app/portfolio_db.py:334`**,
  **`myra_app/ai_second_opinion.py:260`**: all use `ORDER BY date DESC LIMIT 1` —
  the **latest** row only, which is clean (2026-10-16).
- **Every scanner** reads `df["high_52w"].iloc[-1]` / `df["sma_50"].iloc[-1]` —
  again the latest row.
- **`liquidity_flip_detector.py:198`** computes its *own* `sma_200`
  (`df["close"].rolling(200, min_periods=1).mean()`) and gates on
  `len(df) >= 200`; it never reads the stored `sma_200`.
- **`screening_modes.TRIGGER_CONTRIBUTING_COLUMNS`** (`sma_50`, `high_52w`,
  `low_52w`) is evaluated by `_column_population(latest_date)` — the **latest**
  session, not history.
- The only consumer that reads a *date range* is the chart endpoint
  (`AdvancedChart.tsx:1665`), where repaired history would render **correctly**
  instead of showing the degenerate session-value.

**Therefore:** no consumer reads a historical row of these columns, none mixes a
recovered row with a stale one, and none assumes the four columns come from a
single coherent run. Recovering three and deferring one creates **no**
downstream incoherence. V2.5 publication stays blocked throughout
(`mandatory_inputs_unsourced`; lake empty), and `sma_200` is a V2.5 mandatory
input whose absence is unchanged by this decision.

---

## G. Execution safeguards

| Safeguard | State |
|---|---|
| Reproducible target selection | ✅ `tools/f2_recovery_manifest.py`, read-only |
| Pre-write hash validation | ✅ present; **must be regenerated** for the reduced scope |
| Source/schema checks | ⚠️ defined in the report, not enforced in code |
| WAL-safe backup | ✅ **proven this phase** on a temporary WAL database |
| Transaction + rollback | ✅ tested (`test_a_failed_apply_rolls_back_completely`) |
| Idempotency | ✅ tested; a rerun plans zero work |
| Post-write verification | ⚠️ queries specified in the Phase 23 report, not automated |
| Concurrent-writer protection | ⚠️ `BEGIN IMMEDIATE` blocks a concurrent writer mid-run, but no *pre-flight* check exists |
| Safe abort on drift | ✅ hash comparison |

**Backup evidence (temporary database, never production).** `VACUUM INTO`
produced a complete copy: 500/500 rows, `PRAGMA integrity_check` → `ok`, and a
SHA-256 over the ordered rows **identical to the source**. The same test showed
why a plain file copy is unacceptable: copying a WAL database's main file
produced a database with **no tables at all**, because committed data still
lives in the `-wal` file. R0 must use `VACUUM INTO`, verify `integrity_check`,
and compare a content digest before proceeding.

**Smallest required changes before any execution** (none applied here — this
phase adds no production-write path):

1. Regenerate the manifest for the three-column scope and record its hash.
2. Add a pre-flight assertion that no process holds a write lock and that the
   row count and schema match the recorded values.
3. Wrap the run so post-write verification queries execute and fail loudly.

---

## H. Prohibited operations — confirmation

- Every live connection used `sqlite3.connect("file:…?mode=ro", uri=True)`.
- `tools/f2_recovery_manifest.py` has no writable code path.
- **No recovery executed.** R0–R4 were not run; no backup of production was
  created (the backup test used a temporary directory).
- No backfill, ingestion, bulk refresh, migration, indicator-lake build or
  scheduler operation. No scoring weight, threshold or formula changed.
- `MYRA_V25_RANKING_INPUTS` still disabled; no V2.5 score published; no
  mandatory-input blocking weakened; TriggerScanner operational.
- The manual end-of-day CSV workflow untouched.
- Live-database guards, test isolation and schema checks unchanged.

Production state after this phase is identical to the verified state in §A.

---

## I. Owner decisions

1. **Adopt Option B?** Recover `sma_50`/`high_52w`/`low_52w` for the 12 sessions
   and defer `sma_200`. *Recommendation: yes.*
2. **Correct the record on 2026-02-01.** It is a declared NSE live trading
   session, not a phantom. Phases 20/21 label it as a defect needing source-owner
   resolution; that is now known to be wrong and the earlier reports should be
   annotated. *Recommendation: annotate.*
3. **Route the eight unexplained weekend sessions to the eod2 owner** with the
   four questions in §B. *Recommendation: yes.*
4. **Decide the `sma_200` contract** for symbols with fewer than 200 sessions —
   minimum-history rule, or accept a truncated-frame mean. This blocks the
   deferred column. *Recommendation: decide before any `sma_200` backfill.*
5. **`delivery_ma_60`** still has no trustworthy producer or reference value.
   *Recommendation: a separate product decision, not a recovery.*
6. **Align the two producers' 52-week semantics** (252 sessions vs 365 calendar
   days). Out of scope here but it will resurface.