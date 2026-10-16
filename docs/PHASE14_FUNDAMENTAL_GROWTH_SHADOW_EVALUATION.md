# Phase 14 — Fundamental Growth: validated definition + preliminary shadow ranking

**Verdict.** The annual growth definition is sufficiently validated to encode and
test. The ranking evidence is **not** sufficient to authorize live activation.

This phase added an additive, read-only growth signal module, an opt-in mode on
the existing shadow comparison tool, tests, and this record. It did **not**
activate growth in the live ranking path, change any scoring weight or
threshold, write to any production table, run enrichment, or build a backtesting
system.

---

## 1. The question

Phase 12 established that `fundamentals.profit_growth` / `sales_growth` were
written as a fabricated `0` for the entire universe, and that the fix was to
leave them `None`. Phase 13 then recorded that these fields *do* have a source
(`upstox_income_statement`) but deliberately left them unpopulated, because
"populating them feeds the ranking pipeline - that turns on a signal".

Phase 14 asks the question that must be answered before such a decision: what do
those numbers actually mean, how many symbols can be measured honestly, and what
would populating them do to the ranking?

## Scope guardrails actually observed

Read-only against every production database; no live enrichment run; no
backtesting framework; no historical rewrite; no scoring-weight or threshold
change; no automatic activation; no scheduler or UI change; no unavailable value
silently converted to zero.

## 1. Verified source contract

Re-derived from the live database and `schema/valuation.sql`, not inferred from
the task summary.

| Property | Verified value |
|---|---|
| Table | `upstox_income_statement` (valuation DB), PK `(isin, period, report_type, time_period)` |
| Rows / symbols | 17,771 / 2,456 |
| `time_period` split | `yearly` 9,186 · `quarterly` 8,585 |
| `report_type` / `units_in` | `consolidated` on all 17,771 rows · `crore` on all 17,771 |
| Period labels | 99 distinct, **0 malformed**; year range 2001–2026 |
| Fiscal year ends | **Mar** 26 · **Dec** 23 · **Jun** 23 · **Sep** 22 · **Nov** 5 |
| `fetched_at` | 2026-08-31T06:38:27 → 2026-09-01T03:50:37 (+00:00) |
| Integrity | 0 symbols with more than one ISIN; 0 duplicate `(symbol, period)` annual rows; 0 NULL `revenue` / `net_profit` in annual rows |
| `net_profit_change` format | `+10.18%` / `-8.63%`; 0 non-conforming values |

### The definition

**Year-on-year growth = `(current - prior) / abs(prior) * 100`, in percent.**

`abs(prior)` is the provider's own convention, not an invention: a recovery from
a loss reads positive. It was confirmed by recomputing every annual pair and
comparing with the provider's own `net_profit_change` string —
**2,166 of 2,166 eligible symbols agree** to within 0.02pp. That is the strongest
available independent check on both the formula and the sign convention.

### Quarterly data cannot produce an annual YoY figure

Within the same `(symbol, report_type)` series, `quarterly` rows with a lag of 1
are quarter-on-quarter (6,378/6,399 agreement), and a lag of 4 — the
same-quarter-prior-year pair that YoY needs — yields **0 pairs**: the source does
not store them. Mixing the two would silently compare different statistics.
Only `time_period = 'yearly'` is therefore admitted, and quarterly rows can never
supply a missing prior period.

### Fiscal year ends are company-specific

Company fiscal years end in Mar, Dec, Jun, Sep and Nov. Assuming a December
year-end — or a single global calendar — would mis-pair most of the universe.
The derivation evaluates each fiscal-end month as its own cohort and keeps the
cohort containing the most recent annual period.

## 2. Eligibility contract

Implemented in `myra_app/growth_signals.py` as a **pure function** over records,
so the whole contract is unit-testable without a database. An observation that
cannot be computed is returned with `profit_growth = None` plus a machine-readable
reason; it is **never** coerced to `0`. Every exclusion is attributed by
`coverage_report()`.

| Rule | Reason when refused |
|---|---|
| A prior annual period must exist | `no_prior_period` |
| Only `time_period='yearly'` may be used | `no_annual_records` (for quarterly-only symbols) |
| The two annual periods must be consecutive years, same fiscal-end month | `non_consecutive_periods` |
| Records disagreeing about one period are unresolvable | `conflicting_records` |
| Latest period must be recent (default: within 1 year of the newest annual year in the source) | `stale_period` |
| Latest record's `fetched_at` must be present and parseable | `missing_freshness_metadata` |
| `net_profit` present for both periods | `missing_values` |
| Prior base must be non-zero | `zero_prior_base` |
| **Prior-period base must be ≥ ₹1 crore** (default) | `prior_base_below_floor` |

