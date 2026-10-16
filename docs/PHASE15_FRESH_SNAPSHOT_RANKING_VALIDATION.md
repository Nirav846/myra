# Phase 15 — Fresh-Snapshot Readiness and Controlled Three-Way Shadow Comparison

**Status:** complete, negative result.
**Decision:** `INSUFFICIENT DATA`.
**Production behaviour changed:** none. `MYRA_V25_RANKING_INPUTS` and every
default path are untouched; growth remains disabled in production.

---

## 1. Scope, constraints, and what this phase was not allowed to do

The goal was to establish whether a *fresh, technically representative* ranking
snapshot could be produced at all, and — only if it could — to measure three
orderings on one common universe:

| Ordering | Meaning |
|----------|---------|
| **A** | production baseline |
| **B** | Phase 6 ranking inputs enabled |
| **C** | B plus the validated Phase 14A annual growth signal |

Hard constraints carried into the phase (all respected):

* growth stays disabled in production; no weight, threshold, formula, band or
  ordering change;
* no historical rewrite, no fundamentals backfill, no Screener.in enrichment,
  no ingestion or re-ingestion to make the experiment succeed;
* the manual-CSV-update workflow is preserved;
* database connections read-only; no fabricated or zero-for-unavailable values;
* no new backtesting framework, scheduler, database, or parallel ranking
  architecture;
* all stashes and unrelated working-tree changes preserved;
* **no scoring change to fix the missing-ATR defect in this phase**;
* if a reliable fresh snapshot could not be generated safely, stop at the
  readiness audit and report the blocker rather than manufacture a result.

This phase produced **no ranking evidence**. It produced a proven blocker, a
coverage baseline, and one separately-scoped scoring defect.

---

## 2. Starting state

| Item | Value |
|------|-------|
| Branch | `main` |
| HEAD | `d4c9215` — identical to `origin/main` |
| Working tree | clean |
| Stashes | `stash@{0}`–`stash@{2}` preserved |
| Prior phase | Phase 14A committed and pushed; CI `38873289957`, CodeQL `38873289663`, both green |
| Backend | stopped (port 8000 down, required for the read-only guard) |

Phase 14A artifacts (`myra_app/growth_signals.py`,
`tools/compare_v25_orderings.py`, `tests/test_growth_signals.py`,
`docs/PHASE14_FUNDAMENTAL_GROWTH_SHADOW_EVALUATION.md`) were present and
unmodified at the start of this phase.

---

## 3. Starting state → the real scoring and snapshot dataflow

Traced from the code, not assumed. There is exactly one production path from
database to saved snapshot:

```
MyraEngine.run_scan                                  myra_app/engine.py
  └─ universe_loader.load_universe                   universe_loader.py
       └─ lib.precompute_indicators(as_of_date)      librarian_intelligence.py:33
            └─ loader.indicators.load_indicators(
                   "precomputed", symbol)            data_loader.py:36
                 └─ data/indicators/precomputed/<SYM>.parquet   ← the indicator lake
       └─ (Phase 6, opt-in) ranking_inputs.compute_ranking_inputs
  └─ PositionalScorer.rank(...)                      positional_engine.py
       └─ score_components_v2.base_score/...         score_components_v2.py
  └─ results_manager.archive_results(...)            results_manager.py
       └─ myra_reports/scan_<strategy_id>.csv
```

Two structural facts matter for every measurement below:

1. **`universe_loader.load_universe` is the only production universe path.** It
   is imported by one module and called from one line: `myra_app/engine.py:232`.
2. **`myra_web/` never runs the positional scorer.** It contains no reference to
   `run_scan`, `execute_scan` or `MyraEngine`. `myra_web/routes/scanners.py`
   uses `scanner._get_universe()` and queries `technical_data` directly — a
   separate path that cannot produce a `PositionalScorer` ordering.

The shadow harness (`myra_app/shadow_ranking.compare_orderings`) calls the real
`PositionalScorer.rank`, so it was already capable of producing A and B and C on
a common universe. The blocker is entirely upstream of it.

---

## 4. Fresh-snapshot readiness audit

