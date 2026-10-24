# Phase 46 — Float Exhaustion: columnar window

| | |
|---|---|
| Scanner | `myra_app/strategies/float_exhaustion_scanner.py` (`FloatExhaustionScanner`) |
| Web route | `/float-exhaustion` (React) → `GET/POST /api/float-exhaustion/{status,scan}` |
| Baseline | 25.60 s min / 25.87 s median end-to-end, 444 candidates, fp `5eda03eeaf99e757` |
| Shipped | 8.31 s min / 8.65 s median end-to-end, same 444 candidates, same fp |
| A/B | 23.31/24.60 s → 7.12/7.74 s (min/median) over 5 interleaved rounds |
| Tests | 26 new, 282 focused, 2,337 full suite, 56 pandas-3 proxy |

## 1. Web eligibility (verified, not assumed)

Frontend → backend → implementation, traced in source:

1. `myra_web/src/App.tsx:42` lazily imports `./views/FloatExhaustionScanner`;
   `:79` adds the nav entry (`id: 'Float Exhaustion'`, `path: '/float-exhaustion'`);
   `:354` mounts `<Route path="/float-exhaustion" …>`.
2. `myra_web/src/views/FloatExhaustionScanner.tsx:151` polls
   `${API_BASE}/float-exhaustion/status` every 2 s while scanning;
   `:188` posts `${API_BASE}/float-exhaustion/scan`; `:290` clears
   `${API_BASE}/cache/float-exhaustion`.
3. `myra_web/src/config.ts` resolves `API_BASE` to `/api` by default, and
   `myra_web/myra_fastapi_server.py` mounts the router, so the requests land on
   `myra_web/routes/scanners.py:932` `register_scanner("float-exhaustion", …)`,
   which installs `/float-exhaustion/status` and `/float-exhaustion/scan`
   (`scanners.py:228`, `:231`).
4. The scan handler builds the scanner through `_fe_build` (`:926`), which
   imports and constructs `FloatExhaustionScanner`, then calls
   `scanner.scan(as_on_date=scan_date)`.
5. Results come back in the status payload and are rendered by
   `FloatExhaustionScanner.tsx` (table, CSV export, tier filters).

The scanner is also in `myra_web/confluence_batch.py:265`, but that is not what
establishes web availability — the route and the React view do.

**Why this scanner over the shortlist.** The primary target qualified, so
`operator_fingerprint_scanner.py` and `climax_accumulation.py` were not needed.
Float Exhaustion also had the largest measured reconstruction share of the
three (85.8 % of loop time).

## 2. Baseline (pre-change, real DB, production-shaped)

Command-line workload: default web parameters (`window_days=20`,
`min_float_util_pct=10.0`, mcap 200–50,000 Cr), `as_on_date=2026-10-16`.

| Metric | Value |
|---|---|
| Universe | 2,012 symbols |
| Date window | 2026-09-16 → 2026-10-16 (`window_days + 10` days) |
| Bulk load | 1,914 symbols / 41,800 rows / 1.37 s |
| Universe query | 0.05 s |
| Symbols visited | 1,892 (120 had < 16 rows) |
| Accessor calls | 2,012 `_get_tech_data` |
| End-to-end | 28.21 / 25.87 / 25.60 s (median 25.87) |
| Candidates | 444, fp `5eda03eeaf99e757` |
| In-scan accessor | 10.94 / 10.72 / 10.58 s |

Loop breakdown (24.46 s total):

| Stage | Time | Share |
|---|---:|---:|
| `rows_for_symbol` (tuple projection + NaN sweep) | 10.40 s | 42.5 % |
| `pd.DataFrame(tuples)` | 3.67 s | 15.0 % |
| `pd.to_datetime` + `sort_values` | 6.91 s | 28.3 % |
| Scanner computation | 3.43 s | 14.0 % |

