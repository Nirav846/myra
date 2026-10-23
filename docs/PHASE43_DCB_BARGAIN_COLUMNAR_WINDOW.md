# Phase 43 — DCB Bargain: columnar window for the per-symbol frame

Status: implemented and measured. One production file touched
(`myra_app/strategies/dcb_bargain.py`), plus one new test module and six
existing DCB tests re-pointed at the real data seam.

## 1. What changed

| File | Change |
|---|---|
| `myra_app/strategies/dcb_bargain.py` | New module-private `_ColumnarWindow` (~70 lines); `DCBBargainScanner._columnar` class attribute; new `_get_tech_df()` accessor; `scan()` builds the window from the slice and releases `_bulk_data`; the detection loop reads through `_get_tech_df()` instead of calling `get_df_for_symbol()` directly. |
| `tests/test_dcb_columnar_window.py` | New — 20 tests over the window contract and the scanner's output parity. |
| `tests/test_dcb_bargain.py` | Six scan-level tests now inject `{"TEST": tech_df}` through `load_ohlcv_for_universe` instead of mocking `get_df_for_symbol`; the mocked seam no longer exists. Assertions unchanged. |

Black 26.10.1 (pinned in `.pre-commit-config.yaml`) additionally normalised two
pre-existing formatting sites in `dcb_bargain.py` (a `conn.execute("""…""")`
call and one dict value). Mechanical only, no behaviour.

Nothing else was touched: Liquidity Flip, `db/bulk_loader.py`, the web layer,
schemas, thresholds, scoring and date rules are unchanged.

## 2. Baseline before the change

Real universe, `as_on_date="2026-10-16"`, unmodified scanner, 3 runs:

- universe 2,012 symbols; slice 1,922 symbols / 756,770 rows
- `total_calendar` = 677 days → `min_date` 2024-12-08
- `get_df_for_symbol` called 2,012 times, 14.71–16.02 s of the run
- E2E 41.31 / 32.74 / 31.76 s (min 31.76, median 32.74; run 0 includes the cold read)
- 32 candidates, fingerprint `f680de6df7d7ce4`

So ~46 % of the run was spent re-selecting and copying a small frame per symbol
out of a slice the scanner had already loaded in full.

## 3. Data-access trace (both call sites)

| Site | Role | State after this phase |
|---|---|---|
| `scan()` loop (`get_df_for_symbol`, was line 622) | the live hot path, one call per symbol | now `self._get_tech_df(...)` |
| `_get_tech_data` (was line 139) | tuple-shaped analogue; **no callers** anywhere in `myra_app/`, `tools/`, `tests/` or `myra_web/` | shares `_get_tech_df()`; still returns `list[tuple]` in `COLUMNS_8` order |

Both sites previously materialised a frame from the same slice with the same
eight columns, so one window can serve both — no second representation is
introduced. The tuple branch of `_get_tech_data` (`df.itertuples(...)`) is kept
verbatim, including its original `[]`-when-absent contract.

DCB is endpoint-only (`myra_web/routes/scanners.py:458 _dcb_build`); it does not
appear in `myra_web/confluence_batch.py`, so it never runs inside a batch
snapshot. `scan()` performs its own `load_ohlcv_for_universe` call.

## 4. Design

`_ColumnarWindow.from_frames(frames, columns)` concatenates each requested
column into one contiguous array and records `spans[symbol] = (start, end)`.
`frame(symbol)` slices those arrays, builds one DataFrame and restores
`datetime64` on `date`; an absent or empty symbol returns `None`.

Two deliberate differences from the Phase 42 copy in
`liquidity_flip_detector.py`:

- **No sort / `reset_index`.** `load_ohlcv_for_universe` already emits
  `(symbol, date)` ordered frames (`_read_bulk` orders by `symbol, date`), and
  every DCB consumer is positional: `iloc[idx]`, `df_daily.iloc[-1]`,
  `window_df.iloc[-effective_window:]`, `.tail()`, `.values`. Adding a sort would
  be a silent behaviour change for no gain.
- The docstring states the real invariant: `_read_bulk` projects **one** column
  list for the whole slice, so per-symbol column sets are uniform and the
  "drop a column missing from any frame" rule is defensive only.

The scanner keeps exactly one representation alive: the window is built
immediately after the load and `_bulk_data` is set to `None` in the same block.

