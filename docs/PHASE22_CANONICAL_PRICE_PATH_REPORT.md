# Phase 22 — Canonical Adjusted-Price Read Path (F1)

**Baseline:** `da1d60a` (Phase 21) · **Phase:** 22 · **Scope:** Phase 21 decision
**F1** only. No historical recovery (F2) was performed.

**Outcome: F1 implemented and verified. The indicator path now reads historical
prices from one basis. Historical data is *not* repaired — see §7.**

---

## 1. Root cause and Phase 21 evidence

Phase 21 established, using only local data:

- `technical_data` is filled by the eod2 ingestion and holds
  corporate-action-**adjusted**, back-adjusted prices: history is restated
  *prior* to each ex-date (`eod2/src/defs/defs.py` `makeAdjustment`), so the
  series has no price jump at a split or bonus.
- The parquet OHLCV cache (`data/ohlcv_cache`, 3320 files, mtimes ≤
  2026-05-02) holds **unadjusted** prices. At each measured ex-date it drops by
  exactly the action factor — RELIANCE 2024-10-28 −49.76%, WIPRO 2024-12-03
  −50.11%, HDFCBANK 2025-08-26 −50.44%, ZYDUSWELL 2025-09-18 −79.80% — while
  `technical_data` and the eod2 CSVs move normally and agree with each other.
- `LibrarianCore.get_ohlcv` served **the cache**, appending a `technical_data`
  delta when stale. That is one series carrying two price bases.
- `librarian_intelligence._compute_symbol_indicators` — the single producer for
  both lake build paths — reads through `get_ohlcv`, so the indicator lake
  inherited the mix.
- Measured consequence at a window straddling an ex-date: `sma50` 1446 → **2849**
  (RELIANCE), 426 → **2106** (ZYDUSWELL).

---

## 2. Consumers audited and their price semantics

Every consumer of the mixed-source helper was traced (not inferred from names),
and the cache was searched for all readers.

| Consumer | Source today | Basis | Semantics | Action |
|---|---|---|---|---|
| `librarian_intelligence._compute_symbol_indicators` (both lake build paths) | `get_ohlcv` → cache + DB delta | **mixed** | historical price indicators (SMA 20/50/200, RSI, ATR, VWAP, SMC) — needs **adjusted** | **changed** to canonical |
| `results_manager` chart (`:449`) | `get_ohlcv` default | unadjusted | plots a price history; "adjusted" vs "actual traded level" is genuinely ambiguous and cosmetic | **unchanged** — ambiguity documented (§6) |
| `backtest_engine` | direct SQL on `technical_data` | adjusted | **execution prices, rupee sizing, stops, targets** | **unchanged** — does not route through `get_ohlcv` (asserted by test) |
| `worker_pool`, `universe_loader`, `engine` | direct SQL on `technical_data` | adjusted | scanner inputs | unchanged |
| OHLCV cache writers | `get_ohlcv` write-back | — | derived artefact | unchanged; canonical path no longer writes it |

Only **one** consumer's read path changed. The cache's entire blast radius is
those two callers.

---

## 3. The read-path change

Three small edits; no new framework and no duplicated data-access layer.

1. **`myra_app/constants.py`** — added `SOURCE_TECHNICAL_DATA = "technical_data"`
   with the rationale. Placed here (not in `librarian.py`) because both
   `librarian` and `librarian_intelligence` must name it and `librarian` already
   imports `librarian_intelligence` — a constant there would be an import cycle.

2. **`myra_app/librarian.py`** — `get_ohlcv(self, symbol, as_of_date=None, *,
   source=None)`. When `source == SOURCE_TECHNICAL_DATA` it skips the cache read
   entirely and uses the **already existing** `technical_data` read branch
   (ordering, `enforce_index_contract`, `validate_dataframe`, delivery
   harmonisation) unchanged. It also suppresses the branch's
   `save_to_parquet` write-back, so the canonical path is a pure read: seeding
   the cache there would rewrite a production artefact as a side effect of a
   read, and would blur "read path" into "data repair".

3. **`myra_app/librarian_intelligence.py`** —
   `_compute_symbol_indicators` now calls
   `self.get_ohlcv(sym, source=SOURCE_TECHNICAL_DATA)`.

Behaviour preserved deliberately:

- The default remains `source=None` (cache-first). The change is **additive**;
  `results_manager` and any other caller are unaffected.
- Missing symbols/dates read as **unavailable** (`None`), never as a silent
  fallback to unadjusted cache prices.
- A first-ever ingest (empty cache) still seeds the cache as before.
- `as_of_date`, chronological ordering, symbol isolation and one-row-per-session
  deduplication all behave as before.

---

## 4. Tests added

`tests/test_phase22_canonical_price_path.py` — **23 tests**, synthetic data and
temporary databases only, no network and no live market data. Groups follow the
phase brief:

- **A. Corporate-action discontinuity** — a synthetic adjusted series crossing a
  known 1:1 boundary produces no artificial jump, and the straddling 50-session
  mean follows the adjusted series alone.
- **B. Source selection** — the lake requests `SOURCE_TECHNICAL_DATA`; a cache
  holding 7× prices cannot leak into the series; the default is still
  cache-first and keyword-only.
- **C. Missing data** — unknown symbol → `None`; below the 30-row floor →
  `None`; a NULL close stays NULL (is not repaired from the cache); a missing
  date stays a gap.
