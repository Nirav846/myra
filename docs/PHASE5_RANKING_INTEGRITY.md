# Phase 5 - Ranking & scoring integrity audit

Directive: **correctness before score optimisation.** Classify every finding as
(1) a proven defect, (2) an ambiguous policy, (3) a documented deliberate
trade-off, or (4) cosmetic -- and fix only the proven defects. Weights were not
retuned, scales were not renormalised, no favourable default was substituted for
missing inputs, and candidates with missing fundamentals are still scored (never
auto-rejected).

## 0. Verified starting state

| Item | Value |
|------|-------|
| Branch | `main` |
| HEAD / `origin/main` | `4763f86` (`docs: record Phase 4 verification`) |
| Working tree | clean |
| pandas / numpy | 2.2.3 / 1.26.4 |
| Full suite (local) | 1179 passed |
| CI (ubuntu, no live DBs) | 1158 passed / 21 skipped / 0 failed |

## 1. Scoring & ranking dataflow

The v2.5 positional score is produced by exactly one path:

```
bhavcopy ingest -> technical_data (*.db)
      -> feature_enrichment           (Volatility/RSI, 99.4% coverage)
      -> strategy scan (engine.py -> worker_pool.run_workers)  -> list[dict] results
      -> screener.run_scan (NO FUNDAMENTALS: ROE="-", ProfitGrowth=0, MYRA_Score=Score|90)
      -> results.sort(MYRA_Score desc)                  # screener.py:441 (stable)
      -> PositionalScorer.rank(res_df, regime)          # screener.py:447
             precompute_ranks()  -> _rank_roe / _rank_growth / _rank_delivery
                                    / _rank_volume / _rank_sm_score
             compute_score()     -> weighted composite -> MYRA_Score_v25
             sort_values(MYRA_Score_v25 desc)
      -> final sort by (MYRA_Score_v25, len(Stars))     # screener.py:456 (stable)
      -> CLI discovery table / last_results.pkl / snapshot CSV
```

`MYRA_Score_v25` lives **only** in `myra_app`; the FastAPI/UI layer never sees it.
Ranking paths that are deliberately *separate* and were audited but not merged:

| Path | Ranks | Notes |
|------|-------|-------|
| `PositionalScorer.rank` | candidates within one scan | the v2.5 score above |
| `results_manager.apply_global_ranking` | Stage -> Stars -> liquidity | 5-key, stable (np.lexsort) |
| `myra_web.utils._apply_tier_rank` / `_grade_rank` | scanner candidates by tier/grade | API-side |
| `factor_engine.rank_universe` | `conviction_score` -> percentile | `rank(pct=True)`, NaN kept -> "WATCH" |
| `fundamental_ranker` | materialises `fundamental_scores` by `Funda_Score` | weights in DuckDB/SQL |

## 2. Confirmed defects and root causes

### D1 (HIGH) - the entire precomputed-rank layer was silently discarded

`PositionalScorer.rank` fed the scorer `row._asdict()` from
`results_df.itertuples(index=False)`. pandas' `itertuples` renames **every column
whose name starts with `_`** to a positional `_0`, `_1`, ... (it builds a
`namedtuple(..., rename=True)` and leading-underscore field names are invalid):

```python
pd.DataFrame({"_rank_delivery": [0.5], "roe": [1]}).itertuples(index=False)
# -> Pandas(_0=0.5, roe=1)   # '_rank_delivery' is gone
```

So `compute_score` never received `_rank_delivery`, `_rank_sm_score`,
`_rank_volume`, `_rank_roe` or `_rank_growth`, and always used their neutral
constants (0.3 / 0.4). Consequence: **20% (delivery) + 10% (liquidity) + 30%
(fundamentals) of the advertised "relative market strength" contributed nothing**
on a real scan -- every candidate's score was driven only by trend, stability,
base, `F_Score`, regime and drawdown. Delivered `roe` / `ProfitGrowth` never
influenced a score at all.

Root cause: relying on namedtuple field names for columns that are intentionally
underscore-prefixed (private/computed). The same trap is already documented
elsewhere in the repo (`tools/backtest_bulk_deals.py:1080`).

Fix: `rank` now iterates `results_df.to_dict("records")`, which preserves exact
column names.

### D2 (HIGH, only reachable once D1 is fixed) - missing data got the *top* percentile

`score_components_v2.precompute_ranks` used
`rank(pct=True, na_option="bottom")` under a comment claiming that gave missing
data the **lowest** rank. In pandas `na_option="bottom"` assigns NaN the
**largest** rank. Verified on pandas 2.2.3:

| input | `na_option="bottom"` | `na_option="keep"` (now) |
|-------|----------------------|--------------------------|
| `[10, 20, 30, nan]` | `[0.25, 0.5, 0.75, 1.0]` | `[1/3, 2/3, 1.0, nan]` |
| `[nan, nan]` | `[0.75, 0.75]` | `[nan, nan]` |
| `[1, inf, -inf, 2, nan]` | `inf/-inf` ranked as valid | `inf/-inf` masked -> `nan` |

So an absent `avg_delivery_20d` / `smart_money_score` / `avg_volume_20d` scored as
the best in the cross-section. Fix: mask non-finite to NaN, rank with
`na_option="keep"` so missing stays NaN and each consumer applies its documented
neutral fallback (0.3 / 0.4). This also makes ranks comparable regardless of how
many rows are missing (the percentile denominator is now the *available* count).

`precompute_ranks` now returns a copy and no longer mutates the caller's frame.

### D3 (MEDIUM) - `NaN` F_Score propagated into `MYRA_Score_v25`

`fundamental_score` guarded `_rank_roe` / `_rank_growth` for NaN but read
`f_score = row.get("F_Score", 0)` unguarded, so a NaN (or `None`) `F_Score`
produced a NaN `quality_score` -> NaN composite -> NaN `MYRA_Score_v25` for that
row. `F_Score` has no producer inside `myra_app` (only read), so this mainly
protects external callers. Fix: NaN/None -> 0 (neutral, matching the documented
"missing -> 0" intent).

### D4 (LOW, latent) - `valuation_score` treated a missing Graham Number as "Expensive"

`NaN <= 0` is `False`, so a missing `graham_number` fell through the
`if graham <= 0: return 0.5` neutral guard and returned `0.2` (the "Expensive"
band) -- the opposite of the component's own stated policy. A non-positive
`close` also raised `ZeroDivisionError`. Fix: NaN -> 0.5 neutral, and
`close <= 0` -> 0.5. `valuation_score` is not called by production code (tests
only), so this is a latent correctness fix.

### D5 (COSMETIC/dead code) - unreachable fundamentals guard

`compute_score` wrapped the 30% fundamentals blend in `if f is not None:`, but
`fundamental_score` always returns a float. The branch never took the else path;
removed (arithmetic unchanged).

### D6 (MEDIUM) - API tier ranking scored `MOD` below `LOW`

`myra_web.utils._TIER_RANK` (used by `_grade_rank` -> `_best_grade` ->
`build_confluence_report`) mapped `HIGH`/`MID`/`LOW`, but the tiers scanners
actually emit are `HIGH`/`MOD`/`LOW` (`bottom_hunter.py:554-560`,
`_TIER_RANK_MAP`). `_grade_rank("MOD")` therefore returned the unknown-value
`0.0` -- *worse* than `LOW` (1.5) -- so a MOD candidate could be reported as
worse than a LOW one in the confluence "best grade". Fix: add `MOD: 2.5`
(`MID` retained for back-compat).

### D7 (LOW, robustness) - non-deterministic ties; `None` drawdown could crash

- `rank` sorted a single key with the default `kind="quicksort"` (not stable), so
  equal v2.5 scores had no defined order. Now `kind="mergesort"`; ties preserve
  the incoming `MYRA_Score` order, so the whole chain (441 -> 447 -> 456) is
  deterministic. `apply_global_ranking` already uses a stable multi-key sort and
  needed no change.
- `drawdown = row.get("drawdown", 0)` then `drawdown > 0.4`: a `None` value
  raised `TypeError` in Python 3. Now `None`/NaN -> no penalty.

## 3. Missing-data & percentile policy

**Policy: an unavailable input is never rewarded and never silently turned into
"worst possible". It maps to the component's documented neutral fallback.**

| Component | Input | Before | After |
|-----------|-------|--------|-------|
| delivery | `_rank_delivery` / `_rank_sm_score` absent or NaN | 0.3 (fallback) | 0.3 (unchanged) |
| delivery | source **column present, value missing** | rank **1.0** (top) | NaN -> 0.3 neutral |
| liquidity | `_rank_volume` absent or NaN | 0.3 | 0.3 (unchanged) |
| liquidity | source present, value missing | rank **1.0** | NaN -> 0.3 |
| fundamentals | `_rank_roe` / `_rank_growth` absent or NaN | 0.4 | 0.4 (unchanged) |
| fundamentals | source present, value missing | rank **1.0** | NaN -> 0.4 |
| fundamentals | `F_Score` NaN/None | NaN (poisons score) | 0 neutral |
| valuation | `graham_number` NaN | 0.2 ("Expensive") | 0.5 neutral |
| non-finite | `+/-inf` in any rank source | ranked as valid | masked -> NaN |

Ranking implication: because a missing value is no longer a top percentile, a
candidate with **no** delivery/volume/smart-money data can no longer outrank a
candidate with real (even weak) data. This is the core behavioural change.