`_get_tech_df()` never returns `None`; it returns an empty frame, so the loop's
length guard stays a single expression.

## 5. Behaviour preserved

| Hazard | Resolution |
|---|---|
| A — missing column | `_read_bulk` selects one column list for all symbols, so frames are uniform; the intersection can only differ for hand-built dictionaries. Window-level test pins the rule; `delivery_pct` (used by `_compute_del_abs`, `_check_spike_deep`, the discount window, weekly aggregation) is never dropped by a real load. |
| B — all-NA column | Column retained with its dtype; scan output compared against the legacy arm on a slice containing an all-NA `delivery_pct` symbol — identical frames and fingerprint. |
| C — order / index / label dependence | No sort, no reindex. Row order equals the reference accessor's, asserted per symbol. |
| D — shared buffers | `np.shares_memory` is `False` for every column; mutating a returned frame (values *and* the date column) leaves later frames and sibling symbols untouched. |
| E — absent / empty / short history | Absent symbol → `None` → `frame_too_short_skipped`; empty frame skipped at build time; 40-row symbol skipped, 200-row sibling still produces a candidate. |

Candidate dict, score formula, tiering, filters and ordering are untouched, so
the only thing that can differ is the frame's provenance.

## 6. Tests

`tests/test_dcb_columnar_window.py` (20 tests, written before the source change).
Against the pre-change file the module cannot even be collected
(`ImportError: _ColumnarWindow`), because neither the window nor `_get_tech_df`
existed; behavioural equivalence is therefore evidenced by the legacy-arm A/B
(section 7) rather than by a green-then-red run. The five failures seen on the
first run *after* the change were my own fixture assumptions (kept label after a
boolean filter, tuple-vs-list for `columns`, and the documented whole-slice
column rule) — corrected in the tests, not worked around in the source.

- window ↔ `get_df_for_symbol` parity for **every** symbol in a 20-symbol mixed
  slice, and on a narrow date slice
- column order/dtype/`datetime64`/RangeIndex/row-order preservation
- column absent from one frame → dropped for the slice, consistently
- all-NA column retained and still equal to the reference frame
- absent symbol, empty frame, `from_frames({})`
- no shared memory; mutation isolation; idempotent re-materialisation
- scan-level: identical candidates against a legacy arm
  (`_get_tech_df` → `get_df_for_symbol`), identical fingerprint, repeatable
  across runs, `_bulk_data` released while the window keeps the data, score /
  discount identities (`0.6·discount + 0.4·del_abs`, `(dcb − close)/dcb·100`),
  short-history skipping, ghost symbol absent from the slice, all-NA parity,
  empty accessor with no snapshot (`_get_tech_df` only — see section 10 for
  why the tuple accessor is not exercised there)
- `_get_tech_data` still returns plain tuples in column order

Golden expectation: the score and discount identities above are computed from
the emitted candidate row itself, and the whole candidate frame is compared
field-by-field (dtypes included) with the legacy arm — no self-referential
assertions.

Suites:

- full suite **2,271 passed** (385.34 s)
- DCB + LF Phase 41/42 + Phase 39 focused set: 141 passed under the pandas-3
  `future.infer_string` proxy (pandas 2.2.3)
- `pre-commit run --files` green: black, flake8, performance guard, schema drift

## 7. Measurements

Slice: 1,922 symbols / 756,770 rows, `COLUMNS_8`, 677-day window.

Accessor only, 3 interleaved rounds:

| | min | median | all |
|---|---|---|---|
| window build | 0.437 s | 0.445 s | 0.445, 0.493, 0.437 |
| `get_df_for_symbol` ×1,922 | 13.27 s | 16.04 s | 18.23, 16.04, 13.27 |
| window `frame()` ×1,922 | **8.00 s** | **8.55 s** | 10.75, 8.55, 8.00 |
| build + materialise | 8.44 s | 8.99 s | −36 % min / −44 % median |

Parity: 1,922 / 1,922 frames identical, column order included.

End to end, 5 interleaved rounds of the **shipped** `scan()` (only the frame
source differs):

| arm | min | median | all (s) |
|---|---|---|---|
| legacy accessor | 32.23 s | 32.69 s | 43.40, 32.69, 32.66, 32.23, 37.20 |
| columnar window | **25.79 s** | **29.54 s** | 30.42, 29.54, 27.56, 30.87, 25.79 |
| delta | +6.43 s (−20.0 %) | +3.15 s (−9.6 %) | arms do not overlap |

