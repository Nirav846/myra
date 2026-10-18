# Phase 21 — V2.5 Readiness and Recovery Plan

**Baseline:** `58a89ea` (Phase 20) · **Phase:** 21 · **Nature:** investigation,
decision and plan. No production operation was performed.

**Verdict: `READY FOR OWNER-APPROVED RECOVERY`.**

The Phase 20 blockers are resolved as *decisions*, not as code. The price-basis
question — which Phase 20 correctly refused to guess — is now settled with
conclusive local evidence, the historical recovery scope is measured and
reproducible, and a per-signal roadmap identifies exactly which V2.5 inputs are
one step away and which still need a product decision. What remains is owner
approval to execute, not missing evidence.

This verdict authorises nothing by itself. It states that the *next approved
operation* is well-defined, scoped, verifiable and reversible.

---

## A. Verified current state

Checked against the running code and live databases (read-only), not against
Phase 20's documentation.

| Item | Verified state |
|---|---|
| Git | HEAD `58a89ea`, branch `main`, tree **clean**, in sync with `origin/main`; 3 pre-existing stashes untouched |
| CI / CodeQL | `39098072982` success (test 3.12 / lint / frontend-build), `39098072876` success (actions / js-ts / python) |
| Overall readiness | `partial` — via `data_readiness.assess()`, the authoritative path |
| TriggerScanner (web/API) | **`operational`** — "Raw OHLCV and delivery are current and sufficiently populated; the TriggerScanner needs no derived artifact" |
| V2.5 turbo (CLI) | **`blocked`**, reason `lake_empty` |
| Indicator lake | 0 files in `data/indicators/precomputed` |
| Data freshness | latest session `2026-10-16` == expected `2026-10-16`; universe 3213 |
| Production writes | none. `myra_technical.db` mtime still `2026-10-17 11:55:59`, unchanged across Phases 19–21 |
| Scheduler | **down** — no lock file, port 8000 has 0 listeners, 0 python processes. Not started (forbidden). Cause remains "the app was never launched", not a lock |
| V2.5 publication | none. No `MYRA_Score_v25` published from incomplete inputs |

**Unchanged since Phase 20:** all Phase 20 findings reproduce exactly — 12
degenerate sessions, 38,320 affected rows, `sma_200` NULL on 7,965,142 /
8,022,725 rows (99.3%), V2.5 mandatory set of 7 with 2 lake-produced.

**One correction to Phase 20.** Phase 20 reported the cache/DB ratio as a
"per-symbol constant" (2.0 / 5.0 / 1.0) with the boundary at 2026-02-01. That
was a misreading. The true structure is a **single step per symbol at that
symbol's ex-date**, and 2026-02-01 is a calendar artifact (a Sunday row), not a
price-basis boundary. See §B.

---

## B. Price-basis decision

### B.1 Evidence

**Declared intent (local, authoritative):**
- `eod2/src/eod2_data/README.md:17` — "daily: OHLCV daily timeframe data
  **adjusted for bonus and splits**".
- `eod2/src/defs/defs.py:787` — `makeAdjustment`: "Makes adjustment to stock
  data **prior to ex date**" → back-adjustment, prior prices restated.
- `eod2/src/defs/defs.py:829` — `df[col] / adjustmentFactor / 0.05` rounded to
  the 0.05 tick → prices divided, not multiplied.
- `myra_app/constants.py:26-30` — `USE_EOD2_DATA = True`, with the comment
  "prevents unadjusted data from overwriting corporate-action-adjusted rows".
  `technical_data.delivery_source` is written as `"eod2_adjusted"`
  (`eod2_sync.py:221`).

**Measured (read-only, 8 symbols):** for each symbol there is exactly **one**
step in `db/cache`, at a per-symbol ex-date, and the factor is constant on
either side of it.

| Symbol | factor before | factor after | sessions @ old / new | step date |
|---|---|---|---|---|
| RELIANCE | 0.5 | 1.0 | 731 / 364 | 2024-10-28 |
| WIPRO | 0.5 | 1.0 | 754 / 341 | 2024-12-03 |
| HDFCBANK | 0.5 | 1.0 | 935 / 160 | 2025-08-26 |
| ZYDUSWELL | 0.2 | 1.0 | 951 / 144 | 2025-09-18 |
| TCS / ITC / SBIN / INFY | 1.0 | 1.0 | 1095 / 0 | no action in window |

