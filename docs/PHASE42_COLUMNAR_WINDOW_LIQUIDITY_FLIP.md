# Phase 42 — Columnar window behind Liquidity Flip's frame accessor

Follows `docs/PHASE41_LIQUIDITY_FLIP_FRAME_ACCESSOR.md` (`c5f78e5`) and the
Phase 40 audit's step 2: *re-measure with a columnar store behind that one
accessor before committing to the representation change*.

**Outcome: implemented.** The gain is reproducible end to end, parity is exact,
memory does not grow, and the change is confined to one strategy module.

## 1. Design

`myra_app/strategies/liquidity_flip_detector.py` only:

- **`_ColumnarWindow`** (module-private, ~70 lines): a build pass appends each
  requested column of every symbol frame into one contiguous numpy array and
  records `symbol -> (start, end)`. `frame(symbol)` slices the arrays and builds
  one small DataFrame. Columns absent from every frame are dropped, matching
  `get_df_for_symbol`; empty frames are simply absent.
- **`scan()`** folds the slice into the window immediately after
  `load_ohlcv_for_universe` and releases the dict (`self._bulk_data = None`), so
  only one representation is alive while the loop runs.
- **`_get_tech_df()`** keeps its Phase 41 contract and gains a first branch:
  window → bulk dict → per-symbol SQL. The dict and SQL branches are unchanged,
  which keeps the accessor usable outside a batch and keeps the Phase 41 tests
  meaningful as the compatibility boundary.

Why this stays bounded: `bulk_loader.py`, `_BatchSnapshot`, `_Scope`,
`rows_for_symbol` and `get_df_for_symbol` are untouched, so all 17
`load_ohlcv_for_universe` call sites and 14 consumers keep their contracts. No
detection, threshold, scoring, ordering, output-field or date-rule change. The
window is built from the *slice* rather than the snapshot, so it inherits the
per-request `_match_request_dtypes` recast for free — no second dtype rule to
keep aligned.

## 2. Contract hazards

| hazard | handling | evidence |
|---|---|---|
| **A** missing columns | window drops a column absent from every frame, exactly like `get_df_for_symbol`; `_get_tech_df`'s existing `sma_50`/`high_52w`/`low_52w` NaN restore is untouched | `test_columnar_window_drops_columns_absent_from_every_frame`, Phase 41 absent ≡ all-NaN tests |
| **B** dates/order/index | arrays keep snapshot order; `_get_tech_df` still sorts and resets, and `frame()` applies `to_datetime` | `..._keeps_column_order_and_the_date_contract`, `..._matches_a_narrow_date_window`, Phase 41 index tests |
| **C** dtypes / all-NA | arrays are built from the slice, so the recast is inherited; `np.concatenate` preserves `object`, `float64`, `int64` | `..._preserves_an_all_na_object_column`; pandas-3 proxy run |
| **D** ownership | every `frame()` returns fresh blocks; nothing is shared with the arrays | `..._do_not_share_memory_with_the_window`, `test_mutating_one_columnar_frame_cannot_leak` |
| **E** symbol coverage | absent/empty symbol → `None` → empty frame → the existing row-count guard; whitespace and selective universe unchanged | `..._returns_none_for_absent_and_empty_symbols`, Phase 39 suite (30 tests) |

A column set is treated as uniform across the slice, which `_read_bulk`
guarantees (one result set). If a future schema ever violated that, the dropped
column would surface through the same per-symbol `except` path as today.

## 3. Measurements

**Workload:** live `myra_technical.db`, read-only; the scanner's own window
(`as_on - 350 days … 2026-10-16`); 2,012-symbol universe → **1,921 symbols /
413,078 rows**; one `_read_bulk` + one prewarm shared by both arms.

### 3.1 Accessor preparation (1,921 symbols, 3 rounds)

| stage | min | median |
|---|---|---|
| `slice()` — both arms | — | 1.46 s |
| Phase 41: `get_df_for_symbol` + sort/reset | 14.51 s | 15.64 s |
| Phase 42: window build (one-off) | 0.516 s | 0.522 s |
| Phase 42: materialize + sort/reset | 9.61 s | 10.24 s |
| **net preparation** | **14.51 → 10.13 s (−30 %)** | **15.64 → 10.76 s (−31 %)** |

