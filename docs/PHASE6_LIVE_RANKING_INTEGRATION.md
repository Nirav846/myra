# Phase 6 - live ranking-input integration (shadow mode)

Directive: connect **only legitimately-produced** source data to the v2.5
positional scorer, integrate nothing that would require inventing a metric, keep
the resulting ordering **non-authoritative** behind an opt-in switch, and
measure what it would change before anybody decides to ship it. No scoring
weight, window, or screening rule was retuned to improve a correlation; no new
dependency, table, cache or service was added.

Phase 5 fixed *how* the scorer consumes its inputs. Phase 6 answers the question
Phase 5 deliberately deferred: **are those inputs actually produced, and can they
be fed to the scorer without fabricating anything?**

## 0. Verified starting state

| Item | Value |
|------|-------|
| Branch | `main` |
| HEAD / `origin/main` | `b2957e6` (`fix(ranking): unbreak v2.5 rank layer + missing-data policy`) |
| Working tree | clean apart from the Phase 6 edits listed in section 8 |
| Python / pandas / numpy / polars | 3.12.10 / 2.2.3 / 1.26.4 / 1.40.1 |
| Full suite (local, before Phase 6) | 1197 passed |
| CI (ubuntu, no live DBs) | 1176 passed / 21 skipped / 0 failed |
| Live DBs | `myra_app/db/myra_technical.db` (~2.05 GB), `myra_app/db/myra_valuation.db` (~262 MB) |

The live databases *are* present in this workspace, so the audit below is based
on the real schema and real rows, not on `schema/*.sql` alone. Every database
access in this phase was read-only (`mode=ro` URI or a plain `SELECT`).

## 1. What the scorer consumes, and what was missing

`myra_app/score_components_v2.precompute_ranks` (lines 27-33) turns exactly five
per-symbol inputs into percentile ranks:

| Result-row key | Rank column | Consumed by | Neutral fallback when absent |
|---|---|---|---|
| `roe` | `_rank_roe` | `fundamental_score` (weight 0.4) | 0.4 |
| `ProfitGrowth` | `_rank_growth` | `fundamental_score` (weight 0.3) | 0.4 |
| `avg_delivery_20d` | `_rank_delivery` | `delivery_score` (weight 0.4) | 0.3 |
| `avg_volume_20d` | `_rank_volume` | `liquidity_score` | 0.3 |
| `smart_money_score` | `_rank_sm_score` | `delivery_score` (weight 0.6) | 0.3 |

The composite is `trend 0.25 / stability 0.15 / delivery 0.20 / base 0.10 /
liquidity 0.10` scaled by a 0.70 technical / 0.30 fundamental split; those
weights are Phase 5 invariants and were **not touched**.

Before this phase, `myra_app/universe_loader.load_universe` read three of those
five keys with a `0` default and **no producer anywhere in the repository**:

```
avg_volume_20d    <- read, never written
avg_delivery_20d  <- read, never written
smart_money_score <- read, never written
```

and the two that *did* exist in `fundamentals` (`roe`, `profit_growth`) were never
plumbed to the scorer under the lowercase key it reads. Phase 5 already proved
the rank layer was inert (defect D1); Phase 6 shows a second reason it was inert:
three of its five inputs had no producer at all.

## 2. Per-metric audit: source, definition, units, freshness, verdict

| Metric | Authoritative source | Definition / units / window | Freshness | Verdict |
|---|---|---|---|---|
| `avg_volume_20d` | `technical_data.volume` | mean daily share volume over the last **20 trading sessions** | daily bhavcopy | **integrated** |
| `avg_delivery_20d` | `technical_data.delivery_pct` (NSE `DELIV_PER`) | mean daily delivery **percentage** (0-100) over 20 sessions | daily bhavcopy | **integrated** |
| `roe` | `fundamentals.roe` (Morningstar `returnOnEquity`) | percent (TTM) | `last_fundamental_update` | **integrated** |
| `ProfitGrowth` | `fundamentals.profit_growth` (`earningsGrowth`) | percent | `last_fundamental_update` | **NOT integrated** - column exists, 0/3948 rows populated |
| `smart_money_score` | *none* | - | - | **NOT integrated** - no producer, no defensible definition |

### 2.1 `avg_delivery_20d` had a producer that was removed

The metric looks like a phantom until you look at history:

| Commit | Date | What happened |
|---|---|---|
| `90d2cc1` | 2026-04-03 | `librarian_intelligence.precompute_indicators` wrote `df['avg_delivery_20d'] = ta.sma(df['Delivery_qty'], length=20)` and `df['avg_volume_20d'] = ta.sma(df['Volume'], length=20)` into the Parquet indicator lake |
| `aa7016e` | 2026-04-17 | "Refactor precompute_indicators method" - **both columns dropped**; delivery renamed to `avg_deliv_20d` (still a delivery *quantity*) |
| `efe6a2a` | 2026-04-04 | `data/` untracked (the lake left version control) |

So the two `avg_*` keys *were* produced once, by a code path that no longer
exists, while `universe_loader` still reads them. Today the indicator lake is
empty in this checkout (`data/indicators/precomputed/` holds no Parquet files), so
the keys arrive as `0`.

This also explains a real historical artefact: the saved snapshot
`myra_reports/scan_rs_rating.csv` (2026-04-25) carries `avg_delivery_20d` values
like `295734.1`, `7166426.3` - a **delivery quantity**, three orders of magnitude
away from a percentage. The legacy definition was a latent bug: delivery quantity
is a proxy for company size, and it double-counts volume. Phase 6 therefore
defines `avg_delivery_20d` as the mean **delivery percentage**, which is the
definition the repo's own ML feature uses (`myra_app/ml_trainer.py`,
`AVG(delivery_pct) OVER w20 AS avg_del`).

### 2.2 `smart_money_score` has no producer and no definition

`git grep` over the whole repository (including the TypeScript frontend) finds
only *consumers*: `worker_pool.py:285` (a `> 0.7` star bonus),
`strategies/smart_money.py:63-64,91` (`SM_Score`), `results_manager.py:310`.
`strategies/smart_money.py:62` carries the comment "HIGH CONVICTION (Composite SM
Score)" but no composite is implemented and no historical producer exists.

Grafting an existing column (`ias`, `rdv`, `delivery_divergence_score`) onto the
name would be exactly the fabrication this phase forbids, so it is left
**absent** - which is also the Phase 5 behaviour. `delivery_score` therefore runs
its documented neutral path for that 0.6 leg.

### 2.3 `ProfitGrowth` is populated nowhere

Live probe of `myra_valuation.db` (read-only):

```
fundamentals rows            : 3948
distinct symbols             : 3948   (0 rows with a '.' suffix -> NSE base tickers)
roe non-null                 : 691    (all non-zero; percent scale, e.g. 32.3)
profit_growth non-null       : 0
earningsGrowth non-null      : 0
revenueGrowth / sales_growth : 0
market_cap non-null          : 3246
```

`fundamental_sync.MS_CANONICAL_MAP` maps `earningsGrowth -> profit_growth` and
`tools/consolidate_fundamentals_columns.py` lists the `roe`/`returnOnEquity` pair,
so the column *plumbing* is correct - the *values* are simply not there. A
`COALESCE(profit_growth, earningsGrowth)` bridge would change nothing today (both
are NULL); it is noted as the thing to add once the upstream sync populates
either column. `ProfitGrowth` is read as `profit_growth` only.

Note the coverage asymmetry: `roe` is populated for 691 of 3948 symbols, and the
sample is dominated by `_SME` tickers (`ADISOFT_SME`, `AERON_SME`, ...), so a
large-cap scan sees very little of it (see section 7).

## 3. Definitions locked by this phase

These are the semantics the code implements, with the reason each choice was made.

* **20 = 20 trading sessions, market-calendar anchored.** The window is the last
  20 distinct `technical_data.date` values at or before the anchor date, and the
  anchor is inclusive. Calendar days are never substituted, and a fixed 5/7
  conversion is never applied.
* **No look-ahead.** `compute_ranking_inputs(..., as_of_date=D)` only admits rows
  with `date <= D`, so a historical or backtest scan can never read a future
  session.
* **Delivery percentage, not quantity, not ratio.** `delivery_pct` is the NSE
  `DELIV_PER` field (`daily_ingestor.py:445`, `fetcher.py:635`). When it is
  missing but `delivery` and `volume` are present, the percentage is derived as
  `delivery * 100 / volume` - the derivation the repo's repair tools already use
  (`tools/db_doctor.py:604`, `myra_app/mass_backfill.py:217`). `delivery_ratio`
  (a 0-1 fraction) is never substituted for a 0-100 percentage.
* **A full window is required.** A symbol with fewer than 20 sessions in the
  window gets `None`, not a mean of whatever exists - a two-session average must
  never be handed to a cross-sectional ranker as a 20-day one.
