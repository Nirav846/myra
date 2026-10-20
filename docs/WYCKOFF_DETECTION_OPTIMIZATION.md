# Wyckoff detection optimisation

Scope: `WyckoffAutomaton._detect_events()` — the measured bottleneck of the
Wyckoff scan. One function, one call site, no behaviour change.

---

## 1. Root cause

`_detect_events` runs a 90-candle loop per symbol and read every candle field
through pandas scalar access:

```python
row = scan_df.iloc[i]          # materialises a Series per candle
open_p = float(row["open"])    # 7 Series lookups per candle
...
float(exp_avg_vol.iloc[abs_i]) # 4 more, plus slices for prior_low / delivery
```

cProfile over 80 real symbols (26.4 s total, pre-change):

| Cost centre | Time | Share | Calls |
|---|---|---|---|
| `.loc` / `__getitem__` | 13.68 s | 52 % | 38,290 (~479/symbol) |
| row-Series `fast_xs` | 4.71 s | 18 % | — |
| `isinstance` | 2.26 s | 10 % | 732,000 |
| `_detect_events` own bytecode | 1.73 s | 7 % | — |

So the scan was not I/O- or compute-bound: it was **pandas call overhead**.
Two smaller hotspots sat underneath:

- `df["low"].iloc[abs_i-60:abs_i].min()` and
  `df["delivery_pct"].iloc[abs_i-50:abs_i].mean()` — a slice plus a reducer
  per candle, for windows that never change between symbols' neighbouring
  candles.
- `df["delivery_pct"].values.astype(float)` — a full-column float copy per
  candle, only to average a 50-element tail of it.

---

## 2. Files and changes

| File | Change |
|---|---|
| `myra_app/strategies/wyckoff_automaton.py` | +60 / −30 in `_detect_events`; new module-level `_window_mean` helper |
| `tests/test_wyckoff_detect_parity.py` | new — 40 tests (golden parity + `_window_mean` branch coverage) |
| `tests/fixtures/wyckoff_detect_golden.json` | new — 12 cases, 126.7 KB, captured from the pre-change implementation |

### 2.1 Column extraction (the 52 %)

Every column the loop reads is pulled out of the frame once, before the loop:

```python
dates = [str(v) for v in df["date"]]
opens = df["open"].to_numpy()
...
avg_vol_a = exp_avg_vol.to_numpy()
```

`dates` iterates the Series (yielding `Timestamp` objects) so `str(v)` produces
the same `"YYYY-MM-DD 00:00:00"` string as `str(row["date"])`. Array indexing
returns the identical values the `.iloc` calls did.

### 2.2 Windows precomputed instead of re-sliced

- `prior_low_a = df["low"].rolling(60, min_periods=1).min().shift(1).to_numpy()`
  — the trailing low exclusive of the current candle. `min` performs no
  arithmetic, so this is exact by construction.
- The delivery window is sliced once per candle and shared between the Spring
  gate (`avg_del_50d`) and the Spring score (`avg_del_50`), which previously
  computed the same quantity twice from two different full-column copies.

### 2.3 `_window_mean` — removing a 33 % residual

After 2.1/2.2, a re-profile showed `np.nanmean` at **0.752 s of 2.289 s (33 %)**
— about 170 µs to average 50 floats, because it scans the window for NaN and
copies it. The helper keeps the exact NaN semantics but takes a fast path when
the column has no NaN at all (the normal case — enrichment is 99.4 % complete):

```python
if values.size == 0:        return float("nan")
if not has_nan:             return float(values.mean())   # bit-identical
if np.isnan(values).all():  return float("nan")
return float(np.nanmean(values))
```

`ndarray.mean()` and `np.nanmean()`'s NaN-free branch both reduce with the same
`add.reduce` and divide, so the value is identical, not merely close.

### 2.4 Deliberately not touched

The post-loop AR/ST block (`df[df["date"].astype(str) == ...]`, `iterrows`) is
left byte-for-byte as it was. See §6.1: it is unreachable, and rewriting dead
code risks changing which events it would emit if it ever became reachable.

---

## 3. Before / after timings

Method: same machine, same database, same interpreter, same session. The
"before" numbers were taken immediately before editing and the "after" numbers
immediately after, on warm caches, because this box varies ~17 % run to run
(two earlier full scans of identical code measured 281.92 s and 240.09 s).
Only same-session pairs are compared against each other.

### 3.1 Detection only — 182 real symbols, identical frames, 3 repeats

The frames are built once outside the timer, so this isolates `_detect_events`.

| Stage | Repeats (s) | Median | ms/symbol | Speed-up |
|---|---|---|---|---|
| Before | 21.09 / 20.26 / 30.84 | **21.09 s** | 115.9 | — |
| After 2.1 + 2.2 | 4.40 / 4.52 / 5.09 | **4.52 s** | 24.8 | 4.67× |
| After 2.3 | 2.68 / 3.01 / 3.09 | **3.01 s** | 16.5 | **7.0×** |

Event-stream SHA-256 identical at every stage: `a050123e0bbfe665…`, 472 events.

### 3.2 Full scan — 1,400 candidates, whole universe

| Stage | Wall clock | Saved | Speed-up | Candidate hash |
|---|---|---|---|---|
| Before | **204.21 s** | — | — | `3a7e2b8b8a5b67e1…` |
| After 2.1 + 2.2 | **81.68 s** | 122.53 s | 2.50× | `3a7e2b8b8a5b67e1…` |
| After 2.3 | **61.79 s** | **142.42 s** | **3.31×** | `3a7e2b8b8a5b67e1…` |

Identical hash at every stage means the same 1,400 rows with the same fields.

### 3.3 What the residual is