**Continuity test — the decisive proof** (no external data needed): a
back-adjusted series has *no* jump at the ex-date; an unadjusted one drops by
the factor.

| Symbol | ex-date | cache move | db + eod2 move |
|---|---|---|---|
| RELIANCE | 2024-10-28 | **−49.76%** | +0.49% |
| WIPRO | 2024-12-03 | **−50.11%** | −0.21% |
| HDFCBANK | 2025-08-26 | **−50.44%** | −0.88% |
| ZYDUSWELL | 2025-09-18 | **−79.80%** | +1.02% (1:4 bonus) |

`db == eod2 CSV` exactly at every step date, so `technical_data` faithfully
carries eod2's basis. The cache holds pre-adjustment prices.

**Field and column coverage:** the factor applies identically to
open/high/low/close (identical session counts per factor). **Volume is
unscaled** — ratio 1.0 on 1095 of 1096 shared sessions — which is exactly what a
price-only corporate action must do. `delivery_source` is `raw_qty` in both
sources across the shared window; only prices differ.

### B.2 Decision

**Canonical basis = corporate-action-adjusted, back-adjusted to the present, as
carried in `technical_data` via the eod2 CSVs. Evidence: conclusive.** Adjusted
prices are the correct basis for every indicator MYRA computes, because they
remove artificial jumps at ex-dates; using unadjusted prices would make every
50/200-session window straddling an action wrong. This is not a preference.

| Data / consumer | Current basis | Required basis | Evidence | Proposed action |
|---|---|---|---|---|
| `technical_data` OHLC | adjusted | adjusted | no jump at ex-date; == eod2 CSV | **canonical — keep** |
| eod2 daily CSVs | adjusted | adjusted | declared + applied by `makeAdjustment` | **canonical source — keep** |
| `data/ohlcv_cache/*.parquet` | **unadjusted**, stale (mtime ≤ 2026-05-02, window 2021-10-01…2026-04-30) | adjusted | drops by factor at ex-date | **rebuild on the adjusted basis, or stop using it** |
| `LibrarianCore.get_ohlcv` | **mixed** (cache history + DB delta) | one basis | reads cache, appends DB rows | **stop mixing** — this is the defect |
| Indicator lake (`_compute_symbol_indicators` → `get_ohlcv`) | inherits the mix | one basis | `:125` | becomes correct once `get_ohlcv` is fixed |
| `results_manager` chart | mixed | one basis | `:449` | cosmetic; fixed by the same change |
| `backtest_engine` | adjusted (`technical_data`) | adjusted, **but see note** | reads DB directly | no change; note below |
| `volume`, `delivery`, `delivery_pct` | unscaled / raw qty | unscaled | ratio 1.0 across the boundary | **basis-independent — no rebuild needed** |

**Which consumers mix incompatible bases:** exactly one seam,
`LibrarianCore.get_ohlcv` (`librarian.py:307-360`). It returns the cache frame
and, when the cache is stale, appends DB rows newer than the cache max. The
indicator lake inherits the mix through `_compute_symbol_indicators:125`, and
`results_manager:449` renders charts from it.

**Quantified damage** (what a lake build today would produce), at a window
straddling each ex-date:

| Symbol | sma50 correct → mixed | sma200 correct → mixed |
|---|---|---|
| RELIANCE | 1446.32 → **2849.32** | 1458.45 → **2899.02** |
| WIPRO | 275.67 → **540.97** | 256.06 → **507.56** |
| HDFCBANK | 994.11 → **1950.05** | 921.81 → **1818.12** |
| ZYDUSWELL | 425.97 → **2105.63** | 382.08 → **1876.14** |

Roughly a doubling (a fivefold error for ZYDUSWELL) for every symbol with a
corporate action inside its trailing 200 sessions.

### B.3 Fields requiring rebuild if the basis is standardised

- The OHLCV cache itself (3320 parquet files) — **or** it is bypassed.
- Anything already derived from the mixed frame — but the lake is **empty**, so
  nothing has been polluted. `technical_data`'s own stored rolling columns are
  computed by `eod2_sync`/`feature_enrichment` over **adjusted** history and are
  therefore unaffected by the cache.