A wider 545-day window (1,922 symbols / 622,389 rows) measured
16.04–16.79 s → 9.77–10.42 s + 0.52 s build, i.e. −5.74 s, with **1,922/1,922**
symbols `assert_frame_equal`-identical to `get_df_for_symbol`. The first build in
a cold process took 2.87 s; steady state is 0.52 s.

### 3.2 End-to-end (5 interleaved rounds, real modules both sides)

The Phase 41 arm is the `c5f78e5` file materialized via `git show` into a
throwaway module — not a re-implementation.

| arm | runs | min | median |
|---|---|---|---|
| Phase 41 | 25.20, 23.32, 23.16, 25.29, 23.07 | 23.07 s | 23.32 s |
| Phase 42 | 19.97, 20.61, 21.03, 20.94, 19.82 | **19.82 s** | **20.61 s** |
| delta | | **−3.25 s (−14.1 %)** | **−2.71 s (−11.6 %)** |

No round overlaps (max Phase 42 = 21.03 s < min Phase 41 = 23.07 s).
Candidate output identical in all 10 runs: **15 rows, fingerprint
`9ac001e004b9f4d8`** — the same fingerprint recorded in Phases 40 and 41.

The end-to-end delta is smaller than the accessor delta because the micro-loop
has no per-symbol compute between accessor calls; the E2E number is the one that
counts.

### 3.3 Memory

| representation | bytes |
|---|---|
| Phase 41 slice, whole frames `deep=True` (incl. `symbol`, `swing_low`, per-frame overhead) | 87.5 MB |
| Phase 41 slice, the 12 consumed columns only | 61.0 MB |
| Phase 42 window (39.7 MB numpy + 20.9 MB `date` string payload) | 60.7 MB |
| peak during the 0.52 s build (both alive) | 121.7 MB |

So steady state is **−31 %** against what the scan actually held, and equal to
the 12 columns alone (60.7 vs 61.0 MB). Reporting only `ndarray.nbytes` would
have claimed −55 %; that number is wrong because an object-dtype array stores
pointers, not the `date` strings. Peak rises by ~34 MB for half a second.

Limitation: `memory_usage(deep=True)`/`sys.getsizeof` accounting, no RSS
(`K32GetProcessMemoryInfo` returns 0 on this box).

## 4. Tests

`tests/test_liquidity_flip_df_accessor.py` — **34 tests** (20 from Phase 41
unchanged in intent, 14 new), written first: the new section started at
**15 failed / 19 passed** and is now green.

New coverage: every-symbol window ↔ `get_df_for_symbol` equality on a 60-symbol
mixed-history fixture (empty, 1-row, 6-row, short and full histories), narrow
date window, column order + dtypes + `datetime64` date, all-NA object column,
drifted columns dropped, ignored columns (`swing_low`), absent/empty/unknown
symbols, empty slice, no shared memory, mutation isolation across requests and
symbols, dict-path ↔ window-path equality for every symbol, a scan served purely
from the window (`get_df_for_symbol` patched to raise), early return on an empty
universe, and a fresh window per scan.

Retained: the hand-computed golden candidate (now served through the window,
`flip_score` 71.4, grade A), the `rows_for_symbol` oracle parity suite, the
hazard tests, and `tests/test_selective_bulk_universe.py` (30 tests, recorder
updated to `_get_tech_df`).

## 5. Gates

- focused: 34 + 30 = 64 passed; loader/scanner set 107 passed;
- pandas-3 proxy (`future.infer_string = True`): 64 passed;
- full suite: **2,251 passed** in 339.7 s (was 2,237);
- `pre-commit` (black 26.10.1, flake8, performance guard, schema drift): green;
- production DB mtimes unchanged (`myra_technical.db` 2026-10-18 21:31:01,
  `myra_valuation.db` 2026-10-18 22:01:02, `myra_metadata.db` 2026-10-21 13:48:56).

## 6. Not done

- No other consumer migrated (the remaining ten each need their own hazard
  check); `bulk_loader.py` and the snapshot stay as they are.
- Not measured: the other ten scanners' end-to-end effect, other `as_on_date`
  values, and a full confluence batch (excluded by scope).
- The build still duplicates the slice for ~0.5 s. Removing that copy means
  changing what `load_ohlcv_for_universe` returns, which is the 17-call-site
  redesign Phase 40 deliberately deferred.
