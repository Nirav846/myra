# Phase 17 — Technical Data Provenance and Minimum Viable Screening Recovery

**Status:** evidence-gathering complete, one bounded fix applied, screening recovery **not** claimed.
**Starting commit:** `14ec4f0` (Phase 16 tip). Branch `main`, in sync with `origin/main`, working
tree clean, 3 stashes preserved.

Evidence labels used throughout, as required by the phase brief:

- **[Observed]** — read directly from code, schema, or a read-only run I executed.
- **[Inferred]** — a conclusion drawn from observations, with the reasoning stated.
- **[Assumed]** — not verified; must not be treated as fact.
- **[Decision needed]** — a choice only the operator can make.

---

## 1. Baseline

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `14ec4f0` |
| Sync | `0 0` vs `origin/main`; working tree clean; 3 stashes intact |
| `python -m myra_app.lake_build --status` | `present: false, count: 0, reason: "missing"` |
| `data/indicators/precomputed` | 0 files |
| Readiness (`data_readiness.assess()`) | `state: "blocked"`, label **"Screening blocked"** |
| Screening axis | `state: "blocked"`, `reason: "lake_empty"` |
| Technical axis | latest `2026-10-16` = expected `2026-10-16`, coverage `3563/3563` |
| Ingestion axis | `last_status: "timed_out"`, `last_success: null` |

**[Observed]** The database moved during this audit. My first probe read
`technical_data` at `2026-10-15`; a later probe read `2026-10-16`, and
`sync_log.daily_ingest` now records `timed_out — "Task timed out after 900s"`
(`2026-10-17T01:32:40`), overwriting the previous `success` row. With the session now current,
Phase 16's escalation fires for the first time in production: `READY` → **`BLOCKED`**, with the
lake named as the cause. That axis works as designed.

**[Observed]** `sync_log` is `task_name TEXT PRIMARY KEY` — **one mutable row per task**. A failed
run therefore *erases* the record of the last success; `ingestion.last_success` became `null`
even though 2026-10-16 data exists and is complete.

---

## 2. Current behaviour of both screening paths

### 2a. Web/API scanners — **healthy, and the only path producing candidates**

`myra_web/routes/scanners.py:171` calls `scanner.scan(as_on_date=scan_date)`; `_trigger_build`
constructs `TriggerScanner(**kwargs)`. I reproduced exactly that call against the live read-only DB.

| Metric | `min_mcap=200` (route default) | `min_mcap=300` (class default) |
|---|---|---|
| Universe | **2,010** | 1,846 |
| Data date used | **2026-10-16** (current) | 2026-10-15 |
| Candidates | **37** (B=8, C=26, D=3, A=0) | 33 (B=5, C=25, D=3) |
| Runtime | **24.6 s** | 27.8 s |
| Top candidate | CUPID 69.6 (grade B) | SHIVAUM 69.6 (grade B) |

**[Observed]** The earlier "2 candidates" was a *bounded 200-symbol sample*, not this path. The
unbounded path returns 33–37.

**[Observed]** `TriggerScanner` derives everything it needs from raw OHLCV + delivery in
`technical_data` plus the latest `fundamentals` row per symbol. It reads **no lake artifact and no
scorer input**: `open, high, low, close, volume, delivery, delivery_pct, sma_50, high_52w, low_52w,
nifty_outperformance_score`. It is unaffected by every defect in §3.

**[Observed]** It does **not** produce `MYRA_Score_v25`; it produces its own `trigger_score`
(gate-weighted: float absorption 30%, seller extinction 25%, volume pinch 25%, defence bars,
breakout proximity, base duration). It is a *different* screen, not a repaired turbo path.

**[Observed]** Candidate records carry **no data date**. The route stores `scanned_date: scan_date`,
which is `None` unless the caller supplies one (`_trigger_parse`). So the API cannot state which
session it screened without the caller also reading `MAX(date)`. This matters for §8.

### 2b. CLI/turbo path — **three independent blockers; none of them is the missing indicators**