The ₹1 crore floor is applied to the **stored prior-period base**, an input —
never to the resulting growth value. Growth measured off a near-zero denominator
is not a comparable cross-sectional observation, but discarding results by
magnitude alone would hide genuine business changes behind a statistical filter.
The threshold and the freshness window are explicit, documented and configurable
(`EligibilityPolicy`).

Valid **zero** and **negative** growth are preserved as real observations —
`0.0` is a real "flat" reading and is distinguishable from "unavailable".

## 3. Coverage: verified against the live source

**2,166 of 2,456 symbols pass the gate** (88.2%).

| Exclusion | Symbols |
|---|---|
| `stale_period` | 162 |
| `no_prior_period` | 63 |
| `prior_base_below_floor` | 55 |
| `non_consecutive_periods` | 8 |
| `no_annual_records` | 2 |
| `conflicting_records`, `missing_values`, `zero_prior_base`, `missing_freshness_metadata` | 0 |

**The pre-implementation estimate was 2,170 / 2,454 and has been corrected to
2,166 / 2,456.** The two extra symbols are the 2 with no annual rows at all; the
eligible count moved by 4 because the implemented gate adds the consecutive-year
requirement (8 symbols whose newest two filings are not adjacent years, e.g.
Mar 2024 + Mar 2026 with Mar 2025 absent) and because the cohort-aware freshness
and base tests attribute a handful of symbols differently. The implemented
figure is the one to quote.

**Production `fundamentals` fill rates (verified read-only, 3,948 rows):**

| Column | Non-null |
|---|---|
| `profit_growth` | **0** |
| `sales_growth` | **0** |
| `debt_to_equity` | **0** |
| `roe_ttm` | **0** |
| `roe` | 691 |
| `net_margin` | 3,309 |
| `market_cap` | 3,246 |

So today growth contributes the scorer's documented neutral **0.4** to every
symbol: **growth has no influence on the live ranking at all.** `SalesGrowth` is
not even a scorer input — `precompute_ranks` reads only `roe`, `ProfitGrowth`,
`avg_delivery_20d`, `avg_volume_20d` and `smart_money_score` — so revenue growth
is derived and reported for completeness but can move nothing.

## 3. Shadow comparison (preliminary)

`tools/compare_v25_orderings.py --growth-source upstox-annual` compares the live
baseline ordering against one with the same frame and the validated growth input
added. Both orderings are computed from the **same** result set inside
`shadow_ranking.compare_orderings`, so the only difference between them is the
growth input. The mode is opt-in; the default invocation fetches no growth data
and is unchanged.

**Baseline construction.** A saved snapshot carries the Phase 6 opt-in inputs and
the pre-Phase-12 placeholder columns. A *live* scan with the deployed default
supplies none of them, so the tool removes `roe`, `ProfitGrowth`, `SalesGrowth`,
`avg_volume_20d`, `avg_delivery_20d`, `smart_money_score` and the precomputed
rank columns before scoring. Without that step the "baseline" is whatever the
file happens to contain, not the production ordering.

**Frame.** `myra_reports/scan_rs_rating.csv`, **174 days old**, 938 rows, of which
only **222** have a populated technical block (`close`/`sma50`/`sma150`/`sma200`);
196 of those 222 have a valid growth observation.

| | Baseline (growth absent) | Candidate (validated YoY) |
|---|---|---|
| Rows scored | 222 | 222 |
| Growth coverage | 0 / 222 | **196 / 222** |
| Spearman | 1.0 (self-consistency) | **0.9489** |
| Top-20 overlap | 20 / 20 | **6 / 20** (Jaccard 0.176) |
| Moved / max displacement | 0 / 0 | **216 / 56** |

Entered the top 20 (14): `ADANIENSOL, BAJAJCON, BELRISE, BLISSGVS, CHENNPETRO,
CRAFTSMAN, IPCALAB, LAURUSLABS, LUPIN, MRPL, NAVINFLUOR, NLCINDIA, TATASTEEL,
TITAN`. Left the top 20 (14): `ANANDRATHI, APARINDS, FEDERALBNK, GAEL, GESHIP,
GLENMARK, GPIL, GRANULES, GVT&D, HINDALCO, INDIANB, POWERINDIA, RBLBANK, VEDL`.