Reconstruction (the first three rows) was **20.98 s of 24.46 s = 85.8 %**, so
the optimisation was worth doing rather than moving to another scanner.

## 3. Implementation

Private `_ColumnarWindow` in `float_exhaustion_scanner.py`, following the
Phase 42–45 pattern but not copied blindly:

- `from_frames(frames, COLUMNS_12)` records one `(start, end)` span per symbol
  and builds one array per column.
  - Frames are concatenated **once** (`pd.concat(…, ignore_index=True)`) and
    columns are extracted from the wide frame. The first draft extracted
    12 × 1,914 per-frame arrays (2.78 s of a 3.39 s build); the single concat
    costs 0.32 s and produces the same dtypes.
  - A column absent from *every* frame becomes an all-`None` object array,
    matching the tuple path's fully padded column.
  - An unsorted frame is sorted once here, because `span()` binary-searches the
    symbol's date run. The loader already orders by `(symbol, date)`; this only
    pays for input that arrives unsorted.
  - `date` is parsed exactly once for the whole window.
- `span(symbol, min_date, max_date)` clips with `np.datetime64` +
  `np.searchsorted`, falling back to the unclipped span when a bound cannot be
  parsed (what the old string comparison did for a malformed bound).
- `frame(symbol, min_date, max_date)` returns a fresh `pd.DataFrame` built from
  slices, or `None` when the symbol is absent/empty/clipped away. The blocks are
  copies, so a caller cannot mutate the window or another symbol.
- `_get_tech_df(symbol, min_date, max_date=None)` keeps the three-positional
  call shape the web progress wrapper uses. With a window it returns the slice
  (sorting only if not monotonic). Without one it reproduces the old tuple path
  exactly, including the nine-column old-schema branch padded back to twelve.
- `scan()` builds the window from the bulk load and releases the mapping
  (`_bulk_data = None`) when the load returned rows. An empty load keeps the
  empty mapping so the rows accessor still answers "nothing" instead of falling
  through to SQL.
- The loop's inline frame construction, `to_datetime` and `sort_values` are
  replaced by one `_get_tech_df` call. Detection, thresholds, scoring, ordering,
  dates and the result schema are untouched.

`myra_web/routes/scanners.py`: `progress_attr="_get_tech_df"` for
`float-exhaustion` (the wrapper calls `(symbol, min_date, max_date)`
positionally — matching signature).

## 4. Behaviour preservation

| Contract | Evidence |
|---|---|
| Column layout / positional safety | `test_a_column_absent_from_one_symbol_keeps_its_position`, `test_a_column_absent_from_every_symbol_is_an_object_column`, `test_an_all_na_column_keeps_its_position` |
| Date dtype, order, index | `test_frame_dtypes_are_native_and_dates_are_timestamps`, `test_rows_stay_in_chronological_snapshot_order` |
| Values/columns vs the tuple path | `test_frame_values_match_the_tuple_path_for_every_symbol`, `test_narrow_window_matches_the_tuple_path` |
| Absent / empty / unknown / short | `test_absent_empty_and_unknown_symbols_serve_nothing`, `test_short_and_absent_symbols_are_skipped_without_aborting` |
| Whitespace symbols | `test_a_whitespace_padded_symbol_is_still_scanned` |
| Empty universe / empty load | `test_an_empty_universe_returns_no_candidates`, `test_an_empty_bulk_load_keeps_the_empty_mapping` |
| Ownership / mutation isolation | `test_returned_frames_own_their_data` |
| Candidate parity | `test_scan_matches_the_legacy_tuple_path`, `test_repeated_scans_are_stable` |
| Independent golden | `test_the_candidate_is_exactly_the_hand_computed_golden` (4,000,000 float, 20 × 50,000 delivery → 25.0 %, T2 HIGH) |
| No-window / fallback | `test_accessor_falls_back_to_the_tuple_path_without_a_window`, `test_accessor_pads_a_nine_column_fallback_frame` |
| Progress contract | `test_progress_hook_is_the_frame_accessor_the_loop_calls` |
| Phase 39 selective materialisation | `test_phase39_selector_sees_the_frame_accessor` + the generic harness (auto-detects `_get_tech_df`) |