**[Observed]** Driving `Engine.run_scan` exactly as the CLI does, with 12 real symbols:

```
status: failed_indicator_computation
reason: indicator_computation
detail: "'list' object has no attribute 'empty'"
```

That is the *current* production verdict. It is not "the lake is empty" — it is an internal
`AttributeError` that fires **before** the intended verdict can be reached.

| # | Blocker | Evidence | Effect |
|---|---|---|---|
| **B1** | `load_universe` returns the literal list `[]` when the lake is empty (`universe_loader.py:69`), while its own `<10` fast path returns `pd.DataFrame()` | **[Observed]** live run above | `Engine.run_scan`'s `if cache_df.empty` raises; `_abort_empty_universe` (which holds the actionable remedy) is **unreachable** |
| **B2** | `pl.read_database(...)` in `run_scan` fails schema inference | **[Observed]** default fails; `infer_schema_length=1000` fails; `10000` fails; `100000` and `None` succeed (54,930 rows) | Any scan that gets past B1 dies at the Polars read. Leading rows are NULL-heavy, so columns are inferred too narrow. Latent: B1 masks it today |
| **B3** | `worker_pool` references `fvg_mid`, never assigned anywhere in the repo | **[Observed]** isolated reproduction: an always-matching stub returns `FAILED / NameError: name 'fvg_mid' is not defined` at `worker_pool.py:249` and `:310`; a non-matching stub returns `SKIPPED` correctly | **No symbol can ever be returned as a candidate** by the worker pool, whatever the data contains |

**[Inferred]** B3 alone makes "restore trustworthy CLI/turbo screening" unachievable by any
data work. B1 makes the failure *dishonest*. B2 will surface the moment B1 is fixed.

**[Observed]** The Phase 16 tests missed B1 because `test_an_empty_lake_is_a_build_problem_not_a_quiet_market`
calls `_abort_empty_universe` **directly**. The unit was correct; the integration was never asserted.

---

## 3. Indicator-by-indicator audit

The scorer is `myra_app/positional_engine.py`; components are in `myra_app/score_components_v2.py`.
Weights: `trend 0.25, stability 0.15, delivery 0.20, liquidity 0.10, base 0.10`, then
`score = 0.7·technical + 0.3·fundamental`, then the drawdown multiplier. A component delta of 0.2
on `base` is therefore `0.2 × 0.10 × 0.7 × 100 = 1.4` points.

Rows reaching the scorer are built by `myra_app/universe_loader.py:38-344` (the `funda_map` entries
at `:221-337`), one dict per symbol, from the lake's latest row per symbol plus `fundamentals`.
`load_universe` is called from exactly **one** place — `engine.py:342` — and **without**
`populate_ranking_inputs`. **[Observed]**

> **The producer never emits six of the seven fields.** `_compute_symbol_indicators`
> (`librarian_intelligence.py:112-237`) writes exactly: `open, high, low, close, volume,
> delivery_qty, sma20, sma50, sma200, rsi, atr20, vol_sma50, avg_deliv_20d, rdv, delivery_pct, vcp,
> delivery_divergence_score, delivery_accumulation_signal, ias, vwap, fvg, bos, choch, inst_vol,
> symbol`. **[Observed]**

`universe_loader` then does `c.get(k, 0) if c.get(k) is not None else 0` for each, so **"the lake
never measured this" becomes the stored value `0`**. **[Observed]**