Performed read-only through `myra_app.data_readiness.assess()` and direct
`mode=ro` SQLite connections.

### 4.1 Ingestion is genuinely fresh — this part passes

| Check | Result |
|-------|--------|
| Overall state | `ready` |
| Latest market date | `2026-10-15` — equal to the expected trading day |
| Sessions available | `2026-10-15`, `2026-10-14`, `2026-10-13` |
| Symbols at those sessions | 3,194 / 3,204 / 3,221 |
| `coverage.universe` | 3,221 |
| `expected_session_complete` | `true` |
| EOD source folder | `ready`, 3,545 / 3,770 files at 2026-10-15, 0 unstable |
| Ingestion last success | `2026-10-15T21:58:51` |
| Fundamentals reporting period | `2026-09-30` (16 days old, `fresh`) |
| Fundamentals retrieved | `2026-10-13`, 3,948 symbols, 3,460 with core metrics |

The daily pipeline is doing its job. The failure is downstream of it.

### 4.2 The derived indicator lake is empty — this is the blocker

| Check | Result |
|-------|--------|
| `data/indicators/precomputed/` | **0 files** |
| `myra_app/data/indicators/` | exists, empty |
| Core OHLCV cache files | 3,320 (`data/ohlcv_cache`), 3,320 (`data/lake`) |
| OHLCV data range in those caches | 2021-10-01 → **2026-04-30** |
| OHLCV file mtime | 2026-05-02 |
| Lag behind the 2026-10-15 market date | **~168 calendar days** |

`data_loader.IndicatorManager` resolves its root as
`os.path.join(os.getcwd(), "data", "indicators")` (`data_loader.py:18`), i.e.
relative to the *current working directory* rather than through
`myra_app.constants`. From the documented run directory the lake resolves to
`D:\01screener\Myra\data\indicators\precomputed`, and it is empty.

`data/lake` is an orphan: no `.py` file in the tree references it.

### 4.3 The empty lake is fatal, and it fails silently

Verified by execution, not inference. Calling the real
`LibrarianIntelligenceMixin.precompute_indicators` against the real filesystem:

```
indicator dir that production reads : D:\01screener\Myra\data\indicators
precompute_indicators RAISED        : AssertionError -
```

The chain:

1. `precompute_indicators` reads every symbol through
   `load_indicators("precomputed", sym)` → every call returns an empty frame;
* `librarian_intelligence.py:74` `safe_concat(results_list)` on an empty list
  yields an empty DataFrame;
* `librarian_intelligence.py:76` `assert "symbol" in df_final.columns` raises;
* `myra_app/engine.py:295–298` catches it with a bare `except Exception as e`,
  prints one `[!] Turbo Load failed: ...` line, and returns `([], {})`.

**Net effect: every production scan currently returns zero candidates, and the
only visible symptom is a single console line.**

### 4.3.1 The readiness oracle does not detect this

`data_readiness.assess()` reports `ready` while no ranking snapshot can be
produced at all. The oracle measures *ingestion* freshness; it does not measure
whether a *derivable artifact* actually exists. This is the same class of gap as
Phase 13's "scheduled ≠ serving" — here it is **"ready ≠ derivable"**.

### 4.4 Why the lake cannot be rebuilt within this phase's constraints

The only writer is
`LibrarianIntelligenceMixin.update_indicator_history` (`librarian_intelligence.py:89`),
called from `librarian_sync.py:154`. Rebuilding through it requires
`LibrarianCore(read_only=False)`, whose constructor runs `_create_tables()`,
`_migrate_meta_schema()` and `SchemaRegistry.validate_schema()` — i.e.
**production schema writes**, which this phase forbids. It would also read a
168-day-stale cache and rewrite roughly 3,320 OHLCV files plus ~3,320 indicator
files.

Separately, `librarian.get_ohlcv` (`librarian.py:307`) has a staleness catch-up
that pulls the delta from `technical_data` and **writes the refreshed parquet
back** (~`:397`). Merely *reading* through that path mutates the cache, so it is
not a read-only operation either.

### 4.5 The writer does not produce what the scorer reads

`update_indicator_history` writes, for each symbol:

