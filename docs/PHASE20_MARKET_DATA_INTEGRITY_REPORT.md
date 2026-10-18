# Phase 20 — Market-Data Integrity Investigation & Root-Cause Repair

**Baseline:** `9d658c8` (Phase 19) · **Phase:** 20 · **Scope:** reproduce → root-cause →
repair what is conclusively justified → document the rest.

## Verdict

**NOT READY for a controlled validation build of `MYRA_Score_v25`.**

Two root causes were proven and their forward paths fixed. Neither repair can
make the score trustworthy, and both of the blockers below are operator decisions
rather than bugs. V2.5 stays `MODEBLOCKED` (`mandatory_inputs_unsourced`); the
TriggerScanner remains the usable mode. Nothing published a `MYRA_Score_v25` and
no ranking weight, threshold, or growth default was changed.

- The **price basis** on which the indicator lake would be built is still
  undecided, and it is upstream of every indicator (D3).
- **Six of the seven mandatory V2.5 inputs still have no enabled producer**, and
  four of those have no producer at all (Stage 5, re-verified against code).

## What was reproduced

All three anomalies carried into this phase were reproduced against the live
databases (read-only) and against the code, then either repaired or documented.

## D1 — the daily ingestion fabricated every rolling column (FIXED, forward path)

**Symptom.** Phase 19 found `sma_50 = close` for the whole universe on two
sessions. A full-universe re-scan (not a one-date probe) shows **12** defective
sessions: 6 in 2026-08-17…08-31, 4 in 2026-09 (08/11/21/28) and 2 in 2026-10
(07/13). On every one, all four columns degenerate together —
`sma_50 = close`, `high_52w = high`, `low_52w = low`,
`delivery_ma_60 = delivery_pct` — with `sma_200` NULL for every symbol.
Table-wide: `sma_50 = close` on **40,431** rows; `sma_200` NULL on
**7,965,142 of 8,022,725** rows (99.3%), non-NULL only for the 18 sessions since
2026-09-15.

**Root cause** (`myra_app/eod2_sync.py`). `sync_eod2_data` filtered to the rows
newer than the per-symbol cutoff *before* computing the derived columns:

```python
valid_df = valid_df[valid_df["date"] > sym_cutoff]   # :615  (one new session)
valid_df = _compute_derived(valid_df)                 # :623
```

`_compute_derived` guarded its rolling math behind `if len(df) >= 2` and, for a
shorter frame, stored the raw session values in their place — `sma_50 = close`,
`high_52w = high`, `low_52w = low`, `delivery_ma_60 = delivery_pct`. A normal
daily sync always supplies exactly one new row, so it always took that branch,
for the whole universe. `_INSERT_COLS` also omitted `sma_200` entirely, and
`INSERT OR REPLACE` then nulled every column it did not list.

The enrichment repair could not self-heal this: `feature_enrichment` computes the
true rolling values over full history but writes only
`df[df.date == latest_date]` (`:516`), so a session that was not "latest" at some
enrichment run is wrong forever. It is also best-effort and failure-swallowing.

**Repair.** The sync now loads the symbol's most recent 252 rows (enough for the
longest window) and computes the rolling columns over `history + new rows`,
publishing only the new dates; `_compute_derived` no longer substitutes a raw
price for a rolling statistic (`min_periods=1` already gives the honest
short-history answer); and `sma_200` is both computed and written.

**Also fixed — a silent whole-ingest failure.** Adding `sma_200` to the INSERT
surfaced a real hazard: a table lacking any written column makes *every* symbol's
INSERT raise, which the per-symbol handler downgrades to a skipped symbol, so a
run could report "done" while storing nothing. `_insert_columns` now resolves the
table's real columns and drops absent ones **loudly** (warning), so OHLCV still
lands and the gap is visible. This was caught by an existing recovery fixture,
not theorised.

**Not repaired:** the 12 historical sessions and the 99.3% historical `sma_200`
NULLs — a backfill is a forbidden bulk production operation here. The code that
wrote them is fixed, so they cannot recur. See "Required but not executed".

## D2 — a missing `atr_pct` was rewarded with the maximum base score (FIXED)

**Symptom.** `base_score` reads `atr_pct < 0.03` as a favourable tight base and
adds `+0.5`, the maximum. `universe_loader` emitted
`c.get("atr_pct", 0) if ... is not None else 0`, and **`atr_pct` has no producer
anywhere in the repository** (no lake column, no DB column — re-verified by grep
in Stage 5). So "we measured no volatility" was stored as the numeric `0.0` for
the entire universe and collected the maximum formation bonus — better than a
genuinely mid-range base — and was reported to the user as an *observed* input
rather than a missing one.

This was already characterised in `tests/test_phase15_atr_characterization.py`,
which recorded it as a known, deliberately-unfixed defect pending a separate
decision.

**Repair.** `universe_loader` no longer fabricates the zero; the value passes
through. An absent ATR therefore reaches `base_score` as absent and lands on the
documented `0.3` neutral — identical to what a genuine SQL NULL, a NaN and an
absent key already produced. A real measurement (including a legitimate stored
`0.0`) is preserved unchanged.

**Deliberately minimal:** this is a data-integrity fix at the fabrication site,
not a scoring change. `base_score`'s bands, the ranking formula, and the
`pct_above_ma50_60d` behaviour (which punishes rather than rewards absence, and
which Phase 5 consciously left alone) are unchanged. The Phase 15 characterisation
tests still pass, because they pin `base_score` directly — that is the intended
signal that the scorer contract was not altered.

