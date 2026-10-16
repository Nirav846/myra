# Phase 16 - Restore Production Screening and Make Readiness Truthful

**Status:** complete. Code, tests, and documentation; **no production rebuild
was executed.**
**Production behaviour changed:** yes - the CLI/turbo screening path now
reports *why* it cannot screen instead of returning an empty result, readiness
gained a `blocked` state, and the indicator-lake producer is reachable and
correct. No weight, threshold, formula, band, ordering, or scoring input was
changed. Growth remains disabled.

---

## 1. Scope, constraints, and what this phase was not allowed to do

Phase 15 established that MYRA's ranking snapshot could not be produced
(`INSUFFICIENT DATA`) and named five things that had to be fixed first. This
phase does items (1)-(4): restore a functioning screening path, make failures
observable, make readiness reflect screening capability, and reconcile the
universe counts. It deliberately stops before (5), the growth A/B/C run.

Constraints carried from the brief:

1. Inspect and reproduce before changing anything.
2. Preserve all existing branches and stashes; no reset, clean, or drop.
3. No growth activation; no weight, threshold, formula, band, or ordering change.
4. The missing-ATR defect (Phase 15 section 7) stays **isolated and unfixed**.
5. No uncontrolled full rebuild - no thousands of file rewrites without an
   established cost and a safe recovery.
6. No fabricated values; no zero-defaulted indicators to make a scan "pass".
7. The manual EOD CSV update and ingestion workflow is untouched.
8. No production database or historical-data writes during investigation.
9. Readiness must not report `ready` merely because ingestion is current.
10. No new framework, scheduler, or broad refactor.

Rule (9) is the one that had to be implemented, and rule (5) is the one that
had to be *not* implemented: the rebuild itself is left as an explicit,
documented operator action (section 7).

---

## 2. Starting state and reproduction

Measured read-only, before any code change:

| Fact | Value |
|---|---|
| `data/indicators/precomputed` | directory exists, **0 files** |
| `data/ohlcv_cache` | 3,320 parquets, newest index date **2026-04-30** |
| `data/lake` | 3,320 parquets, byte-identical cache, an orphan path |
| `technical_data` newest session | 2026-10-15 |
| `TriggerScanner` universe (working web path) | 1,846 symbols |
| `TriggerScanner.scan()` on a bounded 200 | 2 real candidates in 2.6 s |

The two columns at the bottom are the scope correction that Phase 15 could not
make: there are **two screening stacks**. The web/API path
(`myra_web/routes/scanners.py` -> `myra_app/strategies/*_scanner.py` ->
`fundamentals` + `technical_data`) works today and never reads the lake. Only
the **CLI/turbo path** (`MYRAScreener.execute_scan` -> `Engine.run_scan` ->
`load_universe` -> `precompute_indicators`) depends on the lake.

### 2.1 The producer is unreachable - the actual root cause

`Librarian.sync_market_data(history_years, skip_maintenance)` performs
fetch + enrich, then hits:

```python
if skip_maintenance:
    sync_status.update(...)
    return                      # librarian_sync.py:90-92
...
self.update_indicator_history()  # librarian_sync.py:154 - never reached
```

Both call sites pass `skip_maintenance=True`, and there are only two
(`librarian_sync.py:318` in the background loop, `screener.py:273` in
`execute_scan`). **The indicator-lake producer is unreachable from the entire
codebase.** The `if not skip_maintenance:` at `librarian_sync.py:177` is dead
code for the same reason, which also means the "background sync" that
`start_background_sync()` starts is fetch and enrichment only - it has never
once built the lake.

### 2.2 Two independent proofs that it never ran