### B.4 Would rebuilding indicators from the canonical source preserve historical semantics?

Yes, for indicator values: recomputation over `technical_data` reproduces a
known-good session exactly. Verified — RELIANCE 2026-10-16, recomputed from
stored history: `sma_50` 1260.5080 vs stored 1260.508; `sma_200` 1345.2610 vs
1345.261; `high_52w` 1611.80 vs 1611.8; `low_52w` 1152.20 vs 1152.2. The same
method on the degenerate session 2026-10-13 yields the correct values
(`sma_50` 1266.29 vs stored 1166.7; `high_52w` 1611.80 vs stored 1168.3, and
1611.8 is independently the stored value on the clean 2026-10-16).

**One caveat — absolute price levels.** Adjusted prices are wrong for anything
that means "what did one share cost". `backtest_engine` reads adjusted prices
and derives entry/exit/stop levels and rupee P&L from them. This is pre-existing
and unaffected by the cache decision, but if the backtest is used for absolute
rupee sizing it needs the adjustment factor retained per symbol. Flagged, not
changed.

### B.5 Alternatives considered

1. **Adopt unadjusted as canonical** — rejected. Indicators straddling an
   ex-date become wrong; contradicts the declared intent of the ingestion
   toggle.
2. **Adjusted basis, rebuild the cache** — valid; preserves the existing
   `get_ohlcv` contract, costs a full cache rebuild (bulk operation, needs
   approval).
3. **Adjusted basis, make the lake read `technical_data` directly** (recommended)
   — removes the second source of truth instead of repairing it, which is
   cheaper to keep correct. It changes a read path, so it is scoped and
   reversible like any other code change. Cache-backed consumers
   (`results_manager` charts) then need to follow.

### B.6 Remaining uncertainty

- Which backtest outputs, if any, are consumed as absolute rupee amounts (§B.4).
- Whether any downstream artefact outside this repository already embeds the
  unadjusted basis. Nothing in-repo does; the lake is empty and the cache is
  read-only input.
- Nothing material remains for the **basis** question itself.

---

## C. Historical recovery plan

Proposed only. **Nothing below was executed.**

### C.1 The 12 degenerate sessions

**Exact scope:** 2026-08-17, 08-20, 08-24, 08-25, 08-27, 08-31, 2026-09-08,
09-11, 09-21, 09-28, 2026-10-07, 10-13. **38,320** rows have `sma_50 = close`;
`high_52w = high` and `sma_200 IS NULL` on every row of every one of the 12
sessions (~3,200 rows each).

**Can valid values be reproduced from stored history?** Yes — proven above
(§B.4). The stored OHLCV for those dates is correct; only the derived columns
are wrong. Recomputation needs no re-ingestion and no external source.

**Dependencies that go stale:** every downstream indicator derived from those
rolling columns — `sma_50`, `sma_200`, `high_52w`, `low_52w`,
`delivery_ma_60` — for those dates. Nothing outside the lake depends on them
today, because the lake is empty. So a repair now is **safe and self-contained**.

**Execution plan (small, independently verifiable steps):**

| Step | Precondition | Action | Postcondition / validation | Rollback |
|---|---|---|---|---|
| R0 | owner approval; full DB backup | Snapshot `myra_technical.db` | byte copy + checksum recorded | restore snapshot |
| R1 | R0 | Dry-run on a **copy**: recompute the 12 sessions for 50 sampled symbols | zero divergence on the 4 clean control sessions; 100% of sampled bad sessions change to the recomputed value | discard the copy |
| R2 | R1 clean | `UPDATE` the 12 sessions' `sma_50`, `high_52w`, `low_52w`, `delivery_ma_60` from recomputation, in one transaction | `sma_50 = close` count on those dates drops to the expected 1–2 legitimately-degenerate rows/session | restore snapshot |
| R3 | R2 | Backfill `sma_200` for the same 12 sessions | 0 NULL among those rows | restore snapshot |
| R4 | R3 | Full-table re-audit | `sma_50 = close` total falls from 40,431 to ≈110 (legitimate single-session listings only) | restore snapshot |
| R5 | — | Decide separately whether to backfill `sma_200` for the remaining ~99.3% of history | — | — |