**Effect on scores:** symbols that previously earned a fabricated `+0.5` now
score the neutral. This is a *reduction* in a component that was never earned;
it is published nowhere and no ranking weight changed.

## D3 — the lake's price series is not the database's price series (DOCUMENTED, not repaired)

**Established.** `technical_data` matches the eod2 CSV source **exactly**
(RELIANCE: 7860/7860 sessions, close max-abs-diff 0.0) and is the adjusted
series. The parquet OHLCV cache is a stale **unadjusted** snapshot: every file's
mtime is ≤ 2026-05-02, its window is fixed at 2021-10-01 … 2026-04-30, and the
per-symbol cache/source ratio is a constant cumulative corporate-action factor —
**2.0** for RELIANCE, **5.0** for ZYDUSWELL, **1.0** for TCS/ITC/SBIN —
identical across open/high/low/close, with volume unscaled and `delivery`
absent. Beyond the split boundary (last differing session 2026-02-01) the two
agree exactly, which reproduces Phase 19's unexplained lake-vs-database
`sma_200` gap (RELIANCE 1348.4025 vs 1345.261) to the decimal.

This is not a fetch, scaling, or ingestion bug: the cache is a pre-split snapshot
that was never rebuilt, and `get_ohlcv`'s save never persists because the write
sits inside a swallowing `try/except`.

**Why it is not repaired here.** Correcting it requires either rebuilding the
cache or changing the lake to read `technical_data` directly. Both are bulk
production operations this phase may not perform, and the *authoritative* series
is an operator decision. Documented, not changed.

**Also recorded (not repaired):** the eod2 source itself contains a row dated
2026-02-01 (a Sunday) and omits 2026-01-31; the cache carries 75 weekend rows
and 73 dates absent from the source. Source correction is upstream of this
repository's inputs.

## Stage 5 — input completeness (re-verified against code)

| Input | Producer today | Class |
|---|---|---|
| `sma50`, `sma200` | lake + (now) the daily sync | established; historical `sma_200` NULL (D1) |
| `sma150` | **none** — read only | unresolved definition |
| `pct_above_ma50_60d` | **none** — zero-filled, absence is *punished* | established / known ambiguity |
| `avg_delivery_20d`, `avg_volume_20d` | behind `MYRA_V25_RANKING_INPUTS`, off by default | operator switch |
| `atr_pct` | **none** | unresolved definition; absence now neutral (D2) |
| `atr5`, `keltner_upper/lower`, `drawdown`, `smart_money_score` | **none** | unresolved definitions |

A few files compute a *local* variable of a matching name for their own strategy
signal (`strategies/alpha_vcp.py` builds an `atr5`; `wyckoff_automaton_dynamic.py`
builds an `atr_pct`) — none persist the consumed column, so the classification
stands. Full detail: `docs/V25_TECHNICAL_SIGNAL_CONTRACT.md` §5.

## Required but not executed (awaiting authorisation)

1. **Cache rebuild / price-basis decision** — choose the authoritative series
   (adjusted `technical_data` is recommended), then either rebuild
   `data/ohlcv_cache` or make the lake read `technical_data` directly. Do this
   before trusting any lake indicator.
2. **Backfill of the defective rolling columns** — recompute `sma_50`, `sma_200`,
   `high_52w`, `low_52w`, `delivery_ma_60` for the 12 sessions of 2026-08/09/10
   (≈39,000 rows) and backfill `sma_200` for the 99.3% of history that lacks it.
   The forward path is fixed; this is historical repair only.
3. **Enable a producer or decide the definition** for the four mandatory inputs
   with no producer, and resolve `MYRA_V25_RANKING_INPUTS`.
4. **Correct the eod2 source's phantom 2026-02-01 session** upstream.

None of these were run. No ingestion, scheduler, or bulk refresh was started.

## Changes

- `myra_app/eod2_sync.py` — rolling columns computed over history + new rows;
  raw-price substitution removed; `sma_200` computed and written; insert columns
  resolved against the real schema (loud on a narrower table).
- `myra_app/universe_loader.py` — no fabricated `atr_pct`.
- `tests/test_eod2_sync.py`, `tests/test_recovery_reconciliation.py` — regression
  coverage; the recovery fixture's DDL now derives from `_INSERT_COLS` so it
  cannot drift again.
- `tests/test_phase20_atr_pct_integrity.py` — new: the `atr_pct` matrix
  (absent/NULL/NaN/legit-zero/real value, missing-input reporting, no band or
  cross-band change).
- `docs/V25_TECHNICAL_SIGNAL_CONTRACT.md` — §3/§4/§5/§6/§7 updated to the
  Phase 20 evidence.

## Verification

- Full suite: **1768 passed** (was 1749; +19 new tests), including the whole
  `eod2_sync` / recovery / ingest set run under `-W error::FutureWarning`.
- `black` (pinned 26.10.1 via `pre_commit`), `flake8`, the MYRA Performance Guard
  and the schema-drift guard all pass.
- Every live-DB query in this phase was **read-only**
  (`sqlite3.connect("file:…?mode=ro", uri=True)`); no production data was
  mutated, and the `live_db_guard` fixture was not weakened.

## Not addressed (out of scope, unchanged)

`pct_above_ma50_60d` absence behaviour (Phase 5's deliberate choice);
`fundamental_sync`'s import-time `MORNINGSTAR_TOKEN` `KeyError`; the absent
`upstox_fetcher`; the no-op `screener_enrich`.