Representative moves: `GPIL 8→64 (+56)`, `HINDALCO 15→71 (+56)`,
`JSWSTEEL 78→26 (−52)`, `MRPL 45→0 (−45)`, `ADANIENSOL 49→4 (−45)`,
`INDIANB 1→46 (+45)`.

## 4. Why the earlier 938-row comparison was discarded

The first attempt compared all 938 rows and reported Spearman 0.667 with
position swings of up to ±724. That result was an artefact and was not carried
forward:

- the snapshot's `ProfitGrowth` is **938 filled, 1 distinct value — all `0.0`**
  (the pre-Phase-12 `f.get(col, 0) or 0` default). A **constant** column still
  ranks at the *maximum* percentile, so the baseline awarded every symbol the
  best possible growth rank; supplying real values for a subset then produced a
  collapse that had nothing to do with growth;
- only **225 of 938** rows have any real technical block; the remaining ~713 are
  identical, which is why the file's own `MYRA_Score_v25` has just **16 distinct
  values** with a **725-row tie block at 25.2**. Position deltas inside a tie
  block measure tie-breaking order, not score;
- the same degeneracy is visible in the tool's default mode on this file
  (Spearman 0.6628, moved 926/938, max displacement **753**), which independently
  confirms the file is not a usable basis for a fine-grained ranking claim.

## 4. Limitations (explicit)

1. **Preliminary and stale.** The only available snapshots are from April 2026.
   A saved snapshot is **not** a live-representative result, and this one is
   restricted to 222 usable rows. Nothing here should be read as a live forecast.
2. **The measured effect is an upper bound.** Because the faithful live baseline
   has *all* Phase 6 inputs absent, growth is the **only** populated input in the
   candidate. In a real flag-on run growth would share influence with ROE,
   volume and delivery, so its marginal effect would be smaller than measured.
   This is the isolated comparison the phase asked for, **not** a model of the
   fully-populated path — the scorer was deliberately not refactored to
   accommodate it.
3. **Absolute scores are not live-representative.** `atr_pct` is a constant `0`
   in the snapshot, so `base_score` contributes a constant +0.5 to every row.
   Harmless for relative ordering (it is common to all rows) but the printed
   scores are not what a live run would show.
4. **No alpha claim.** The result says a validated growth input reorders the
   ranking materially. It does **not** say the new order is financially better,
   and no return, hit-rate or risk statistic was computed.
5. **26 of 222 frame symbols fall back to neutral** because they have no eligible
   observation, so a small part of the change is "still missing", not "measured".

## 5. Deferred finding — `atr_pct` missing-data reward (NOT fixed here)

`universe_loader` writes `atr_pct` as `0` when the value is absent, and
`base_score` treats `atr_pct < 0.03` as a very tight (favourable) base, adding
+0.5. A missing ATR therefore *rewards* the row rather than leaving it neutral —
the same class of defect as the `ProfitGrowth = 0` sentinel, on a different
field. It is recorded here and **explicitly deferred**; it is out of Phase 14's
scope and was not changed. It did not affect the comparison above, because the
value is constant across the frame.

## 6. Tests

`tests/test_growth_signals.py` — **44 tests**, all green. Covers: the exact YoY
arithmetic including recovery-from-loss and valid zero; negative growth; all five
fiscal year ends; cohort selection when a company has two series; missing prior
period; non-consecutive annual records; prior base below ₹1 crore (and the
configurable floor); zero prior base; stale periods; missing/unparseable
freshness metadata (and the waiver); malformed period labels; duplicate
identical records collapsing; conflicting records; quarterly-only symbols and
quarterly rows being unable to supply a prior period; missing growth never
becoming zero; the read-only URI; the source adapter against the real DDL; and
tool regressions — the default mode unchanged and fetching no growth data, the
growth mode performing **no writes** (asserted via `total_changes`), the
baseline frame stripping every opt-in field without mutating its input, and
source eligibility being reported separately from frame coverage.

No test opens a production database; `tests/conftest.py`'s live-database guard
enforces that across the whole session.

## 7. Final recommendation

**The annual growth definition is sufficiently validated to encode and test, but
the ranking evidence is insufficient to authorize live activation.**

Encoding it was worthwhile: it pins a definition that was previously only an
unverified string format, it quantifies the honest coverage (2,166 of 2,456),
and it demonstrates that populating growth would reorder the ranking materially —
which is precisely why it must not be switched on quietly.

A **fresh, technically complete scan snapshot** should be generated and the same
tool re-run against it as a separate validation step. That is a prerequisite for
any activation decision, not a reason to expand Phase 14 into a new backtesting
system.