* **Stored values are passed through, never sentinelled.** `roe == 0` stays `0`;
  only a SQL `NULL` becomes `None` (the repo's never-clobber-a-valid-metric
  invariant, applied on the read side).
* **Absent is distinct from weak.** `None` reaches the scorer unchanged, which
  applies its documented neutral fallback (0.3/0.4) rather than a favourable or
  zero default - the Phase 5 D2 policy.
* **Percentile ranks are unit-agnostic.** Because `precompute_ranks` turns values
  into `rank(pct=True)`, the ordering only depends on a metric's cross-sectional
  *ordering* and sign, not on its scale. This is why a source-mix (Morningstar
  vs a scraper) does not corrupt the ordering - but it is also why the *stored*
  value must still carry correct units, since it is shown to users.

## 4. Where the values enter the pipeline

```
technical_data (volume, delivery_pct)         fundamentals (roe, profit_growth)
            |                                              |
   compute_ranking_inputs()                      compute_fundamental_inputs()
             \___________________  ______________________/
                                 \/
       load_universe(..., populate_ranking_inputs=?)     myra_app/universe_loader.py
                                 |                       (opt-in; default off)
                         _populate_ranking_inputs()
                                 |
                             funda_map[symbol]  <- avg_volume_20d / avg_delivery_20d / roe
                                 |
           engine.run_scan -> worker_pool._worker_task -> res_dict = {..., **funda}
                                 |
                     screener.execute_scan  (gap-fill defaults only)
                                 |
                 PositionalScorer.rank -> precompute_ranks -> _rank_* -> MYRA_Score_v25
```

The single integration point is `load_universe`, because `worker_pool` already
spreads every `funda_map` value into the result row (`worker_pool.py:273`). No
scanner strategy, worker, or scorer signature was changed.

`myra_app/screener.py` needed one defensive edit: the "NO FUNDAMENTALS" block
unconditionally wrote `ROE = "-"` and `ProfitGrowth = 0`. Those are now
gap-fill-only (`if r.get("ROE") is None`), so a real value can never be
overwritten by a placeholder. With the flag off the emitted row is unchanged,
because those keys are still absent upstream.

## 5. The switch, and how to run the shadow comparison

The populated ordering is **off by default**:

```python
load_universe(lib, symbols, as_of_date)                                  # unchanged
load_universe(lib, symbols, as_of_date, populate_ranking_inputs=True)    # explicit
# or process-wide:  set MYRA_V25_RANKING_INPUTS=1
```

`ranking_inputs_enabled(explicit)` resolves the explicit argument first, then the
environment variable, and defaults to `False`. Unset / `0` / `false` / `no` /
`off` all keep the current production ordering.

To see what populating them *would* do, without touching production:

```
python tools/compare_v25_orderings.py --snapshot myra_reports/scan_rs_rating.csv \
    --as-of 2026-04-25 --top-n 20 --json out.json
```

Two guarantees make the comparison meaningful:

1. **Read-only.** The tool opens every database through a `file:...?mode=ro` URI,
   so it cannot write, cannot create `-wal`/`-shm` sidecars, and cannot alter
   production data.
2. **Same result set.** Both orderings are produced from one list of rows inside a
   single `compare_orderings` call, so the comparison can never be an artefact of
   comparing two different snapshots (`myra_app/shadow_ranking.py`).

## 6. Before / after database-load behaviour

| | Flag off (default) | Flag on |
|---|---|---|
| Extra queries at scan start | none | 2 bound-resolution reads + `ceil(N/500)` windowed reads on `technical_data` + `ceil(N/500)` reads on `fundamentals` |
| `funda_map` contents | byte-identical to pre-Phase-6 | `avg_volume_20d` / `avg_delivery_20d` / `roe` / `ProfitGrowth` merged where available |
| Ordering | byte-identical | `MYRA_Score_v25` recomputed from the populated ranks |
| Failure mode | n/a | best-effort: a source error leaves the metric absent (neutral fallback); it never aborts the scan or fabricates a value |

Measured wall-clock for gathering inputs over the real 938-symbol snapshot
universe (live DBs, read-only):

| Query shape | `--as-of 2026-04-25` | latest anchor |
|---|---|---|
| symbol filter only (row-window over full history) | 27.39 s | 15.57 s |
| calendar-anchored window (`date >= lower AND date <= upper`) | **2.14 s** | **1.75 s** |

The optimisation resolves the real trading dates once (~0.01 s), which lets
SQLite use the existing `idx_tech_symbol_date` index (`EXPLAIN QUERY PLAN` ->
`SEARCH technical_data USING INDEX idx_tech_symbol_date`) instead of walking
every historical session of every requested symbol.