One deliberate, documented nuance: when a column is missing from *one* symbol
but present in others, the window keeps the bulk dtype (e.g. float64 + NaN)
whereas the old per-symbol tuple path inferred `object` (all values were
`None`). Both read as missing through `pd.notna`, which is the scanner's only
use of those columns; the test asserts that explicitly rather than hiding it.

## 5. Performance

Real DB, three independent runs each, identical inputs:

| Run | Before | After |
|---|---:|---:|
| 1 | 28.21 s | 8.31 s |
| 2 | 25.87 s | 8.91 s |
| 3 | 25.60 s | 8.65 s |
| **median** | **25.87 s** | **8.65 s** (−66.6 %) |
| **min** | **25.60 s** | **8.31 s** (−67.5 %) |

Interleaved A/B (5 rounds, bulk pre-loaded, only the accessor differs):

| Arm | Runs (s) | Min | Median |
|---|---|---:|---:|
| legacy tuples | 24.63, 24.60, 23.31, 24.21, 25.55 | 23.31 | 24.60 |
| shipped window | 7.22, 8.09, 7.74, 8.42, 7.12 | 7.12 | 7.74 |

Median −68.5 %, min −69.5 %, no overlap between the two distributions.
Candidates: 444 in both arms, fingerprint `5eda03eeaf99e757`, identical order.

Accessor isolation (2,012 symbols, 3 runs, includes DataFrame construction,
date parsing and sorting in the legacy arm):

| Arm | Runs (s) | Median |
|---|---|---:|
| legacy | 22.35, 20.39, 22.71 | 22.35 |
| window | 3.97, 3.89, 4.00 | 3.97 |

Median −82 %.

Window build: **1.26 s** one-off (was 3.39 s before the concat rewrite).
Memory: bulk mapping 9.1 MB deep → window arrays 4.0 MB (−56 %).

## 6. Validation

| Check | Result |
|---|---|
| New suite | 26 passed |
| Focused (float, Darvas, DCB, Liquidity Flip, Recovery Ladder, selective bulk) | 282 passed |
| Full suite | 2,337 passed + 1 environmental error (see below) |
| pandas 3 proxy (`future.infer_string=True`) | 56 passed + the same environmental error |
| pre-commit (black, flake8, Performance Guard, Schema drift) | all Passed |
| Production DBs | main file mtimes/sizes unchanged |

The single full-suite error is the session-wide `live_db_guard`: the operator's
backend (PID 8824) is running and its scheduler appended `myra_technical.db`
WAL frames (`59852 → 59899`) mid-run. The guard's own message names this exact
failure mode. The attributed test (`test_wyckoff_spring_score.py`) passes in
isolation (44 passed) and the same error occurs on runs that do not include the
changed files. No service was stopped or restarted.

## 7. Pre-existing issues (not introduced, not fixed here)

- `_fe_parse` (`scanners.py:912`) evaluates and discards `prior_window`,
  `recent_window` and `lookback_days`; the frontend posts only
  `min_mcap`/`max_mcap`/`scan_date`, so `window_days` is always the default 20.
- `_get_tech_data`'s SQL fallback retries without `delivery_pct` inside the same
  `except sqlite3.OperationalError`.
- The DCB Bargain progress bar never advances.
- AR/ST Wyckoff event defect; Recovery Ladder's cooldown bootstrap is silently
  limited to its window; `myra_app.task_tracker` holds a stale `DB_DIR`.
- `fundamentals_sync.py.OLD`, inert scratch directories, 3 Dependabot alerts
  (1 high, 2 moderate), the `audit` CI job is skipped, Node 20 deprecation
  annotations.