Scope note (honest limitation): on the current screener path the rank-source
columns are largely *absent* rather than NaN -- strategy result dicts carry `ROE`
(not `roe`), no `pct_above_ma50_60d`, and `ProfitGrowth` set to a constant `0` by
`screener.py`. With columns absent there is nothing to rank, so after the fix the
live scores fall back to the same neutral constants as before **except** that D1
now lets any present ranks through. D2's reward-of-missing is therefore fully
fixed for any caller that supplies NaN-bearing columns (and for `universe_loader`
callers), and is a guard against the *next* feature that populates these columns
with real per-symbol NaN gaps.

## 4. Formula / output behaviour changes

| Change | Effect |
|--------|--------|
| `rank` uses `to_dict("records")` | `_rank_*` now reach the scorer (D1) |
| `precompute_ranks` `na_option="keep"` + inf mask + copy | missing -> NaN, no input mutation (D2) |
| `F_Score` NaN -> 0 | no NaN scores (D3) |
| `valuation_score` NaN/`close<=0` -> 0.5 | neutral, no crash (D4) |
| dead `if f is not None` removed | none (identical arithmetic) (D5) |
| `rank` `kind="mergesort"` | stable ties (D7) |
| `None` drawdown -> 0 | no crash (D7) |
| `_TIER_RANK["MOD"] = 2.5` | API MOD no longer below LOW (D6) |
| new `MYRA_Score_v25_breakdown` column | explainability (opt-out via `rank(..., explain=False)`) |

Guaranteed unchanged (locked by tests): the six component weights
(0.25/0.15/0.20/0.10/0.10 + 0.30 fundamentals), the 70/30 tech/fundamental
split, regime multipliers (1.15 / 0.9), drawdown multipliers (0.8 / 0.95) and all
known scalar outputs: `_BULL` -> 71.1, EXTREME FEAR -> 81.8, EXTREME GREED ->
64.0, drawdown 0.3 -> 67.5, drawdown 0.5 -> 56.9, minimal `{"close": 1}` -> 20.3.

## 5. Representative before/after ranking cases (deterministic fixtures)

All rows share identical trend (1.0), stability (0.6), base (0.5), `roe`,
`ProfitGrowth`, `F_Score`; they differ only in delivery / volume / smart-money.

**Case A -- all data present** (`GOOD`: delivery 9.0, vol 9e6, sm 0.9 vs `POOR`:
delivery 0.5, vol 1e5, sm 0.1):

| | GOOD | POOR | order |
|---|---|------|-------|
| before (rank layer inert) | 47.4 | 47.4 | tie |
| after | **69.5** | 59.0 | GOOD > POOR |

**Case B -- missing vs weak** (`NO_DATA`: delivery/vol/sm all NaN vs `WEAK`:
0.2 / 1e5 / 0.2):

| | NO_DATA | WEAK | order |
|---|---|------|-------|
| before (both defects) | 47.4 | 47.4 | tie |
| ranks live but `na_option="bottom"` | **69.5** | 59.0 | **NO_DATA wins (bug)** |
| after (both fixed) | 54.8 | **69.5** | WEAK > NO_DATA |

Case B is the definitive regression: it is the exact behaviour the mission calls
out ("do not substitute favourable defaults for missing inputs"). The third row
shows why D1 and D2 had to be fixed together -- fixing only D1 would have shipped
missing-data reward to production.

## 6. Tests added / updated (and what they prove)

`tests/test_score_components_v2.py` (+8, 1 replaced)

- `test_precompute_ranks_missing_values_are_not_ranked` - NaN stays NaN, present
  values ranked among themselves (replaces the Phase 3 bug-locking test).
- `test_precompute_ranks_missing_does_not_outrank_real_weak_value`.
- `test_precompute_ranks_all_missing_gives_all_nan` (was `0.75`).
- `test_precompute_ranks_masks_non_finite` - `+/-inf` cannot dominate.
- `test_precompute_ranks_ties_share_percentile`.
- `test_precompute_ranks_does_not_mutate_input`.
- `test_precompute_ranks_preserves_custom_index` - positional alignment.
- `test_fundamental_score_nan_f_score_does_not_propagate`.
- `test_valuation_score_nan_graham_is_neutral`.

`tests/test_positional_engine.py` (+9)

- `test_rank_consumes_precomputed_ranks` - **would fail before D1**: exact
  69.5 / 59.0 and delivery/liquidity components of 1.0 (not the 0.3 fallback).
- `test_rank_missing_metrics_are_not_rewarded` - the Case B fixture; asserts
  `missing_inputs` lists the rank keys.