* **The contract broke in commit `aa7016e`** (2026-04-17, "Refactor
  precompute_indicators method") which removed the indicator calculations from
  the producer and dropped `pandas_ta`. Since then
  `precompute_indicators()` has ended in `assert "symbol" in df_final.columns`
  on an empty lake, so the empty-lake branch the callers were written for
  (`universe_loader.py:66-69`) was unreachable - D2.
* **`myra_log` was never imported.** `librarian_intelligence.py` called
  `myra_log(i, total, ...)` with no definition in the module, so the producer
  would have raised `NameError` on its very first symbol. The repository's
  `.flake8` ignores `F821`, so an undefined name in a never-executed path
  passed lint silently. **A clean lint run is not evidence that a code path
  executes.**

### 2.3 The failure chain, end to end

| # | Defect | Evidence |
|---|---|---|
| D1 | Producer unreachable behind `skip_maintenance=True` | `librarian_sync.py:90-92` before `:154`; both call sites |
| D2 | `precompute_indicators` raises `AssertionError` on an empty lake | `aa7016e` removed the calculations; callers' empty branch unreachable |
| D3 | `run_scan` caught its exception and returned `([], {})`, byte-identical to "no setups today" | `screener.py` `if not results:` with no reason |
| D4 | `_get_cached_indicators`' "starting background refresh" called `sync_market_data(skip_maintenance=True)` | structurally incapable of rebuilding the lake |
| D5 | `data_readiness.assess()` reported `ready` while screening was impossible | readiness saw only ingestion |

---

## 3. What was implemented

### 3.1 A vocabulary for the outcome (`myra_app/scan_outcome.py`, new)

Six closed statuses: `success_with_candidates`, `success_no_candidates`,
`failed_missing_required_artifacts`, `failed_invalid_input_schema`,
`failed_indicator_computation`, `failed_internal_error`, with a frozen
`ScanOutcome` dataclass (`status`, `reason`, `detail`, `candidates`,
`error_type`). `reason` is a stable machine code and `detail` is prose; they
are separate for the same reason `ReadinessRecovery` separates them.

`success_no_candidates` is deliberately a **success**: a zero-result scan is
information, not an error.

### 3.2 A cheap, read-only probe (`myra_app/indicator_lake.py`, new)

Lists the lake, then opens at most 3 artifacts to confirm the contract
`precompute_indicators` enforces (a `symbol` column plus an index whose
`reset_index()` yields `date`). The real cache was inspected to confirm the
contract: `20MICRONS.parquet` carries `index.name == 'date'`, a `symbol`
column, 998 rows x 35 columns.

Paths resolve through `constants.DATA_DIR` **at call time**, never at import,
and nothing here creates a directory - a probe that materialised what it was
checking for would hide the very problem it exists to surface.

### 3.3 Atomic, resumable, explicit builds (`librarian_intelligence.py`,
`data_loader.py`)

* `precompute_indicators` now returns `pd.DataFrame()` and logs when there are
  no usable rows. The assertions for the non-empty case remain - only the
  impossible case was removed.
* `build_indicator_lake(symbols=None, *, force, max_symbols, reference_date,
  progress, cancel_event)` is the only supported writer. Freshness-gated
  (an artifact already at least as new as the newest stored session is
  skipped), bounded by `max_symbols`, resumable after interruption, and it
  **never raises for a per-symbol failure** - failures are counted and the
  first 25 error strings returned.
* `update_indicator_history` is now a thin wrapper that delegates with
  `force=True`, so existing callers keep their semantics.
* `IndicatorManager.save_indicators` writes to `<path>.tmp` and `os.replace`s
  it into place, cleaning up the scratch file on failure. A half-written
  artifact can no longer load as a healthy one.
* `IndicatorManager.base_dir` is a property resolved per access; reads go
  through `_read_path`, which never creates a directory.

### 3.4 The engine records what happened (`engine.py`, `screener.py`)

`Engine.last_scan_outcome` is reset at the start of every `run_scan` and set on
**every** exit path. `_classify_failure()` maps exceptions to a status by
remedy: `FileNotFoundError` -> missing artifacts; `AssertionError`/`KeyError`/
`IndexError` -> invalid input schema; `ValueError`/`TypeError`/`AttributeError`/
`ArithmeticError` -> indicator computation; anything else -> internal error.

`_abort_empty_universe()` distinguishes two failures that both used to be an
empty list: `indicator_lake_empty` (nothing was built) and
`no_precomputed_rows` (artifacts exist but yielded nothing).

`MYRAScreener.execute_scan` now prints `outcome.label` plus `detail` when a scan
failed, and prints `reason` when a scan completed with no matches. The
"Starting background refresh..." message - which described work that could not
happen - is replaced with the lake's real state and the concrete command to
build it.

### 3.5 Readiness knows about capability (`data_readiness.py`)

`STATE_BLOCKED = "blocked"` ("Screening blocked"), `screening_facts()`, and
`_apply_screening()`:

* `screening_facts()` reasons are `probe_unavailable`, `probe_failed`,
  `unreadable`, `lake_empty`, `invalid_artifact`, `stale`,
  `coverage_insufficient`, `coverage_partial`, `ok`. It reads one `COUNT(*)`
  for the active-universe denominator and opens at most 3 artifacts.
* `_apply_screening()` is **escalation only**: `READY`, `WAITING`, and `STALE`
  may escalate to `PARTIAL`/`STALE`/`BLOCKED`. `FAILED`, `UNKNOWN`, and
  `PARTIAL` are never downgraded, so a genuine ingestion failure still wins.
  Waiting for an unpublished session does *not* hide an unusable lake, because
  an install whose lake has never been built is blocked, not merely waiting.
* The verdict and its reason appear in the `assess()` payload and in
  `fingerprint()`, so a lake going from blocked to built is a transition the
  UI can actually see.

Frontend: `ReadinessState` gained `'blocked'`, `DataReadiness` gained a
`screening` field, and `DataReadinessPanel` styles `blocked` as a failure with
an alert icon (`STATE_STYLE` has an unknown-state fallback, so an old backend
cannot break the panel).

---

## 4. Universe reconciliation (measured, read-only - membership unchanged)

Phase 15 found "3,610 active vs 3,194 technical vs 3,221 readiness-universe".
All three numbers are correct for what they measure:

| Definition | Source | Count |
|---|---|---|
| Active universe | `symbols_master WHERE in_active_universe = 1` | **3,610** |
| Distinct symbols with any technical row | `technical_data` (all time) | **3,425** |
| Symbols with a row at the newest session | `technical_data` @ 2026-10-15 | **3,194** |
| Readiness "universe" | MAX over the last 3 sessions | **3,221** |
| CLI/turbo working set | `load_universe` -> `precompute_indicators` | **0** (empty lake) |
| Web scanner universe | `fundamentals` + market-cap band | **1,846** |

Joins (read-only):

| Set relation | Count |
|---|---|
| active ∩ technical (all history) | 2,419 |
| active ∩ technical (newest session) | 2,353 |
| active - technical (all history) | 1,191 |
| technical (all history) - active | 1,006 |
| technical (all history) - technical (newest session) | 231 |

Cause classes for `active - technical` (1,191): 1,053 other, 108 possible
ETF/AMC fund, 30 rights (`-RE`). For `technical - active` (1,006): **585 `_SME`
suffix**, 420 other, 1 fund. The **twin-symbol convention** dominates: a
symbol and its `_SME` listing are two rows in `technical_data` for one
underlying company (e.g. `AAKAAR` and `AAKAAR_SME`). Separately,
`symbols_master` labels every row `instrument_type = 'EQUITY'`, including
ETFs and rights.

**Nothing was added to or removed from any universe.** This section reports the
definitions and the join gaps; settling what "the active universe" *should*
mean is a product decision, not a repair, and is left to the operator.

---

## 5. Verification