R5 is deliberately a **separate decision**: it touches ~8M rows and is only
worth doing once the basis is fixed, because a backfill over a mixed-basis frame
would bake in wrong values.

**Detection of partial completion / duplicates / silent failure:** every step is
a single transaction; the post-condition counts above are asserted after each
step; a per-symbol row-count delta is logged. `live_db_guard` continues to
protect test runs.

**Interruption:** SQLite transactions are atomic; an interrupted `UPDATE` rolls
back. Recovery = restore the R0 snapshot.

### C.2 The phantom sessions — scope is larger than recorded

Phase 20 identified one phantom session (2026-02-01). Measured scope:

- **37** weekend-dated sessions exist in `technical_data` (31 Saturday,
  6 Sunday).
- **9** of them are universe-wide (1005–2791 symbols): 2014-03-22, 2015-02-28,
  2016-10-30, 2023-11-12, 2024-01-20, 2024-03-02, 2024-05-18, 2025-02-01,
  2026-02-01.

**Correction: nothing is missing.** 2026-01-31 is a **Saturday** — not a trading
day. The trading days Friday 2026-01-30 (2807 rows) and Monday 2026-02-02 (2796
rows) both exist. The Sunday row is an **extra** record, not a shifted one.

**Origin: upstream, in the eod2 source.** RELIANCE's eod2 CSV contains
`2026-02-01` and does not contain `2026-01-31`; `technical_data` and the cache
both inherited it. MYRA cannot synthesise the correct session because the
source never carried the right date.

**Recovery (needs the eod2 maintainer first):**
1. Correct the eod2 CSVs so sessions carry true trading dates (upstream change).
2. Re-ingest so `technical_data` reflects them — note `sync_eod2_data` only
   inserts dates **newer** than the per-symbol cutoff, so a same-date correction
   will **not** be picked up; an explicit targeted correction is required.
3. Only then remove the 9 spurious sessions from `technical_data`.

**Do not delete these rows before step 1.** Until the source is corrected,
deleting them loses observations that the next ingest would simply re-add
under the same wrong dates.

### C.3 `sma_200` history

99.3% NULL. Same recomputation as R3; no re-ingestion required; see R5 for why
it is deferred.

---

## D. V2.5 signal roadmap

### D.1 Mandatory inputs (7)

| Input | Definition / units | Producer | Warm-up | Adjustment-dependent? | Missing behaviour | Class |
|---|---|---|---|---|---|---|
| `sma50` | 50-session mean close | lake + `eod2_sync` | 50 | **yes** | `trend_score` neutral 0.3 | produced |
| `sma200` | 200-session mean close | lake + `eod2_sync` (Phase 20) | 200 | **yes** | neutral 0.3 | produced, history NULL |
| `sma150` | 150-session mean close | **none** | 150 | **yes** | neutral 0.3 | → option **2** |
| `pct_above_ma50_60d` | fraction of last 60 sessions with close > MA50 | **none** | 60 | **yes** | `stability_score` 0.0 (punishes) | → option **2** |
| `avg_volume_20d` | mean session volume, 20 sessions | `ranking_inputs` (gated) | 20 | **no** | `liquidity_score` 0.3 | → option **1** |
| `avg_delivery_20d` | mean daily delivery %, 20 sessions | `ranking_inputs` (gated) | 20 | **no** | neutral 0.3 | → option **1** |
| `atr_pct` | ATR as fraction of price | **none** | depends | **yes** | neutral 0.3 (Phase 20 fix) | → option **4** |

### D.2 Optional inputs (5)

`keltner_upper`, `keltner_lower`, `atr5`, `drawdown`, `smart_money_score` — all
unsourced. `atr5` and `keltner_*` become computable from the lake's existing
`atr20` once their centre-line/multiplier is decided (**4**). `drawdown` is
decidable (**4**). `smart_money_score` should **remain unavailable (5)** — it has
no defensible definition, and Phase 12 already removed its fabrication.

### D.3 Classification of each unsourced signal