Both arms: 32 candidates, fingerprint `f680de6df7d7ce4` — identical to the
pre-change baseline and to each other, symbol order included. The 43.40 s first
legacy round is the cold disk read; it is excluded from neither column, and the
arms still do not overlap.

Memory (deep): the slice dictionary costs 160.1 MB whole-frame / 87.3 MB for the
eight columns alone; the window costs 87.0 MB (48.4 MB numpy + ~38.6 MB of
object-dtype date strings). Steady state after the build: 87.0 MB instead of
160.1 MB (−45.7 %). Peak is higher for ~0.44 s while both representations are
alive (~247 MB), which is the price of the one-pass build.

## 8. Decision gate

Accessor −36 % min / −44 % median, E2E −20 % min / −9.6 % median, zero output
change, steady memory down 45.7 %, and the added surface is one private class
plus one accessor. **Accepted.**

Honest caveat: the E2E median gain (−9.6 %) is smaller than the accessor gain
because the remaining ~17 s of the run is detection work this phase does not
touch, and the shipped arm has a 5 s spread. The min-to-min comparison and the
non-overlapping ranges are the load-bearing evidence, not the median alone.

## 9. Compatibility and lifecycle

- `_get_tech_data` keeps its signature and tuple shape; `_get_tech_df` is new.
- The `_bulk_data is None` early return in `scan()` is unchanged and still
  tested (`test_scan_returns_empty_when_bulk_data_is_none`).
- The dictionary branch inside `_get_tech_df` remains for callers that set
  `_bulk_data` themselves (the pattern used by `tools/calibrate_wyckoff_weights.py`
  for other scanners); after `scan()` returns, `_columnar` serves frames and
  `_bulk_data` is `None`.
- A window is rebuilt per scan, so a scanner instance reused across dates cannot
  serve a stale slice; `test_scan_is_repeatable_across_runs` covers it.

## 10. Remaining work (not this phase)

- `_ColumnarWindow` now exists twice (Liquidity Flip, DCB). Extracting it into a
  shared private module is the obvious next step, but doing it here would have
  meant editing Liquidity Flip, which this phase forbids.
- **Pre-existing UI defect:** `register_scanner()` defaults `progress_attr` to
  `"_get_tech_data"` and patches that attribute, but DCB's `scan()` calls
  `_get_tech_df` — so the DCB progress bar never advances past 10 %.
  Unchanged by this phase; noted, not fixed.
- **Pre-existing:** a symbol whose `delivery_pct` is entirely NULL makes the
  discount window call `np.nanmean` on an empty slice (RuntimeWarning). Same in
  both arms; the test that exercises it marks the warning explicitly.
- **Pre-existing:** \_get_tech_data\'s SQL fallback retries without
  \delivery_pct\ inside the same \xcept sqlite3.OperationalError\ block, so a
  database that lacks \	echnical_data\ outright raises instead of returning
  \[]\. CI has such a database; the new test was narrowed to the accessor
  rather than changing the fallback (out of scope).
- The other ten batch scanners still materialise frames from tuples per symbol.

## 11. Verification

```
pytest                    2271 passed, 385.34 s
pandas-3 proxy (141 tests) 141 passed
pre-commit                 black / flake8 / perf guard / schema drift: Passed
```

The first push (\773364\) failed CI on one new test that indirectly
exercised the unguarded SQL fallback described in section 10 (CI has no
\	echnical_data\ table); the test was narrowed to the accessor and the suite
went green locally, then on CI (2,250 passed / 21 skipped in 70.66 s).

Database mtimes unchanged after all benchmarks and tests
(`myra_technical.db` 2026-10-18 21:31:01, `myra_valuation.db` 2026-10-18
22:01:02, `myra_metadata.db` 2026-10-21 13:48:56). All measurement ran read-only
outside production services; no refresh, backfill, scheduler or batch job was
started.

Pushed as \773364\ (implementation) and \718e315\ (test narrowing).
\HEAD == origin/main == 718e315\, working tree clean. CI run 39931593146
success (2 m 17 s), CodeQL run 39931592669 success (2 m 4 s).