| Field | Meaning (formula / units) | Consumed by | Existing producer? | In `technical_data`? | History needed / available | Cheap? | Behaviour when absent |
|---|---|---|---|---|---|---|---|
| `sma50` | 50-session SMA of close, **absolute price** | `trend_score`, `universe_loader` stage | **Yes** — lake emits it; `technical_data.sma_50` **verified** to equal a true 50-session SMA | Yes (`sma_50`) | 50 / 3,701 symbols ≥30 obs | free | key absent → `0`; NaN → neutral `0.3` |
| `sma150` | 150-session SMA of close, **absolute price** | `trend_score`, `universe_loader` stage, `_missing_inputs` | **No.** Two real implementations exist but neither reaches the scorer: `data_adapter.compute_common_indicators` (reachable, `pandas_ta.Study`) and `alpha_supply_absorption.py:19` (strategy-local `rolling(150).mean()`) | **No** | 150 / 3,287 symbols | 1 line | absent → `0` → **awards the `sma50 > sma150` sub-score (+0.4) to any symbol with a positive `sma50`**, and caps `trend` at 0.6 |
| `sma200` | 200-session SMA of close | `trend_score` | **Yes** — lake emits it; `technical_data.sma_200` **verified** | Yes | 200 / 3,227 | free | absent → `0` |
| `atr_pct` | ATR(20) ÷ close, **fraction** (tests use 0.02–0.08) | `base_score` | **No** — but `atr20` *is* in the lake, so it is one division away | No | 20 | 1 line | **fabricated `0` → `base_score` returns 0.5 (the *best* tightness band) instead of the 0.3 neutral → +1.4 pts, and it is not reported as missing** |
| `pct_above_ma50_60d` | share of the last 60 sessions with close > SMA50, **0–1** | `stability_score` (returned as the component) | **No** | No | 110 | 1 line | absent → `0.0` = **worst** (NaN would be 0.3). See §7 |
| `keltner_upper` / `keltner_lower` | SMA20 ± 1.5·ATR20, **absolute price** | `base_score` squeeze gate, `scanners/primitives.py:220` | **No producer at all** — 0 writers repo-wide, only 3 readers | No | 20 | derivable from `sma20`+`atr20` (both in the lake) | `0` → the `+0.5` squeeze bonus is **unattainable for every symbol** |
| `atr5` | ATR(5), **absolute price** | `base_score` squeeze gate, `universe_loader` Consensus | No usable one: `whale_tracker.py:46` / `super_setup.py:18` call `ta.atr` with **no `ta` import** (`NameError`); `alpha_vcp.py:43` uses `tr.iloc[-5:].mean()` — a *different* formula | No | 5 | 1 line | `0` → same as keltner |
| `drawdown` | fraction off the high, **0–1** | `_drawdown_multiplier` | **No** | No | **[Decision needed]** (252 assumed) | 1 line | absent/NaN → multiplier **1.0 = no penalty**, the most favourable of the three branches, and it is **not listed in `_missing_inputs` at all** |

**[Observed]** `myra_core/schema.py` declares `TECHNICAL_DATA_ALL` containing `sma150, atr20, atr5,
atr14, atr_pct, std20, drawdown, money_flow_cr` and `MA_COLS = ["sma20","sma50","sma150","sma200"]`.
The live table has **43 columns and none of those** (it uses `sma_5/10/15/50/200`). `TECHNICAL_DATA_ALL`
is referenced by nothing in the repo — a dead declaration that misdescribes reality. **[Observed]**

**[Observed]** `union`: `sma_50`, `sma_200`, `sma_5`, `sma_10`, `sma_15` are **100% populated at the
latest session (3563/3563)** and `sma_50`/`sma_200` match a true N-session SMA to 4 dp on every
sample I checked. These are reusable as-is; `sma20` is **not** stored.

### 3b. Key-name mismatches — five more fields are dead on the lake path

The lake's field names and the names `universe_loader` reads do not all agree. A name that never
matches falls to the `, 0)` default, so these are silent zeros *independent of* the seven traced
fields. The lake emits `vol_sma50`, `avg_deliv_20d`, `delivery_pct`; `universe_loader` reads
`avg_volume_20d` (`:288`), `avg_delivery_20d` (`:291`), `delivery_percent` (`:299`) — **no
overlap**. It also reads `rel_spread`/`rel_vol` (`:170-173`), which the lake does not emit either.
**[Observed]**

Consequences, each a population-wide constant rather than a per-symbol value:

| Field | Read at | Production value after a lake build | Because |
|---|---|---|---|
| `avg_volume_20d` | `:288` | **0** (all symbols) | lake emits `vol_sma50` |
| `avg_delivery_20d` | `:291` | **0** (all symbols) | lake emits `avg_deliv_20d` |
| `delivery_percent` | `:299` | **0** (all symbols) | lake emits `delivery_pct` |
| `VSA_Intensity` | `:177`, `:282` | **0** (all symbols) | derived from two absent keys + `del_pct`=0 |
| `Stage` | `:183-188` | **"Stage 4"** (all symbols) | `:184 if sma150 > 0:` — the guard itself is the fabricated `0`, so the branch never runs |

**[Observed]** `Stage` is a *display* field, not a rank source (see below), so its collapse is a
reporting defect, not a selection defect — but it is the same fabricated-zero root cause.

**[Observed]** `roe` (lowercase) is the key `precompute_ranks` reads; `ROE` (uppercase, `:224`) is
display only. `funda_map` only ever gets lowercase `roe` from `_populate_ranking_inputs` — which does
not run by default. So the ROE rank column is never created and ROE never contributes to the score.

### 3c. The correct pattern already exists in the repo — and is dormant

`myra_app/ranking_inputs.py` is the authoritative, already-audited implementation of exactly the
discipline the seven fields violate. Its docstring states the rule: *"a source failure leaves the
metric absent (`None`), so the scorer applies its neutral fallback rather than ranking a
two-session average as a 20-day one"*; `_merge_row` "never overwrite[s] a real value with
`None`/`0`". **[Observed]**

**[Observed]** It is **opt-in and off by default** (`ranking_inputs_enabled(None)` reads
`MYRA_V25_RANKING_INPUTS`, unset → `False`), and `load_universe` is called without the flag. So the
module that would do this correctly is not on the production path.

**[Observed]** The inconsistency is visible inside a single dict: `smart_money_score` was
deliberately left `None` with a comment explaining that no producer exists (`universe_loader.py:307-312`),
while `sma150`, `atr_pct`, `pct_above_ma50_60d`, `keltner_*`, `atr5` and `drawdown` — the same
predicament — were left as `0`. The Phase 6 audit
(`docs/PHASE6_LIVE_RANKING_INTEGRATION.md`) had already established the "no producer anywhere in
the repository" class for three of the scorer's five cross-sectional inputs; Phase 17 extends it to
the seven technical fields.

**[Observed]** `precompute_ranks` (`score_components_v2.py:27-33`) reads exactly five rank sources
(`roe`, `ProfitGrowth`, `avg_delivery_20d`, `avg_volume_20d`, `smart_money_score`) and otherwise
emits `NaN` so consumers apply their own neutral fallback. The seven traced fields bypass it
entirely — they reach the score per-symbol through `trend_score`/`stability_score`/`base_score`.

> **Scope note.** My 60-symbol reconstruction is faithful for `trend`/`stability`/`base`, which are
> read per-symbol exactly as listed. I deliberately did **not** reconstruct
> `avg_volume_20d`/`avg_delivery_20d`/`roe`, so the `delivery` and `liquidity` components were
> constant in that comparison (§7 says so explicitly). In production those two are *also*
> pathological — all-zero rank columns, and a case-mismatched `roe` — so no §7 figure depends on
> them.

---

## 4. Read-only technical database findings

| Metric | Value |
|---|---|
| Rows | 8,415,614 (2026-10-16 ingest) |
| Symbols | 3,779 |
| `(symbol, date)` duplicates | **0** |
| `close` NULL or ≤0 | 225 rows (0.003%) |
| `high`/`low` NULL or ≤0 | 68,802 rows (0.86%) |
| `delivery_pct` NULL | **0** |
| Observations per symbol | min 1, p10 124, **median 1,227**, max 7,861 |
| Symbols with ≥30 / ≥60 / ≥110 / ≥150 / ≥200 / ≥250 obs | 3,701 / 3,495 / 3,423 / **3,287** / 3,227 / 3,124 |
| Indexes | `idx_tech_date`, `idx_tech_symbol`, `idx_tech_date_symbol`, `idx_tech_symbol_date` |

