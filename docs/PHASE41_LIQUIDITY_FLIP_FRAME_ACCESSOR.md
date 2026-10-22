# Phase 41 — Liquidity Flip: frame accessor instead of the tuple round-trip

Follows `docs/PHASE40_COLUMNAR_SNAPSHOT_AUDIT.md`, whose decision was
**prototype one consumer** before considering any columnar-store rework.
Scope was deliberately one scanner; `bulk_loader.py` was not touched.

## 1. Change

`myra_app/strategies/liquidity_flip_detector.py` only:

- added `_get_tech_df()`, the same idiom already used by `trigger_scanner`,
  `bottom_hunter`, `invisible_hand_scanner` and `dcb_bargain` — bulk window →
  `get_df_for_symbol`; otherwise the untouched per-symbol SQL path plus the
  pre-existing 12/9-column reconstruction;
- `scan()` now reads `df` from that accessor. The `col_count` branch, the
  `pd.to_datetime` call, the `sort_values("date")` and the `reset_index` moved
  into the accessor, where they apply to both branches.

The tuple rebuild (`pd.DataFrame(tech, columns=[...])` + `to_datetime` + sort +
reset) cost 14.16 s of the Phase 40 26.42 s measurement, i.e. more than the
accessor call itself.

## 2. Contract hazards (from Phase 40 §5–§6)

**A — absent optional columns.** `rows_for_symbol` pads a column missing from the
live schema with `None`; `get_df_for_symbol` drops it. Left alone, the scan's
`df["high_52w"]` read would raise `KeyError` per symbol.

Resolved explicitly: `_get_tech_df` re-adds `sma_50` / `high_52w` / `low_52w` as
`NaN` when they are missing. This is faithful, not cosmetic — the scan reads the
52-week pair behind `pd.notna(...)` and falls back to `df["high"].max()` /
`df["low"].min()`, and an all-`NaN` column takes exactly that branch
(`pd.notna(None)` and `pd.notna(nan)` are both `False`). `sma_50` is never read.

Verified end to end: on the drifted-schema fixture the absent-column run is
`assert_frame_equal`-identical to the all-`NaN` run, and both use the in-window
high/low band (`wk52_pos = 50.0`) rather than the real 52-week band (71.4).

**B — index labels and dtypes.** `get_df_for_symbol` neither sorts nor resets the
index; it slices with `.loc[mask]`. Every consumer erases both one line later,
and this scan is purely positional (`.iloc[-1]`, `.tail()`, `.values`). The
ordering and the zero-based index are now pinned inside `_get_tech_df` rather
than inherited from the snapshot's build order.

For this scanner the labels were already `0..n-1` in the bulk path: it asks for
exactly the window the batch scope materialised, so the mask selects every row.
A relabelled (500-based) snapshot produces identical candidates, which is
asserted.

## 3. Tests

New `tests/test_liquidity_flip_df_accessor.py` — 20 tests, written first
(17 failed / 3 passed pre-change, 20 passed after):

- the bulk branch calls `get_df_for_symbol` and never `rows_for_symbol`;
- **parity**: 6 fixture cases run through the migrated scanner and through a
  test-local oracle that reproduces the pre-Phase-41 recipe via
  `rows_for_symbol` (a different function, so a shared accessor defect cannot
  make the comparison vacuous); frames must be `assert_frame_equal`-identical;
- **golden**: one hand-computed STRONG FLIP candidate (prior 20.0, recent 65.0,
  jump 45.0, 150.0 Cr, `wk52_pos` 71.4, score 71.4, grade A, High) so at least
  one assertion does not compare two execution paths;
- hazard A: absent ≡ all-`NaN` ≡ fallback band; no exception;
- hazard B: sorted, zero-based index, `datetime64` date, 12-column contract,
  independent copy (mutating the returned frame does not touch the bulk window);
- domain: whitespace entry found, absent symbol / empty frame / short history
  skipped, every universe symbol visited once, ordering still score-descending.

`tests/test_selective_bulk_universe.py` — the shared harness recorded
`_get_tech_data`; it now records `_get_tech_df` for frame consumers and keeps the
tuple recorder for the other four scanners. Its 30 assertions are unchanged.

## 4. Measurements

One prewarmed `batch_snapshot_scope` (prewarm 27.95 s), real universe, both arms
on the same slice, best of two, read-only:

| arm | run 1 | run 2 | output |
|---|---|---|---|
| legacy (tuple rebuild) | 32.00 s | 32.70 s | `9ac001e004b9f4d8`, 15 rows |
| migrated (frame accessor) | 22.70 s | 24.24 s | `9ac001e004b9f4d8`, 15 rows |

−29 % on the scanner, and the fingerprint matches the Phase 40 liquidity-flip
fingerprint exactly, so the candidate set is unchanged on real data.

## 5. Gates

- focused: 20 new + 30 Phase 39 + 105 bulk-loader = green;
- pandas-3 proxy (`pd.options.future.infer_string = True`): 50 passed;
- full suite: **2237 passed** in 332.8 s (was 2217);
- `pre-commit` (black 26.10.1, flake8, performance guard, schema drift): all pass;
- production DB mtimes unchanged (`myra_technical.db` 2026-10-18 21:31:01,
  `myra_valuation.db` 2026-10-18 22:01:02, `myra_metadata.db` 2026-10-21 13:48:56).

## 6. Not done (and why)

- No columnar-store integration, no `bulk_loader.py` change: Phase 40's
  conclusion was to prove one consumer first, and it holds — the remaining
  columnar gain (10.75 s vs 12.26 s accessor) is smaller than the tuple
  overhead this phase removed.
- The other 10 rows-consumers were not migrated; each needs its own hazard
  check (Wyckoff's 13-value padding is already pinned by
  `tests/test_wyckoff_schema_fallback.py`).
- No scanner logic, ranking, schema, ingestion or dependency changes.