| Check | Result |
|---|---|
| New tests (`tests/test_phase16_*.py` + Phase 16 additions to `test_data_readiness.py`) | **79 passed** |
| Full suite | **1624 passed** (Phase 15 baseline 1545) |
| Phase 15 ATR characterization (`test_phase15_atr_characterization.py`) | 10 passed, untouched |
| `black` (pre-commit's pinned 26.x) | clean |
| `flake8` (pinned 6.0.0) | clean |
| `tsc --noEmit` (`pnpm lint`) | clean |
| Production databases / caches / lakes | **never written**; the lake is still 0 files |

What the tests pin: probe availability vs validity, the "never create what you
are checking for" rule, atomic writes (including the failure path), the
empty-lake return contract, the real producer round-trip
(computation -> file -> probe -> consumer), build freshness gating, resumability,
`max_symbols` bounding, per-symbol failure isolation, cancellation, the
`myra_log` progress path, the `ScanOutcome` vocabulary, exception
classification, both empty-universe reasons, `screening_facts`' verdicts, the
escalation-only rule, and readiness payload/fingerprint wiring.

### 5.1 Two things this phase changed about existing tests, and why

* `tests/test_data_readiness.py` gained an autouse fixture that stubs
  `screening_facts` to healthy. `fixture_db_dir` redirects `DB_DIR`, not
  `DATA_DIR`, so without it those tests would probe whatever lake the machine
  running them happens to have and every `ready`/`waiting` verdict would be
  escalated for a reason the module does not test. Four tests at the end of the
  file re-patch it to check the escalation wiring itself.
* `test_scoring_components_are_not_modified_by_this_phase` was a whole-file
  `git diff` guard that included `myra_app/screener.py`. Phase 16 legitimately
  edits that file's *reporting* (section 3.4), so the guard is split: the three
  files carrying the scoring maths stay byte-identical, and `screener.py` is
  checked for ranking-relevant markers (`stars_count`, `s_score`,
  `multibagger_early`, `smc_phase`) in its changed lines instead. The Phase 15
  guarantee is preserved and made durable rather than deleted.

---

## 6. What was deliberately not done

* **No production rebuild.** Constraint 5/8. Section 7 is the runbook.
* **No scoring change.** `score_components_v2.py`, `positional_engine.py`,
  `ranking_inputs.py` are byte-identical to HEAD, and the ATR defect from
  Phase 15 section 7 is untouched.
* **No growth activation.**
* **No universe membership change.**
* **No new dependency, scheduler, or framework.**

---

## 7. Runbook: building the indicator lake (operator action)

```powershell
# Read-only: what exists, what is fresh, what is unusable
python -m myra_app.lake_build --status

# Build, staged. Idempotent: re-running skips artifacts that are already
# at least as new as the newest stored session.
python -m myra_app.lake_build --limit 500
python -m myra_app.lake_build --limit 500   # next batch, no rework

# Or one bounded, interruptible (Ctrl-C) run
python -m myra_app.lake_build
```

* **Scope:** reads `data/ohlcv_cache` (3,320 files, newest index date
  2026-04-30) and writes one artifact per active symbol under
  `data/indicators/precomputed`. Roughly 3,610 files on a cold build;
  `--status` writes nothing at all.
* **Recovery:** the build is resumable and idempotent. To roll back, delete
  `data/indicators/precomputed` - it is regenerable and holds no data that
  exists nowhere else.
* **Watch out:** the OHLCV cache is ~168 days behind `technical_data`, so a
  build from it produces a lake that the probe will correctly report as
  **stale**. Refreshing the cache and ingestion (the existing manual EOD
  workflow) comes first; this phase deliberately did not touch either.
* After a build, readiness should move `blocked` -> `stale`/`partial` -> `ready`,
  and `python -m myra_app.lake_build --status` is the single command that
  says where you are.

---

## 8. Known limitations and pre-existing issues (reported, not fixed)

1. **The producer does not cover the scorer's full technical block.**
   `update_indicator_history` / `_compute_symbol_indicators` emits `sma20`,
   `sma50`, `sma200`, `rsi`, `atr20`, `vol_sma50`, `avg_deliv_20d`, `rdv`,
   `delivery_pct`, `vcp`, `delivery_divergence_score`,
   `delivery_accumulation_signal`, `ias`, `vwap`, `fvg`, `bos`, `choch`,
   `inst_vol`, `symbol`. It never emits `atr_pct`, `sma150`,
   `pct_above_ma50_60d`, `keltner_*`, `atr5`, or `drawdown`, and
   `technical_data`'s DDL has only `sma_50`/`sma_200`. **A rebuilt lake
   therefore restores screening but not the scorer's full technical block (70%
   of the score).** This is stated rather than papered over with defaults.
2. `get_ohlcv` writes parquet back to the cache on read; it is not read-only.
3. `engine.py:267` builds a meta-DB path from `os.getcwd()`, bypassing the
   `DB_DIR` / `DB_MAP` invariant (pre-existing, from before this phase).
4. `roe_ttm`, `smart_money_score`, `debt_to_equity` still have no producer and
   stay `None`.
5. `.flake8` ignores `F401` and `F821`; a clean lint run does not prove a code
   path executes (section 2.2).
6. Dependabot's `base_factor` `dependency_file_not_resolvable` failure is
   pre-existing and third-party; no action taken.

---

## 9. Handoff

Phase 16 delivers (1)-(4) of Phase 15's unblock list: the producer is
reachable and correct, the failure is loud and classified, readiness reports
`blocked` instead of `ready`, and the universe counts are explained.

What remains before the growth A/B/C comparison (5) is **not code**: run
section 7, confirm readiness reaches `ready`, then resolve the scorer's
missing technical inputs (item 1 of section 8) - without that, a scan can run
but the ranking inputs are still incomplete. The ATR defect stays on its own
track.