- **D. Volume and delivery** — share counts and delivery quantities are not
  rescaled across the boundary; `delivery_pct` still equals quantity/volume.
- **E. Backtest / rupee levels** — `backtest_engine` does not route through
  `get_ohlcv`, and the chart consumer is still on the legacy default.
- **F. Preserved behaviour** — the full indicator set still computes on valid
  adjusted data, the eod2 adjusted-source toggle is still on, and a rolling
  mean can never equal its own session's close (the Phase 20 D1 shape).

Six of these exercise the **real** `Librarian.get_ohlcv` body against a temporary
SQLite `technical_data` and a temporary parquet cache, rather than a stub.

`tests/test_phase21_price_basis.py` — the Phase 21 guard asserted the *hazard*
(`self.get_ohlcv(sym)` present). With the hazard closed it now pins the **fix**:
the lake must request `SOURCE_TECHNICAL_DATA`, and a bare `get_ohlcv(sym)` is a
failure. Two existing fakes (`test_phase16_indicator_lake.py`,
`test_phase19_signal_contract.py`) were updated to mirror the new signature.

### Mutation check

The guards were verified by disabling the fix (`canonical = False`): two
integration tests failed, one reporting exactly

```
unexpected 0.5016 jump at the ex-date
```

— the artificial 50% discontinuity, reproduced. The fix was then restored.

---

## 5. Verification results

| Check | Result |
|---|---|
| Focused suites (librarian / lake / indicator / ohlcv / parquet / phase1x / phase2x / data_loader / cache) | **272 passed** |
| Phase 21/22 + lake + contract suites | **85 passed** |
| **Full suite** | **1797 passed** (Phase 21: 1773) |
| `black` (pinned 26.10.1 via `pre_commit`) | Passed |
| `flake8`, MYRA Performance Guard, schema-drift guard | Passed |
| `myra_technical.db` mtime | **2026-10-17 11:55:59 — unchanged** |
| Indicator lake | **0 files** — no artifact created |
| OHLCV cache | **3320 files, newest mtime 2026-05-02 — unchanged** |
| Scheduler | 0 python processes, port 8000 idle — **not started** |
| `data_readiness.assess()` | `partial`; TriggerScanner **operational**; V2.5 **blocked** (`lake_empty`) |
| `MYRA_V25_RANKING_INPUTS` | not enabled |

One unrelated hunk appears in `myra_app/librarian.py`: the pinned black
reformatted the pre-existing `search_symbols` dict literal. The committed file at
HEAD was **already** non-compliant with black 26.10.1 (verified with
`black --check` against `git show HEAD:...`), so this is drift correction the
repo's own hook mandates, in a function this phase did not otherwise touch.

---

## 6. Consumers intentionally left unchanged

- **`results_manager` charts.** Its correct semantics are genuinely ambiguous: a
  price chart may legitimately show actual traded levels (unadjusted) or a
  continuous adjusted history. Choosing would be a guess. Left on the legacy
  default and documented. Charts will continue to show the pre-ex-date scale.
- **`backtest_engine`.** Already reads `technical_data` directly and therefore
  already operates on adjusted prices. It was **not** re-pointed. Two follow-ups
  remain open and are not F1: rupee-denominated P&L and absolute stop/target
  levels are derived from adjusted prices, which is wrong for level-based
  sizing if those outputs are consumed that way.
- **The OHLCV cache itself.** Still written by the cold-cache path and still
  read by the chart consumer. Retiring it is a data-lifecycle decision, not a
  read-path change.

---

## 7. What this phase did **not** do

**The historical data has not been repaired.** This phase changed the *read
path* only. Specifically, all of the following remain true:

- The **12 degenerate sessions** (2026-08-17 … 2026-10-13, 38,320 rows with
  `sma_50 = close`) still hold fabricated rolling values in `technical_data`.
- The **99.3% of history with NULL `sma_200`** is unchanged.
- The **9 universe-wide weekend-dated sessions** (including the 2026-02-01
  Sunday) are still present.
- The parquet cache still holds unadjusted prices.

F2 (the historical recovery) remains unauthorised and unstarted.

---

## 8. Remaining price-basis uncertainties

1. **Rupee-level semantics in the backtest** — see §6. Needs a separate
   decision on whether any backtest output is consumed as an absolute amount.
2. **Chart semantics** — see §6. Cosmetic; no scoring consequence.
3. **Cache lifecycle** — whether the unadjusted cache should be retired,
   rebuilt, or kept only for charts.
4. **Lake artifact schema** — the canonical frame carries fewer columns than the
   cache frame (the cache had extra cached indicator columns that nothing read).
   Harmless while the lake is empty; worth confirming at the first real build.

---

## 9. Readiness for F2 review

The read path no longer contaminates new indicator work, which was the
precondition F2 depends on: recomputing history over a single basis is now the
default. F2 can be reviewed on that basis. It still requires, per Phase 21 §C:

- F1 executed — **done**.
- The 9 weekend sessions resolved upstream with the eod2 maintainer, or an
  explicit decision to leave them.
- Owner approval for the R0–R4 plan (backup, dry-run on a copy, update, audit).

**Remaining blockers before a controlled indicator-lake validation build:**
the F2 historical recovery, the `atr_pct` product decision (1 of 7 mandatory
inputs), and a decision on `MYRA_V25_RANKING_INPUTS`. See
`docs/PHASE21_V25_READINESS_AND_RECOVERY_PLAN.md` §D.