- `sma150` → **2** (existing source fields support a defensible producer: 150-session mean of the same adjusted close series used for `sma50`/`sma200`). Must be produced from the *same* series, so it depends on §B.
- `pct_above_ma50_60d` → **2** (definition already established and unit-pinned by test; computable from `close` and `sma50`).
- `avg_volume_20d` → **1** (existing validated producer, currently disabled).
- `avg_delivery_20d` → **1** (same).
- `atr_pct` → **4** (needs a product decision on lookback and denominator).
- `atr5`, `keltner_upper`, `keltner_lower`, `drawdown` → **4** (product decision).
- `smart_money_score` → **5** (remain unavailable).

### D.4 Implementation order

1. **Price-basis fix (§B.5 option 3)** — everything adjustment-dependent depends
   on it. Produces no new signal; unblocks correct computation.
2. **`avg_volume_20d` + `avg_delivery_20d`** — enable the existing gated
   producer. **Not blocked by the basis decision** (volume/delivery are
   unscaled). Requires 20 sessions of warm-up; symbols newer than that stay
   absent and take the neutral.
3. **`pct_above_ma50_60d` + `sma150`** — small producers over columns that
   already exist. Depends on step 1.
4. **`atr_pct`** — after the product decision.
5. Optional signals — last, and they do not block a build.

### D.5 `MYRA_V25_RANKING_INPUTS` — kept disabled

**Actual effect.** When enabled, `universe_loader` merges four fields
(`ranking_inputs.METRIC_KEYS = roe, ProfitGrowth, avg_delivery_20d,
avg_volume_20d`) into each result row. Those four feed `precompute_ranks`, which
produces `_rank_roe`, `_rank_growth`, `_rank_delivery`, `_rank_volume` — and
therefore `fundamental_score`, `delivery_score` and `liquidity_score`. It
deliberately does **not** produce `smart_money_score`. Each metric requires a
full 20-session window; otherwise it is left absent so the scorer applies its
neutral.

**Validation evidence needed before activation** — none exists yet:
1. Re-measure per-component score distributions on a representative universe
   with and without the flag, and quantify ordering changes.
2. Confirm coverage: what fraction of the universe actually has a full 20-session
   window for both metrics (recent listings will not).
3. Confirm the `roe`/`ProfitGrowth` values are real measurements and not
   zero-filled (Phase 12 removed fabrication for `profit_growth`,
   `sales_growth`, `debt_to_equity`; `roe` follows the same path).
4. A/B one full ranking run and review the reordering before any switch.

**Recommendation: keep disabled** until §D.5 items 1–4 are done. Enabling it
changes published ordering.

---

## E. Validation-build gate

Designed, not run.

### E.1 Preconditions (all must hold)

1. Canonical basis fixed: `get_ohlcv` returns a single-basis frame; a
   straddling-window check shows `sma_50` agreeing with the DB-only computation
   for RELIANCE, WIPRO, HDFCBANK and ZYDUSWELL.
2. Recovery R1–R4 complete and verified.
3. At least **6 of 7** mandatory inputs have an enabled producer (see E.4).
4. `MYRA_V25_RANKING_INPUTS` state is an explicit, recorded decision.
5. A backup exists and its checksum is recorded.

### E.2 Indicator correctness checks (per symbol, on the latest session and on
5 historical dates spanning ≥1 corporate action)

- `sma50`, `sma150`, `sma200` equal an independent recomputation from
  `technical_data` within 1e-6.
- `sma_N != close` for any symbol with ≥N sessions — a direct guard against the
  Phase 20 D1 shape.
- ATR equals an independent True-Range computation to 1e-6.
- No rolling column equals its own session's field unless genuinely so.

### E.3 Data-quality checks

- No duplicate `(symbol, date)`; no weekend-dated session; no date gap beyond
  the market calendar.
- Latest session == expected session; per-session symbol count ≥ the established
  coverage fraction (already encoded in `data_readiness.COVERAGE_COMPLETE_FRACTION`
  — reuse it rather than inventing a threshold).
- **Legitimate zero vs unavailable:** distinguish explicitly — NULL/NaN = not
  measured (neutral, never a bonus); `0.0` = measured zero. Assert `atr_pct`
  is never fabricated to 0 (Phase 20 regression) and that absence is reported in
  `explain_score()["missing_inputs"]`.

### E.4 Coverage and publication

- Mandatory coverage threshold: **6 of 7** is proposed as the minimum for a
  validation build, and is labelled **awaiting owner approval** — the project
  has no established threshold for this. The honest reading of the current
  contract is that 7 of 7 is required for publication; 6 of 7 is a
  *validation-build* threshold only.