Post-change cProfile (73 symbols, 2.289 s, shares only — cProfile inflates
absolutes): `DataFrame.__getitem__` 15 %, `rolling._apply` 10 %, the dead AR/ST
boolean mask 7.5 %. `_detect_events`' own bytecode is now the largest single
entry, i.e. the remaining cost is close to the irreducible per-candle work.

---

## 4. Golden parity evidence

`tests/fixtures/wyckoff_detect_golden.json` was generated by running the
**unmodified** implementation over 12 hand-built cases and storing the complete
event list — every field, in order, floats included. The parity test asserts
full structural equality (`actual == expected`), not counts.

| Case | Rows | Events captured |
|---|---|---|
| `all_events` | 81 | SC, Spring, SOS |
| `sc_then_rally_ar_st_dead` | 85 | SC only (see §6.1) |
| `spring_only` | 82 | Spring (grade C, equal-low + two-candle paths) |
| `short_history` | 40 | none — `n < 55` early return |
| `exactly_54_rows` | 54 | none — boundary |
| `warmup_55_rows` | 55 | none — first candle of the warm-up |
| `zero_volume_candles` | 62 | 8 × SOS (zero-volume candles skipped) |
| `nan_and_non_numeric_delivery` | 72 | SC (`None`, `nan`, `"not-a-number"` all coerced) |
| `no_swing_low_column` | 82 | Spring (fallback `has_swing=False` path) |
| `all_nan_swing_low` | 82 | Spring (`pd.notna` rejection) |
| `never_triggers` | 120 | none — flat series |
| `reverse_sorted_input` | 82 | Spring (input order is caller's responsibility) |

Event-type coverage: **SC, SOS, Spring**. `test_ar_and_st_remain_unreachable`
asserts AR/ST stay absent, so an optimisation cannot silently "repair" the dead
path (§6.1) and call it parity.

### 4.1 Substitution equivalence on real data

Beyond the synthetic fixture, each substitution was compared against the exact
expression it replaced across the whole universe:

| Substitution | Comparisons | Mismatches |
|---|---|---|
| `rolling(60).min().shift(1)` vs `.iloc[a:i].min()` | 100,377 candles | **0** |
| `np.nanmean(slice)` vs `.iloc[a:i].mean()` | 100,377 candles | **0** |
| `expanding().to_numpy()[i]` vs `.iloc[i]` | 100,377 candles | **0** |
| slice-then-`astype(float)` vs column-then-slice | 100,377 candles | **0** |
| `_window_mean` (no-NaN) vs `Series.mean()` | 102,052 windows | **0** |

The universe turned out to contain no NaN `delivery_pct` at all, so the NaN
branches of `_window_mean` are covered synthetically instead:
`test_window_mean_matches_pandas_mean` (9 shapes, including partial and all-NaN),
`test_window_mean_empty_window_is_nan`, and
`test_window_mean_fast_path_is_bit_identical_to_nanmean` (200 random windows).

---

## 5. Tests run

- `pytest tests/test_wyckoff_detect_parity.py` → **40 passed** (3.70 s)
- `pytest` (full suite) → **2126 passed** (351.68 s), up from 2086 + 40 new
- `black --check` and `flake8` on both changed files → clean
- Existing Wyckoff suites (`test_wyckoff_calibration`,
  `test_wyckoff_delivery_recency`, `test_wyckoff_mcap`,
  `test_wyckoff_schema_fallback`, `test_wyckoff_spring_score`) all green
- `test_detection_does_not_mutate_input_frame` guards against the loop writing
  back into the caller's frame

Reproduce:

```powershell
.\pkscreener_env\Scripts\python.exe -m pytest tests/test_wyckoff_detect_parity.py -q
.\pkscreener_env\Scripts\python.exe -m pytest -q
```

---

## 6. Limitations

### 6.1 Pre-existing defect found, not fixed: AR and ST are unreachable

The post-loop block looks up the SC candle with

```python
sc_idx = df[df["date"].astype(str) == sc["event_date"]]
```

`Series.astype(str)` on a datetime column yields `"2025-03-12"`, while
`sc["event_date"]` is `str(Timestamp)` → `"2025-03-12 00:00:00"`. They can never
be equal, so `sc_idx` is always empty and the block always `continue`s. AR
(Automatic Rally) and ST (Secondary Test) have therefore never been emitted:

- the live `wyckoff_cache.json` contains only `SC`, `SOS`, `Spring` across 1,389
  rows;
- `sc_then_rally_ar_st_dead` is built to satisfy the AR and ST rules and yields
  SC only.

Fixing it would add two new event types to scanner output — a product change,
not a performance change, and explicitly out of scope here. It is pinned by a
test so nobody changes it by accident in either direction.

### 6.2 Other limits

- Timings are single-machine, warm-cache, and this host is noisy (~17 % run-to-
  run variance). The ratios are reliable, the absolute numbers are one sample.
- Only the Wyckoff scan was optimised. `_get_tech_data` (22.14 s / 1,818 calls),
  frame build + scoring (18.95 s) and the per-symbol vs bulk frame path
  (`get_df_for_symbol` measured 13.86 s vs 40.98 s) are untouched.
- The dead AR/ST block still does one full-column `astype(str)` per SC event
  (~7.5 % of post-change time). Removing it is safe but pointless until the
  defect in §6.1 is decided.
- No concurrency, caching or schema change was introduced.

---

## 7. Keep or revert?

**Keep.** 204.21 s → 61.79 s on a full scan (142 s per run, 3.31×) with a
byte-identical event stream, verified three independent ways: a golden fixture
of the exact pre-change output, whole-universe substitution equivalence
(100 k+ comparisons, zero mismatches), and an identical candidate hash on the
full 1,400-row scan. The change is confined to one function, removes work rather
than adding abstraction, and the new tests fail loudly if the event stream ever
drifts.