```
sma20, sma50, sma200, rsi, atr20, vol_sma50, avg_deliv_20d, rdv,
delivery_pct, vcp, delivery_accumulation_signal, ias, fvg, bos, choch, symbol
```

It never writes `atr_pct`. `schema/technical.sql` confirms `technical_data`
carries `sma_50` / `sma_200` (underscore names) and has **no** `sma150`,
`atr_pct`, `pct_above_ma50_60d`, `keltner_*`, `atr5` or `drawdown` column, so
those cannot be sourced from SQLite instead.

The scorer's technical block — 70% of the total score — therefore has **no
populated source anywhere in the current system**. Any ordering produced now
would be the technical block in a fabricated or fully-neutral state, which is
exactly what this phase was instructed not to manufacture.

---

## 5. Universe and input coverage (measured, read-only)

Four different "universe" numbers are in play, and they disagree:

| Definition | Symbols |
|-----------|---------|
| `symbols_master` rows (all `in_active_universe = 1`) | **3,610** |
| Distinct symbols in `technical_data` | 3,425 |
| Distinct symbols in `technical_data` at 2026-10-15 | **3,194** |
| `data_readiness` `coverage.universe` | 3,221 |

The two obvious joins:

| Join | Symbols |
|------|---------|
| active ∩ `technical_data` (all dates) | **2,419** |
| active ∩ `technical_data` at latest date | 2,353 |
| in `technical_data`, not active | 1,006 (predominantly `*_SME` symbols) |
| active, no `technical_data` at all | 1,191 (ETFs such as `ABSL10BANK`, `ABSLLIQUID`; rights such as `5PAISA-RE`) |

### 5.1 Ranking-input coverage (read-only, valid 20-session window)

Window: **2026-09-17 → 2026-10-15** (20 sessions).

| Input | Populated | Share of 3,610 active |
|-------|-----------|----------------------|
| ≥1 session in window (any symbol) | 3,356 | — |
| Full 20-session window | 2,898 | — |
| `avg_volume_20d` (active universe) | **2,332** | **64.6%** |
| `avg_delivery_20d` (active universe) | **2,332** | **64.6%** |
| `compute_ranking_inputs` returned symbols | 2,375 | 65.8% |
| `roe` | **234** | **6.5%** |
| `roe_ttm`, `profit_growth`, `sales_growth`, `debt_to_equity` | **0** | 0% |
| `atr_pct` with a *readable* value | **0** | 0% |
| Annual growth, eligible **within the active universe** | **1,788** | 49.5% |

`fundamentals` holds 3,948 rows; 2,956 of them are active-universe symbols, but
only 234 of those carry a non-null `roe`. `debt_to_equity` has no producer at
all (`upstox_income_statement` stores only revenue / operating profit / net
profit), and `roe_ttm` has no producer — both are recorded here, neither is
fabricated.

### 5.2 Growth eligibility, restricted to the active universe

`growth_signals.derive_growth` over `upstox_income_statement`:

| Outcome | Symbols |
|---------|---------|
| Symbols in source | 2,456 |
| Eligible (whole source) | 2,166 |
| **Eligible and within the active universe** | **1,788** |
| Eligible, active, and with a `fundamentals` row | 1,788 |

Refusal reasons (whole source):
`no_annual_records=2`, `no_prior_period=63`, `non_consecutive_periods=8`,
`stale_period=162`, `prior_base_below_floor=55`; all other reasons `0`.

The growth signal is the **healthiest** of the three candidate inputs, and it
still covers only half the active universe.

---

## 6. Three-way A/B/C comparison — not performed, and why

The comparison tools exist and are correct: `shadow_ranking.compare_orderings`
drives the real `PositionalScorer`, `MATERIAL_MOVE = 5`, and
`tools/compare_v25_orderings.py` already exposes
`--growth-source {none,upstox-annual}` while leaving the default path unchanged.
No new comparison behaviour was needed, and none was added.

The comparison was **not run against fresh data**, for three independently
sufficient reasons:

1. **No snapshot can be produced.** With the lake empty,
   `precompute_indicators` raises `AssertionError` and `run_scan` returns
   `([], {})`. There is nothing to compare.
