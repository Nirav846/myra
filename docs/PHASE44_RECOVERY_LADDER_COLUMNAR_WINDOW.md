# Phase 44 — Recovery Ladder: columnar window, arrays straight to the loop

Scanner: **Recovery Ladder** (`BottomHunterM1Scanner`,
`myra_app/strategies/bottom_hunter_m1_scanner.py`).
Pattern: the Phase 42/43 columnar window, applied to the last scanner that still
rebuilt a twelve-column tuple list per symbol for a loop that reads four columns.

## Why this scanner

`load_ohlcv_for_universe` hands the scan a `{symbol: DataFrame}` slice, the scan
kept that dictionary on `self._bulk_data`, and every symbol then went through
`rows_for_symbol()` — a fresh boolean filter over a frame, a column projection, a
NaN→None sweep and a tuple list — only for the loop to rebuild three arrays from
four positions (`date`, `low`, `close`, last `delivery_pct`). With 500 symbols in
a live 545-day window that is ~181,800 mutated rows per scan, none of which the
detection math ever needs.

## Design (Option C — arrays direct, no frame materialisation)

`_ColumnarWindow` (module-private) is built **once** from the load result:

* `from_frames(frames, columns)` concatenates one array per column across the
  slice, records `spans[symbol] = (start, end)` and skips zero-row frames. A
  column missing from *one* frame is padded with `None` for that frame only —
  Recovery Ladder indexes by position (`row[4]` is the close), so dropping a
  column would silently shift every field after it. This mirrors
  `rows_for_symbol`'s per-symbol `_pad_rows` fallback.
* `span()` clips with `np.searchsorted` on ISO date strings, guarded by a
  cheap "does the request actually cover the stored span" check and an
  `isinstance(dates[start], str)` check, so non-string dates (hand-built
  fixtures) serve the whole span instead of raising.
* `rows()` reproduces the `rows_for_symbol` tuple contract exactly — including
  the NaN→None sweep — because `_bootstrap_from_full_history` and
  `scan_near_trigger` still consume tuples.
* `bars()` returns `(closes, lows, dates, delivery_pct)` and **copies** the
  arrays, so a caller cannot mutate what the next symbol will be served.

Wiring:

* `scan()` builds the window right after the load and sets `self._bulk_data = None`
  so only one representation of the slice stays live (the dictionary only ever
  held that same slice, so this is equivalent, not lossy).
* `_get_tech_data()` gained a window branch ahead of the dictionary/SQL branches,
  leaving every other caller's contract untouched.
* `_get_tech_bars()` is new: `(closes, lows, dates, delivery_pct)`, from the
  window when present, otherwise built from `_get_tech_data()` rows exactly the
  way the loop used to build them. The no-window path is therefore byte-identical
  to the previous loop input.
* The loop calls `_get_tech_bars(symbol, min_date, as_of)` and gates on
  `len(closes) < lookback + 1`; `last_delivery_pct` is computed once via a new
  `_delivery_pct_value()` (former `_last_delivery_pct` body, now shared).
* `progress_attr` on the registration (`myra_web/routes/scanners.py`) moved from
  `_get_tech_data` to `_get_tech_bars`: the progress wrapper is installed on the
  attribute the loop actually calls, and it passes `(symbol, min_date, max_date)`
  positionally, which is the new signature.
* No sort, reindex, dtype coercion, threshold, scoring, date or schema change.
  `detect_events` is untouched.

## Evidence

### Parity

| Check | Result |
|---|---|
| Live scan (500 symbols, 545-day window), legacy arm vs shipped, 5 interleaved rounds | identical frame (`check_dtype=True`), 1 candidate `HINDALCO`, fingerprint `99274c4df9b729c0` — same as the pre-change baseline |
| Back-dated scan (`as_on_date=2026-06-30`, top_n=120, full-history window) | identical (0 candidates both arms) |
| Full-history span (`min_date=1995-01-02`, 500 symbols, 1,999,750 rows) | `rows()` **and** `bars()` equal to `rows_for_symbol` + the loop's own array build for all 500 symbols |
| Narrow window inside the same slice (`>= 2026-01-01`) | parity for all 500 symbols (date-clipping path) |
| Fixture-level | 19 new tests: tuple parity per symbol, narrow windows, chronological order, absent-column None padding in place, all-NA column, absent/empty/short symbols, `from_frames({})`, memory independence, scan vs legacy arm, golden candidate fields, short-history/ghost-symbol skipping, cooldown persistence, no-window fallback, progress-hook call shape |