**[Observed]** Every source field the seven indicators need (`open, high, low, close, volume`)
is present and current. History is sufficient for `sma150` on 3,287 of 3,779 symbols (87%).

**[Observed]** OHLCV cache: 3,320 parquet files, newest mtime **2026-05-02** — **168 days behind**
`technical_data`, independently confirming the Phase 16 handoff.

---

## 5. Option comparison and recommendation

| | **A — reuse** | **B — derive from `technical_data`** | **C — OHLCV cache + lake** | **D — API scanner now** |
|---|---|---|---|---|
| Covers | `sma50`, `sma200`, `sma150` (via `compute_common_indicators`), `atr_pct` (one division from the lake's `atr20`) — **3 of 7** | **all 7** | would cover 1 of 7 (`sma50`/`sma200`); emits nothing for the rest | works today, independent of all 7 |
| Provenance | verified: `pandas_ta` SMA == true SMA; `compute_common_indicators` is reachable via `DataAdapter.get_price_df` | exact, same library (`pandas_ta`) the lake uses | artefact is *written fresh* from *168-day-old prices* | raw OHLCV + delivery, current |
| Freshness limit | inherits lake staleness | **none** — `technical_data` is current | **168 days** | current (`2026-10-16`) |
| Size | small adapter | ~1 module + **3 unspecified definitions** | n/a | **zero** |
| Runtime / memory | free | **measured**: 200 symbols / 495k rows / 73 MB → 5.12 s read + 6.45 s calc = **57.9 ms/symbol** → **~198 s for 3,425 symbols** single-pass; a 252-session bounded read is materially smaller | expensive *and* still stale | 24.6 s for 2,010 symbols |
| Production writes | none | none required | **yes** | none |
| Ranking comparability | improves | improves | none (still zero-filled) | **not comparable** — different score |
| Risk | low | medium — definitions are decisions | **mislabeling stale as current** | none |

**Recommendation — primary: Option D as the immediate research path.** It is the only path that
produces candidates today, it is current, it needs no code change, and it is unaffected by §3.
Its limitation must be stated plainly: it is a gate-based screen, not the v2.5 positional score,
and its output is **not comparable** with `MYRA_Score_v25`.

**Fallback / eventual target: Option B, gated on B1–B3 being resolved.** Option B is the smallest
*safe* source for the six unsourced fields and needs no production write, but it must not be
implemented first: doing so would turn a **visible** failure into an **invisible wrong answer**
(§7), and B3 would still prevent any candidate being returned.

**Option C is rejected for this phase.** A rebuild from a 168-day-stale cache produces artifacts
that are freshly *written* and stale *in content* — exactly the trap the brief forbids. It also
cannot populate six of the seven fields.

**Option A is a genuine partial win** and should be folded into B, not pursued alone.

---

## 6. Runtime, memory and freshness

**[Observed]** Measured, not estimated — 200 symbols (≥250 obs each), 495,336 rows, 73.1 MB:
read 5.12 s, compute 6.45 s → **57.9 ms/symbol** → **≈198 s** for 3,425 symbols in one pass.
Memory is bounded by chunking per symbol; the naive all-at-once frame would be ≈1.25 GB for the
full universe, so **process in chunks**.

**[Inferred]** Reading only the required lookback (252 sessions) instead of full history cuts the
read roughly 5–8× (median history is 1,227 rows), so ≈198 s is a conservative upper bound.

**[Observed]** Freshness: `technical_data` is current (`2026-10-16`); the OHLCV cache is 168 days
behind; the lake is empty. Freshness is therefore **not** the CLI path's binding constraint —
capability is (§2b).

---

## 7. Missing-data risks

Measured over 60 real symbols (≥300 obs), comparing the row the scorer *would* receive after a lake
build against the same row with provenance-correct values.

**[Observed]** `delivery` and `liquidity` were **excluded** from this reconstruction (both were
constant `0.3` in the run) because they are driven by the rank columns of §3b, not by the seven
traced fields. The figures below therefore isolate the seven fields and are *lower bounds* on the
production damage: §3b shows those two components are independently pathological (all-zero rank
columns, case-mismatched `roe`).

| Component | Post-build (fabricated zeros) | Provenance-correct |
|---|---|---|
| `trend` | distinct values **{0.4, 0.6}**, mean 0.5033 | **{0, 0.2, 0.4, 0.6, 0.8, 1.0}**, mean 0.5300 |
| `stability` | **identically 0.0 — all 60** | 36 distinct values, mean 0.4469 |
| `base` | **identically 0.5 — all 60** | {0.3: 32, 0.5: 11, 0.8: 11, 1.0: 6} |
| squeeze bonus ever awarded | **0 / 60** | **6 / 60** |
| `missing_inputs` reports `atr_pct` | **0 / 60** | 0 / 60 |
| `missing_inputs` reports `drawdown` | **0 / 60** | 0 / 60 |

**[Observed]** Whole-score effect on the same population:

| | mean score | Spearman vs correct | top-10 overlap with correct |
|---|---|---|---|
| A — fabricated zeros | 27.01 | **0.7696** | **3 / 10** |
| B — keys absent (neutral fallbacks) | 25.61 | **0.7696** | **3 / 10** |
| C — provenance-correct | 31.08 | — | — |

Two conclusions follow, and they contradict a natural assumption:

1. **[Observed]** A and B rank **identically** (same Spearman to 4 dp, same top-10). Absence and a
   fabricated `0` produce the same ordering, because `trend_score` and `stability_score` default to
   `0` themselves via `row.get(k, 0)`. The *only* measurable difference is the +1.4-point `atr_pct`
   uplift — which is exactly what Phase 15 characterised. **So "stop zero-filling" would not change
   which stocks are selected, only their absolute scores.**
2. **[Observed]** Both A and B rank **poorly against the truth** (Spearman 0.77; only 3 of the top
   10 shared). **The cost is the missing measurements themselves — ~23% of rank correlation — not
   the conflation.**

**[Observed]** Post-build, `stability` (weight 0.15) contributes **exactly nothing for every
symbol**, and `trend` (weight 0.25) is quantised to two values. The technical block loses
`0.25(0.53−0.5033) + 0.15(0.4469−0) + 0.10(0.4983−0.5) ≈ 0.074` of a possible 0.80 ≈ **a 29% loss of
technical signal**, with `delivery`/`liquidity` held constant.

**[Observed]** Three absences are invisible to the score breakdown: `atr_pct` (fabricated `0` is
presented as an observed, favourable value), `drawdown` (absent → the *most favourable* multiplier,
1.0, and the key is not in `_missing_inputs`' source list at all), and `keltner_*`/`atr5` (absence
silently removes a bonus rather than reporting a gap).

### Minimum separate fix (documented, **deliberately not implemented**)

Stop fabricating `0` for `atr_pct`, `drawdown`, `sma150`, `pct_above_ma50_60d`, `keltner_upper`,
`keltner_lower`, `atr5` in `universe_loader` so each takes the component's documented neutral
fallback. Add `keltner_upper/lower`, `atr5` and `drawdown` to `_missing_inputs`' source list.
**Tests that distinguish a genuine zero from missing data already exist and pin the defect:**
`tests/test_phase15_atr_characterization.py` (`base_score` with `atr_pct=0.0` → 0.5 vs absent → 0.3)
and `tests/test_positional_engine.py::test_compute_score_tolerates_none_or_nan_drawdown`. Those
tests are the intended *inversion signal* when the fix lands.

**[Observed]** Because this fix alone would not change ranking (conclusion 1) and *would* lower
every absolute score by ~1.4 points, it should be scheduled **together with** supplying the real
data, not before it. Changing it in isolation would move display bands for the whole universe with
no selection benefit.

---

## 8. Minimum acceptance test for trustworthy screening

Executable procedure; all steps read-only. The point of each step is the **discrimination**, not
the number.

```
# 0. Preconditions (fail fast, do not proceed on a false premise)
python -m myra_app.lake_build --status          # expect count>0 before claiming turbo works

# 1. Data date and source  (must be recorded, not assumed)
SELECT MAX(date) FROM technical_data            # = the session actually screened
SELECT last_run, last_status FROM sync_log WHERE task_name='daily_ingest'

# 2. Universe size and definition
SELECT COUNT(DISTINCT symbol) FROM technical_data WHERE date = <latest>   # 3,563
python -c "from myra_app.strategies.trigger_scanner import TriggerScanner as T; print(len(T()._get_universe()))"  # 1,846

# 3. The screen itself — same call the route makes (scanners.py:171)
#    TriggerScanner(min_mcap=200, ...).scan(as_on_date=<latest>)
#    record: candidates, grades, runtime, and MAX(date) from step 1

# 4. Technical-input coverage for the scorer
#    replay the seven fields against technical_data and report, per field:
#    populated / absent / fabricated-zero   (see §3 table)
```

| # | Outcome to distinguish | How it must be detected | Must **not** be reported as |
|---|---|---|---|
| 1 | Successful scan **with** candidates | `last_scan_outcome.status == success_with_candidates` **and** candidates > 0 | — |
| 2 | Successful scan, **no** candidates | `success_no_candidates` | a failure |
| 3 | **Failed** scan | `outcome.failed is True` with its `reason` | an empty market |
| 4 | Scan with **incomplete** technical inputs | ≥1 of the seven fields absent/fabricated (`missing_inputs`, §7) | a successful scan |
| 5 | Scan on **stale** source data | `technical.latest_date < technical.expected_date` **or** lake `stale_vs` mismatch | `ready` |

Record for every run: data date, source, universe size + definition, candidate count, technical-input
coverage, missing-input handling, runtime, and whether scoring actually completed.

**Can recovery be verified today? No.** **[Observed]** The turbo path cannot produce a candidate
(B3), the lake is empty (`lake_build --status` → `count: 0`), and a lake build from the current cache
would be 168 days stale. Per the brief's instruction to say so explicitly:

> **Exact next operational decision required:** either (a) refresh the OHLCV cache from the EOD
> source so a lake build is content-current, or (b) authorise deriving the seven fields from
> `technical_data` instead of the lake — and in **both** cases first authorise fixing B3
> (`worker_pool` `fvg_mid`), without which no candidate can be returned at all.

**Do not declare the turbo path restored because the command exits 0 or because artifact files
exist.** Today it exits 0 and reports `indicator_lake_empty` — correct, and still not a working scan.

---

## 9. Outstanding decisions and blockers

**Blockers (code), all independent of data:**
1. **B3** `worker_pool.py:249` and `:310` — `fvg_mid` unassigned → *any* match returns `FAILED`.
   **Blocking all candidate output.** Minimal fix is one assignment, but see §7: applying it now
   would publish scores built on fabricated zeros.
2. **B1** — **fixed in this phase** (`universe_loader.py:69` now returns `pd.DataFrame()`), so the
   CLI's verdict is the honest `indicator_lake_empty` with the actionable remedy instead of
   `'list' object has no attribute 'empty'`.
3. **B2** `engine.run_scan`'s `pl.read_database` schema inference — fails at default, 1,000 and
   10,000; succeeds at 100,000 / `None`. Latent behind B1. **Not fixed — out of scope.**

**Decisions only the operator can make:**
1. **[Decision needed]** Lookback definition of `drawdown` (I used 252 sessions; the thresholds
   0.2/0.4 suggest a 1-year high, but nothing in the repo specifies it).
2. **[Decision needed]** Keltner definition — the consumer tests `close ± atr5` against the bands
   (a squeeze test) while the bands themselves are unspecified; SMA20 vs EMA20 and the multiplier
   must be fixed and written down.
3. **[Decision needed]** `pct_above_ma50_60d` is a 0–1 *fraction* (pinned by
   `test_score_components_v2.py:153`), but a future producer emitting a 0–100 percentage would return
   e.g. `85` as the component and inflate the score ~280×. The unit must be stated wherever it is produced.
4. **[Decision needed]** Whether `atr5` means Wilder-smoothed ATR(5) (`pandas_ta.atr`) or mean of the
   last 5 true ranges (`alpha_vcp.py:43`). The two differ; the repo contains both.
5. **[Decision needed]** Source of truth for `sma150`: extend the lake producer, or reuse
   `data_adapter.compute_common_indicators`. The second avoids a second engine.

**Pre-existing issues found, reported not fixed** (repo invariant: flag, don't bury):
`sync_log`'s single-row-per-task design erases the last success; `data_readiness._decide` never
consults `timed_out` (only `failed`/`error`), so a timed-out ingest reads as "waiting for EOD";
`myra_core/schema.py::TECHNICAL_DATA_ALL` describes columns the live table does not have and is
referenced by nothing; `data_adapter.py:37` builds its DB path from `__file__` instead of `DB_DIR`;
`super_setup.py` and `whale_tracker.py` call `ta.*` with no `ta` import (`NameError`);
the five lake↔`universe_loader` key-name mismatches in §3b (`vol_sma50`/`avg_volume_20d`,
`avg_deliv_20d`/`avg_delivery_20d`, `delivery_pct`/`delivery_percent`, plus `rel_spread`/`rel_vol`);
`Stage` collapsing to `"Stage 4"` for every symbol and `VSA_Intensity` to `0` (§3b); the lowercase
`roe` key never being populated, so ROE contributes nothing to ranking (§3c);
`ranking_inputs.py` — the one module that does absent-data correctly — being opt-in and off by default.

**[Inferred]** These survive lint because `.flake8` ignores `F821` — a clean `flake8` run is not
evidence that a code path executes.

---

## 10. Criteria for returning to growth-ranking validation

Do **not** run the growth A/B/C experiment yet. **The data does not currently support a current,
representative, technically complete comparison.** Preconditions, in order:

1. `MYRA_Score_v25` is **produced at all** — requires B3 fixed and a populated, content-current
   indicator source. Today `ranking_history` holds **0 rows** and no path emits the score.
2. **All seven fields populated** with declared units/lookbacks (§9 decisions 1–5), verified by the
   §8 coverage check — not merely present as keys.
3. **No fabricated zeros**: every absent value takes its documented neutral fallback *and* appears
   in `missing_inputs`. Until then, absence and a favourable measurement remain indistinguishable.
4. A representative common universe, defined once and used by both arms (the API scanner's 2,010
   and the CLI's ~3,610 `symbols_master` set are **not** the same universe — §2).
5. Current technical **and** fundamental inputs with documented provenance and freshness
   (`technical_data` is current; check `fundamentals` independently — it is a separate DB).
6. A validated baseline matching actual production ordering.
7. Repeatable comparison metrics, with the limitation stated: the API scanner's `trigger_score` and
   the positional `MYRA_Score_v25` are different constructs and must not be compared as if they were
   the same ranking.

**[Observed]** Criteria 1 and 3 are **not** met, and cannot be met by any data-only action, because
B3 blocks score production entirely.

---

## What this phase changed

| File | Change |
|---|---|
| `myra_app/universe_loader.py` | `load_universe`'s empty-lake branch returns `pd.DataFrame()` instead of `[]`, matching its own `<10` fast path and every other return. Makes `Engine._abort_empty_universe` reachable and the CLI verdict actionable. |
| `tests/test_phase17_universe_empty_contract.py` | 4 tests: the return-type contract, agreement between both empty paths, and the **integration** through `run_scan` asserting `indicator_lake_empty` — the assertion Phase 16's unit-only test omitted. |

Verified: full suite **1628 passed** (1624 + 4); `black` and `flake8` clean (pinned 26.10.1 / 6.0.0);
live re-run of the failing 12-symbol scan now reports `indicator_lake_empty` with the remedy;
all production DB mtimes unchanged and the lake still empty (0 files) — no production write occurred.

**Screening recovery is not claimed.** The turbo path still cannot return a candidate.