It is not *value*-neutral, and the difference was measured rather than assumed:
over the 938-symbol snapshot universe, 793 symbols produced inputs before the
change and 785 after - **14 symbols lost coverage** (e.g. `HEG` had 5 sessions,
`SANGINITA` 9, `SELMC` 5, in the most recent 20 market dates). Those are exactly
the names whose "last 20 sessions" would otherwise have spanned months, letting a
stale average compete in a cross-sectional rank against fresh 20-day averages.
The stricter definition is deliberate (section 3) and the trade-off is recorded
in section 9.

## 7. Shadow-comparison measurement (real data)

**Provenance:** historical snapshot CSVs stored in `myra_reports/` combined with
**live** database reads (read-only). Not fixtures, not synthetic rows. The
snapshots predate the current code, so this measures "what would the ordering
have been", not "what the ordering is today".

| Snapshot | Candidates | Spearman | identical | top-20 overlap | moved / max delta | coverage (`roe` / `ProfitGrowth` / `avg_delivery_20d` / `avg_volume_20d`) |
|---|---|---|---|---|---|---|
| `scan_rs_rating.csv` | 938 | 0.6698 | False | 13/20 (J=0.481) | 931 / 739 | 71 / 0 / 654 / 654 |
| `scan_multibagger_early.csv` | 385 | 0.8855 | False | 14/20 (J=0.538) | 381 / 238 | 62 / 0 / 382 / 382 |

Top-20 churn (`scan_rs_rating.csv`): **entered** `AJANTPHARM, ANANDRATHI,
APLAPOLLO, GPIL, JBCHEPHARM, TORNTPHARM, TVSMOTOR`; **left** `HINDZINC, INDIANB,
MAHABANK, ONGC, SBIN, SHRIRAMFIN, UNIONBANK`. The names that leave are delivery
*quantity* heavy PSU and large caps; the names that enter are high-ROE quality
names - the direction you would expect when delivery intensity and ROE replace a
delivery quantity and a constant. `scan_multibagger_early.csv` behaves the same
way at a smaller magnitude.

### 7.1 Why the coverage numbers look the way they do

* `avg_delivery_20d` / `avg_volume_20d` (654/938 and 382/385): coverage tracks
  **symbol presence in `technical_data`**, not the window logic. `technical_data`
  holds 3423 distinct symbols; 656 of the 938 snapshot symbols are present, and
  654 of those have a full 20-session window.
* `roe` (71/938): `fundamentals.roe` is populated for 691 of 3948 symbols and the
  population is SME-heavy, so only 71 of this large-cap universe is covered. This
  is a **fundamentals-population** gap, not a matching bug - there are zero
  dotted/suffixed symbols in the table, so the join is not silently missing rows.
  The snapshot's own `ROE` column has 196 non-null values because it was written
  in a different data era.
* `ProfitGrowth` (0 everywhere): section 2.3.

### 7.2 Honest caveat: the baseline ordering is degenerate

The measured `moved` and `max position delta` figures are **inflated by
tie-breaking in the baseline** and must not be read as signal:

* Only 225 of 938 snapshot rows carry any technical input at all
  (`close`/`sma50`/... are non-null for 225 rows, null for 713), so 713 rows
  score one identical neutral value. In the snapshot's own `MYRA_Score_v25`
  column that is 713/938 rows sharing a single score.
* `pct_above_ma50_60d` and `atr_pct` are present but single-valued (1 distinct
  value each), and `F_Score` is absent entirely - so three more scoring inputs
  contribute a constant.
* Re-scoring the snapshot with current HEAD gives 164 distinct scores with a
  713-row tie block; populating the inputs gives 204 distinct scores with the
  largest block down to 286. The baseline's *within-block* order is therefore
  arbitrary (stable-sort order), which is where most of the "movement" lives.

The load-bearing numbers are the rank correlation (0.67 / 0.89) and the top-20
overlap (13/20, 14/20), because the top-20 is drawn entirely from the 225 rows
that do have real inputs. **No claim is made that the populated ordering is
better** - only that it is materially different, and that the difference is
depressed or inflated by how complete the underlying data is. That is the whole
argument for shipping it disabled.

## 8. Files changed, and what the tests lock

New:

| File | Purpose |
|---|---|
| `myra_app/ranking_inputs.py` | authoritative (read-only) sources + definitions + the opt-in flag |
| `myra_app/shadow_ranking.py` | same-result-set ordering comparison (`compare_orderings`) |
| `tools/compare_v25_orderings.py` | read-only CLI to run the comparison on a snapshot |
| `tests/test_ranking_inputs.py` | 14 tests |
| `tests/test_shadow_ranking.py` | 6 tests |

Modified:

| File | Change |
|---|---|
| `myra_app/universe_loader.py` | `load_universe(..., populate_ranking_inputs=None)` + `_populate_ranking_inputs` |
| `myra_app/screener.py` | `ROE`/`ProfitGrowth` gap-fill only (plus one pre-existing non-black formatting normalisation) |

What the 20 new tests pin:

* full-window means, and **absent** (not zero) when history is short;
* derived delivery percentage when `delivery_pct` is NULL, and `None` when a
  symbol has no delivery data at all;
* `as_of_date` excludes future sessions (look-ahead guard);
* case-insensitive symbol alignment, empty/`None` inputs;
* `roe == 0` is preserved and SQL `NULL` becomes `None`;
* `attach_ranking_inputs` never mutates its input and never overwrites a real
  value with `None`;
* the flag defaults to **off**, env truthiness is honoured, and an explicit
  argument wins over the environment;
* `_populate_ranking_inputs` end-to-end against a fake librarian;
* `compare_orderings` returns an identical ordering and Spearman `1.0` for empty
  inputs (the regression guarantee for the default path), reports coverage
  correctly, and is deterministic.

**Outcomes:** new tests 20 passed; full local suite **1217 passed**
(1197 + 20 new) / 0 failed / 0 skipped; `black` 26.10.1 clean, `flake8` clean on
all touched files; `tools/performance_guard.py` reports **268 violations, 0
CRITICAL** - exactly the pre-existing baseline, i.e. the new files add no
performance-guard warnings.

## 9. Limitations

1. **`smart_money_score` remains phantom.** `delivery_score` = `0.6 *
   _rank_sm_score + 0.4 * _rank_delivery`, and the 0.6 leg is still always the
   neutral 0.3. The component's heaviest advertised input is inert until someone
   defines and produces it.
2. **`ProfitGrowth` remains empty**, so `fundamental_score` loses its 0.3 growth
   leg on top of `F_Score` being absent from these snapshots.
3. **`roe` coverage is 7.6% on a large-cap universe** because the fundamentals
   population is SME-heavy. The scorer's ROE leg will be neutral for most scan
   candidates until the upstream sync widens coverage.
4. **Data-completeness gaps in `technical_data`.** 282 of 938 snapshot symbols
   have no rows at all, and liquid names can be sparse (`HEG`: 5 sessions in the
   most recent 20 market dates). This is upstream of the scorer and is the
   dominant driver of the coverage numbers above.
5. **The calendar-anchored window tightened coverage** from 793 to 785 symbols on
   the measured universe (section 6). Deliberate, but it is a definition change
   and should be reviewed against the "20 sessions" semantics used elsewhere.
6. **Freshness is not enforced.** `fundamentals.last_fundamental_update` exists
   but no staleness cutoff is applied, so an old-but-present ROE is treated the
   same as a fresh one.
7. **The event-level ranking is within-scan**, not universe-wide (Phase 5
   deferred item), so a percentile means "percentile of this scan's candidates".
8. **The measurement is historical.** No live end-to-end scan was possible here
   because the indicator lake is empty in this checkout, so the numbers come from
   stored snapshots plus live-DB inputs rather than a fresh scan.
9. **`universe_loader` still `0`-defaults ~40 cache columns** (Phase 5 deferred
   item), which conflates "absent" with "zero" for inputs outside this phase's
   scope.

## 10. Next steps

The single highest-value next phase is **data completeness and freshness**, not
more scoring work: the scorer's rank semantics are now correct (Phase 5) and the
legitimate sources are now connected (Phase 6), but three of the five inputs are
either absent or thinly populated, and 30% of a typical scan universe has no
`technical_data` rows at all. Concretely:

1. Populate `fundamentals.profit_growth` (or `earningsGrowth`) and widen
   `fundamentals.roe` beyond the SME tail; add a `last_fundamental_update`
   staleness guard so an old value surfaces as absent rather than as current.
2. Investigate the `technical_data` session gaps that make liquid names look
   sparse, since every rolling metric silently degrades to a neutral fallback
   when they are missing.
3. Only then decide whether the populated ordering should become the default -
   and do it with out-of-sample evidence, not with an in-sample correlation.