The reference arm is not the new code: it injects the pre-change per-symbol work
(`rows_for_symbol` + tuple→array build) as `_get_tech_bars`, so both arms share
universe, slice, detection loop and cooldown state.

### Speed (5 interleaved rounds, same session, 500 symbols / ~181,800 rows)

| | legacy arm | shipped | delta |
|---|---|---|---|
| end-to-end, min | 8.18 s | 4.72 s | **−42.3 %** |
| end-to-end, median | 8.30 s | 4.86 s | **−41.4 %** |
| per-symbol accessor, min | 3.39 s | 0.03 s | −99.1 % |
| per-symbol accessor, median | 3.46 s | 0.03 s | −99.1 % |

Ranges never overlap (legacy 8.18–9.36 s, shipped 4.72–5.20 s). The Phase-44
entry baseline measured 11.28 / 13.40 s for the same scan; that gap is machine
and cache state, which is why the ratio above is measured in one session with
both arms interleaved rather than against the earlier number. Phase 40's 71.2 %
figure for this scanner is historical context only and is **not** claimed here.

### Memory

Window for this slice: 26.7 MB, of which 17.5 MB is numpy payload, versus a
26.8 MB dictionary (`memory_usage(deep=True)`, 12 columns) / 38.4 MB including
frame overhead — roughly break-even, because a column of 181,800 rows costs the
same either way. The gain here is time, not peak memory. (`psutil` is absent in
this environment; these are pandas deep-usage numbers.)

### Contract / regression checks

* Full suite: **2,290 passed** (353.92 s) — the 2,271 of Phase 43 plus the 19 new
  tests.
* pandas-3 proxy (`pd.options.future.infer_string = True`): 101 passed across the
  Recovery Ladder, DCB and Liquidity Flip columnar suites.
* `pre-commit run --files` green (black, flake8, perf guard, schema drift guard).
* `git status` in the four production DBs shows unchanged mtimes
  (technical `2026-10-18 21:31:01`, valuation `2026-10-18 22:01:02`,
  metadata `2026-10-21 13:48:56`, scoring `2026-10-17 01:41:25`) — read-only.
* No ingestion, refresh, backfill, lake build, EOD sync, scheduler or model write
  was run.

## Notes carried forward (not changed here)

* `_bootstrap_from_full_history` asks for `1900-01-01` but the slice it reads was
  loaded from the 545-day live window, so the "full-history" bootstrap is
  silently window-limited. Pre-existing; fixing it changes live cooldown seeding
  behaviour and needs its own decision.
* `scan_near_trigger` still consumes tuples (`_get_tech_data`), now served from
  the window instead of `rows_for_symbol` — measured 1.64–1.73 s vs 6.72–7.90 s
  for the tuple path over 500 symbols, so it got faster without a signature
  change.
* `scanners.py` needed two black-only hunks (lines ~735 and ~1419) on
  pre-existing lines for the pinned black 26.10.1 hook to pass; they are
  mechanical and unrelated to this change.
* Still open from earlier phases: DCB progress bar never advances; the
  `_get_tech_data` SQL fallback retries without `delivery_pct` inside the same
  `except sqlite3.OperationalError`; unreconciled historical scanner timings.

## Files

* `myra_app/strategies/bottom_hunter_m1_scanner.py` (+~200/−~15)
* `tests/test_bottom_hunter_m1_columnar.py` (new, 19 tests)
* `myra_web/routes/scanners.py` (one line + two black hunks)