2. **The scorer's technical block can't be populated.** 70% of the score depends
   on fields (`sma50`, `sma150`, `sma200`, `atr_pct`, `pct_above_ma50_60d`,
   `keltner_*`, `atr5`, `drawdown`) that exist in neither the lake nor
   `technical_data`. Any frame built now would measure the fabricated fallbacks,
   not the strategies.
3. **Building a frame anyway would violate the constraints.** Rebuilding the
   lake needs production schema writes and ~6,640 file rewrites against a
   168-day-stale cache.

Reporting A/B/C metrics from the April snapshot was considered and rejected: it
is stale (last snapshot `scan_rs_rating.csv`, 2026-04-25, 938 rows, of which
only 222 carry usable technical values), and Phase 14A already recorded it as an
upper bound rather than as fresh evidence. Re-presenting it here as a Phase 15
result would misrepresent stale data as current.

For the record, the Phase 14A upper-bound figures remain the only ranking
evidence in existence, and they are **not** activation-grade:
baseline Spearman 1.0 / top-20 20-of-20; growth candidate Spearman **0.9489**,
top-20 **6-of-20**, 216 moved, max displacement 56; 14 symbols entered and 14
left the top 20.

---

## 7. Missing-ATR base-score defect (separate audit, deliberately not fixed)

### 7.1 The chain

1. `score_components_v2.base_score` reads `atr = row.get("atr_pct", 0.05)`
   (`score_components_v2.py:104`), treating a tight base as favourable:
   `atr < 0.03` → `+0.5`; `atr < 0.05` → `+0.3`; then
   `return min(1.0, score if score > 0 else 0.3)` (line 125).
2. No producer writes `atr_pct` (section 4.5).
3. `universe_loader` therefore emits, for every symbol
   (`universe_loader.py:309`):

   ```python
   "atr_pct": c.get("atr_pct", 0) if c.get("atr_pct") is not None else 0
   ```

   A missing measurement becomes a stored `0.0` — a *numerically observed*
   value — instead of `None`.
4. `0.0 < 0.03`, so every row collects the **maximum** base score `0.5`.

An honest `None` or absent key would fall to the neutral `0.3`. So the system
rewards missing data with its best base score.

### 7.2 Measured impact

Executed against the real scorer:

| `atr_pct` | `base_score` | `MYRA_Score_v25` (NORMAL) |
|-----------|--------------|---------------------------|
| key absent | 0.3 | 57.7 |
| `None` | 0.3 | 57.7 |
| **`0.0` (fabricated)** | **0.5** | **59.1** |
| `0.02` (genuinely tight) | 0.5 | 59.1 |
| `0.04` (mid) | 0.3 | 57.7 |
| `0.08` (genuinely wide) | 0.3 | 57.7 |

Two conflations, both wrong:

* **missing == genuinely tight** (row 3 vs row 4) — the defect rewards absence;
* **genuinely wide == missing** (row 6 vs rows 1–2) — the floor at `0.3` erases
  the distinction between "wide base" and "no measurement".

Magnitude: `+0.2` on a `0.10`-weight component, applied before the `0.7`
technical share → **`+1.4` points** on `MYRA_Score_v25` at `NORMAL` regime with
no drawdown, ranging roughly `+1.1` to `+1.6` once the regime and drawdown
multipliers are applied.

**Relative ranking is not distorted.** The uplift is uniform, so Spearman
correlation and top-N membership are unaffected. What *is* affected:

* **absolute scores and therefore display bands.** A row scoring `68.8` on an
  honest neutral ATR scores `70.2` with the fabricated zero — it is presented as
  `70+` "Elite" purely because a measurement is missing. This is verified as a
  characterization test, not asserted from arithmetic.
* **explainability.** `_missing_inputs()` reports `[]` for a fabricated
  `atr_pct = 0.0`, so the breakdown presents a missing value as an observed,
  favourable one. An honest absent key *is* reported.

### 7.3 Scope

This affects **every** row in the active universe and therefore every saved
snapshot, including the April `scan_rs_rating.csv` (where `atr_pct` is constant
`0.0` across all 938 rows). It is a pre-existing defect, present before this
phase, and it is present in production right now.