- **No partial or fabricated score may be published.** `classify_missing_v25_inputs`
  must report zero mandatory gaps before any `MYRA_Score_v25` is emitted;
  otherwise the mode stays `blocked`/`degraded` and the score is not shown.
- Artifact count == universe symbols attempted, with a per-symbol failure report
  (no silent per-symbol loss — the Phase 19 F1 class of bug).
- Compare against TriggerScanner **without** treating the scores as
  interchangeable: different modes, different inputs, explicitly labelled
  `comparability: not comparable` in `screening_modes`. Use TriggerScanner as a
  *regression* reference for symbol selection, never as a score yardstick.

### E.5 Pass/fail criteria

A successful *build* is not a successful *validation*. Both must pass:
- **Build pass:** every artifact written, no symbol silently failed, schema as expected.
- **Validation pass:** all §E.2 checks at 1e-6, all §E.3 checks clean,
  mandatory coverage ≥ the approved threshold, and zero mandatory gaps reported.
- Any failure → **no publication**; remediate and re-run.

---

## F. Operator decisions

Only decisions that genuinely require the owner.

**F1 — Adopt the adjusted basis as canonical and authorise the fix.**
Options: (a) recommended — make the lake/`get_ohlcv` read `technical_data`
directly, retire the cache's role as a price source; (b) rebuild the cache on
the adjusted basis; (c) keep as-is (rejected: doubles SMAs for affected symbols).
Evidence: §B.1, §B.2. Consequence of (a): one fewer source of truth; a cache
rebuild is no longer needed for correctness.

**F2 — Authorise the historical recovery (R0–R4).**
Scope: 12 sessions, 38,320 rows, recomputation only, no re-ingestion.
Prerequisite: F1 (a backfill must run on a single-basis frame). Evidence: §C.1.

**F3 — Authorise the `sma_200` history backfill (R5), separately.**
~8M rows. Recommended: defer until F1 is done and its effect measured.

**F4 — Contact the eod2 maintainer about the 9 weekend-dated sessions.**
Upstream dependency; MYRA cannot fix this alone. Evidence: §C.2.

**F5 — Decide `atr_pct`**: lookback and denominator (options in
`V25_TECHNICAL_SIGNAL_CONTRACT.md` §2.2). Recommended: ATR(20)/close, matching
the lake's existing `atr20`. Blocks 1 of 7 mandatory inputs.

**F6 — Decide `MYRA_V25_RANKING_INPUTS`.** Recommended: keep disabled until
§D.5 validation exists.

**F7 — Approve or amend the proposed 6-of-7 validation-build coverage
threshold** (§E.4). Labelled awaiting approval.

**F8 — Whether `backtest_engine` results are consumed as absolute rupee
amounts.** If yes, per-symbol adjustment factors must be retained; adjusted
prices are otherwise unsuitable for level-based sizing. Evidence: §B.4.

---

## G. Execution restrictions — confirmed

During Phase 21 the following **did not occur**:

- No historical backfill, no ingestion, no bulk refresh, no indicator-lake build.
- No database migration and no production data correction.
- No write of any kind to a live database — every connection was
  `sqlite3.connect("file:…?mode=ro", uri=True)`; `myra_technical.db`'s mtime is
  unchanged at `2026-10-17 11:55:59`.
- No scheduler start, restart or reconfiguration (it remains down).
- `MYRA_V25_RANKING_INPUTS` not enabled.
- No scoring weight, threshold, ranking formula or growth-default change.
- No `MYRA_Score_v25` published.
- No TriggerScanner change — it remains operational and untouched.
- `live_db_guard` and existing test isolation preserved.

Changes shipped in Phase 21 are confined to documentation and one new test file
(`tests/test_phase21_price_basis.py`, temporary fixtures only, no live data).

---

## Verdict

**`READY FOR OWNER-APPROVED RECOVERY`**

The evidence needed to make the price-basis decision is now complete and
conclusive; the historical recovery is measured, reproducible and reversible;
and the V2.5 roadmap separates signals that are one step away from the one
signal that still needs a product decision. Readiness is for the *next approved
operation* — F1 first, then F2 — not permission to execute either.