- `test_rank_ties_preserve_input_order`, `test_rank_breakdown_reconstructs_score`,
  `test_rank_explain_false_omits_breakdown`,
  `test_explain_score_matches_compute_score`,
  `test_compute_score_tolerates_none_or_nan_drawdown`,
  `test_rank_does_not_mutate_input_frame`.

`tests/test_dcb_defaults.py` (+2) - `_grade_rank("MOD") > _grade_rank("LOW")`,
unknown tier -> 0 (D6).

## 7. Explainability

Before, a `MYRA_Score_v25` number was un-auditable: there was no way to see which
inputs were present, which fallbacks fired, or how regime/drawdown moved the
score. Now:

- `PositionalScorer.explain_score(row, regime)` returns
  `{score, components{trend,stability,delivery,liquidity,base,fundamental},
  weights, regime, drawdown_multiplier, missing_inputs[]}` -- the same
  single-pass code that produces the score (`_score_and_breakdown`), so the
  explanation cannot drift from the value.
- `rank(..., explain=True)` (default) attaches the same dict per row as
  `MYRA_Score_v25_breakdown`; `explain=False` omits it.
- Weights live in `_BASE_WEIGHTS` / `_TECH_SHARE` / `_FUNDAMENTAL_SHARE`, so the
  full formula is reconstructable from the module.
- The column is added to `results_manager`'s `std` list so it is not rendered as
  a "hero" metric in the CLI table.

Reconstruction contract: given a result row + the regime,
`explain_score` reproduces `MYRA_Score_v25` exactly (tested). Wiring the
breakdown into the API/UI is a follow-up (the UI does not consume
`MYRA_Score_v25` at all today).

## 8. Verification (commands + actual outcomes)

| Command | Outcome |
|---------|---------|
| `pytest tests/test_score_components_v2.py tests/test_positional_engine.py tests/test_dcb_defaults.py tests/test_results_manager.py -q` | 63 passed |
| `pytest ... tests/test_confluence_aggregation.py -q` | 85 passed |
| `pytest tests/ -q` (full suite, local) | **1197 passed**, 0 failed, 0 skipped (360.22 s) |
| `black` 26.10.1 on all touched files | 1 file reformatted, then clean |
| `flake8` on all touched files | clean |

Before/after harness (reporting only, not committed as a test): the three
deterministic cases in section 5 were reproduced with the same fixture rows.

## 9. Ambiguities, limitations and deferred items

1. **`stability_score` absent key** falls through to `row.get(..., 0)` -> `0.0`
   while NaN -> `0.3`, so "unavailable" and "consistently below MA50" are
   conflated for this one component. **Left unchanged** (deliberate trade-off):
   relaxing it would substitute a *more favourable* default for missing input,
   which the directive forbids. Documented in code.
2. **`universe_loader` zero-defaults** (`avg_volume_20d` / `avg_delivery_20d` /
   `smart_money_score` -> `0`) turn "no data" into "worst" for ~40 columns. This
   is systemic and touches every scanner, so it was not changed here; it also
   means D2's NaN semantics matter only where a real NaN reaches a rank source.
3. **Regime values are effectively never `EXTREME GREED`**:
   `MarketMoodHelper.get_market_mood` can only return `EXTREME FEAR`, `FEAR`,
   `GREED`, `NEUTRAL` (the VIX fallback path cannot reach `EXTREME GREED`), so
   that branch is dead in production. Also `universe_loader`'s default mood is
   `"Neutral"` while `screener.py` defaults to `"NEUTRAL"`; neither matches the
   `EXTREME*` strings, so both are no-ops. Cosmetic today; fixing requires a
   product decision on the mood scale.
4. **Ranks are within-scan, not universe-wide.** `precompute_ranks` ranks the
   scan's result set, so "percentile" is relative to that scan's survivors, not
   the full NSE universe. This is the existing design and was not changed.
5. **Two ranking systems coexist** (v2.5 positional vs API tier/grade vs
   `conviction_score`). They are not reconciled; they answer different
   questions. Documented rather than merged.
6. **Pre-existing, not introduced here:** CI emits one third-party
   `Pandas4Warning` from `pandas_ta/__init__.py`
   (`mode.copy_on_write` deprecation). Unchanged.

## 10. Next-phase recommendation

**Populate the v2.5 rank sources on the real scan path, then measure.** D1 showed
the scoring layer's advertised cross-sectional terms (delivery, liquidity, ROE,
growth) were inert because no production caller supplies them. The highest-value
next step is to have the screener attach the per-symbol `avg_delivery_20d`,
`avg_volume_20d`, `smart_money_score`, `roe` and `ProfitGrowth` (or their ranks)
to each result row, so the now-correct rank + missing-data semantics actually
discriminate candidates -- with a shadow-mode comparison of pre/post score
ordering before it becomes the default sort.