### 7.4 What was and was not done

Fixed: **nothing**. Changing scoring behaviour requires its own evaluation, and
this phase was explicitly barred from touching it. The defect is pinned by
`tests/test_phase15_atr_characterization.py` (10 tests) so that the follow-up fix
has a measurable before/after — those tests assert the *defective* behaviour and
are expected to be inverted when the fix lands.

---

## 8. Verification performed

| Check | Result |
|-------|--------|
| New tests (`tests/test_phase15_atr_characterization.py`) | 10 passed |
| Full suite | see section 10 / session history |
| `black` | clean |
| `flake8` | clean |
| Frontend (`tsc` / build) | not applicable — no frontend file changed |
| Production growth flag | unchanged; growth remains disabled |
| Default buyer path | unchanged; `--growth-source` still defaults to `none` |
| Read-only guarantee | all DB access via `mode=ro`; no schema write, no file rewrite |

### Pre-existing issues encountered and reported (not caused by this phase)

* **Hardcoded DB path bypassing `DB_DIR`.** `myra_app/engine.py:267` builds a
  meta-database path as
  `os.path.join(os.getcwd(), "db", LibrarianCore.DB_MAP["meta"])`. This violates
  the documented invariant that database resolution always goes through
  `DB_DIR` / `DB_MAP`. It is on the fallback branch taken when `lib._meta_conn`
  is falsy. Severity: medium (wrong database silently used if the process cwd
  differs from the repository root — the same cwd assumption that section 4.2
  shows is already fragile).
* **`smart_money_score` has no producer.** It is never written, the star is
  never awarded, and it stays explicitly `None`. Left as is — fabricating a
  value is forbidden.
* **`roe_ttm` has no producer.** Documented, not fabricated.
* **Dependabot run `failure` on `base_factor`**
  (`dependency_file_not_resolvable`) — pre-existing and third-party; no action
  taken.

---

## 9. What would unblock this

In dependency order:

1. **Populate the indicator lake** (or make the pipeline derive the scorer's
   technical fields on demand). Until then no ranking snapshot exists and every
   scan returns zero candidates. This is a release-blocking bug independent of
   Phase 15.
2. **Make the empty-lake failure loud.** `engine.py:295-298` turns a total
   system failure into one console line and an empty result. A scan that
   produces zero candidates because its input artifact is missing must surface
   to the user.
3. **Extend the readiness oracle to cover derived artifacts**, so `ready` means
   "the ranking pipeline can actually run", not just "ingestion is fresh".
4. **Resolve the universe definition** — 3,610 / 3,425 / 3,221 / 3,194 — and
   the 1,006-symbol and 1,191-symbol join gaps, before any coverage claim is
   made about a snapshot.
5. **Only then** run the three-way A/B/C shadow comparison on a fresh, common,
   representative universe — reporting Spearman, top-10/top-20 overlap, moved
   counts, median/p90/max displacement, largest moves with coverage, top-20
   entrances and exits, and score deltas, alongside full-universe coverage.

The missing-ATR defect (section 7) is **not** a prerequisite for any of the
above, because it does not change relative order. It should be fixed and
evaluated on its own track.

---

## 10. Decision

> ### `INSUFFICIENT DATA`

No fresh, technically representative ranking snapshot can be produced. The daily
data pipeline is fresh and healthy, but the derived indicator lake the ranking
engine depends on is empty, so `precompute_indicators` raises and every
production scan silently returns zero candidates. The scorer's technical block —
70% of the score — has no populated source anywhere in the system, and building
one would require production schema writes and thousands of file rewrites
against a 168-day-stale cache, all of which this phase forbids.

The controlled three-way A/B/C comparison was therefore **not run**. No ranking
evidence is produced by this phase, and growth remains disabled in production.

The separate missing-ATR audit found a confirmed, uniform `+1.4`-point score
inflation from fabricated missing data, with band-crossing and explainability
consequences. It is audited, characterized by tests, and **not fixed** here.

**Strongest supported claim:** MYRA cannot currently produce any fresh ranking
snapshot, and its readiness reporting says everything is ready.
