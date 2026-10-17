# V2.5 Technical-Signal Contract

**Status:** canonical, as of Phase 19.
**Scope:** every value a published `MYRA_Score_v25` consumes — technical,
fundamental and contextual — with its producer, its missing-data behaviour, and
whether its definition is decided or still open.
**Companion:** `docs/PHASE19_V25_TECHNICAL_SIGNAL_CONTRACT.md` (what was repaired,
what was verified, and what remains an operator decision).

This document exists because "the lake is missing a few columns" was, on
inspection, four different problems wearing one name:

| Situation | Looks like | Actually is |
|---|---|---|
| The lake has not been built | missing data | a pending build step |
| The lake is built but a field has no writer | missing data | a definitional gap — a build will not supply it |
| A producer exists but is switched off | missing data | an operator switch (changing it changes scores) |
| The field is absent and gets zero-filled | missing data | a fabricated *measurement*, already inside the numbers |

Only the first is fixed by building the lake.

---

## 1. What the score consumes

`PositionalScorer` (`myra_app/positional_engine.py`) weights five technical
components and one fundamental block:

```
score = 0.25·trend + 0.15·stability + 0.20·delivery + 0.10·liquidity + 0.10·base   (technical share 0.7)
      + 0.30·fundamental                                                            (fundamental share 0.3)
score = score × regime_adjustment(regime) × drawdown_multiplier(drawdown)
```

`_BASE_WEIGHTS = {trend 0.25, stability 0.15, delivery 0.20, liquidity 0.10, base 0.10}`,
`_TECH_SHARE = 0.7`, `_FUNDAMENTAL_SHARE = 0.3`. None of these were changed in
Phase 19, and none of them may be changed without an explicit decision.

### The consumed fields

| # | Field | Consumed by | Meaning | Class |
|---|---|---|---|---|
| 1 | `sma50` | `trend_score` | 50-session close mean | Established |
| 2 | `sma150` | `trend_score` | 150-session close mean | **Unresolved** (source) |
| 3 | `sma200` | `trend_score` | 200-session close mean | Established |
| 4 | `close` | `trend_score`, `base_score`, `valuation_score` | Latest close | Established |
| 5 | `pct_above_ma50_60d` | `stability_score` | Fraction of the last 60 sessions with close > MA50 | Established (unit pinned by test) |
| 6 | `avg_delivery_20d` | `_rank_delivery` → `delivery_score` | Mean **daily delivery %** over 20 sessions | Established (definition) / no enabled producer |
| 7 | `smart_money_score` | `_rank_sm_score` → `delivery_score` | Composite 0–1 smart-money signal | **Unresolved** (composite undefined) |
| 8 | `avg_volume_20d` | `_rank_volume` → `liquidity_score` | Mean session volume over 20 sessions | Established / no enabled producer |
| 9 | `atr_pct` | `base_score` | ATR as a fraction of price | **Unresolved** (lookback, and see §4) |
| 10 | `keltner_upper` | `base_score` | Upper Keltner band | **Unresolved** (SMA vs EMA, multiplier) |
| 11 | `keltner_lower` | `base_score` | Lower Keltner band | **Unresolved** (as above) |
| 12 | `atr5` | `base_score` | 5-session ATR | **Unresolved** (definition) |
| 13 | `drawdown` | `PositionalScorer._drawdown_multiplier` | Fraction below the trailing high | **Unresolved** (lookback) |
| 14 | `roe` | `_rank_roe` → `fundamental_score` | Return on equity | Established / no enabled producer |
| 15 | `ProfitGrowth` | `_rank_growth` → `fundamental_score` | Profit growth | Established / no enabled producer |
| 16 | `F_Score` | `fundamental_score` | Piotroski-style 0–5 quality score | **Unresolved** (nothing computes it) |
| 17 | `regime` | `regime_adjustment` | Market regime label | Established |
| — | `graham_number` | `valuation_score` | Graham number | **Not consumed**: `valuation_score` is never used in the composite |
| — | `BookValue`, `PE`, `ROE`, `eps_latest`, `bvps_latest` | `universe_loader` | Passed through for display / other scanners | out of scope here |

Anything not in this table is not a v2.5 input, however loudly it appears in a
`funda_map` dump.

---

## 2. Per-field detail

### 2.1 Established definitions

**`sma50`, `sma200`** — `close.rolling(N).mean()`, N = 50 / 200 sessions,
computed on the *close* series of whatever frame the producer holds.

*Verified against production (read-only, `RELIANCE`, 2026-10-16):*
`technical_data.sma_50` = 1260.508 = `close.rolling(50).mean()` exactly, and a
fresh rolling mean over the lake's own frame also gives **1260.508** — the two
series agree at the tail, which is the row a scan actually reads.
`technical_data.sma_200` = 1345.261; the lake's `rolling(200)` = **1348.4025**.
This 3.1 gap is *not* a definitional disagreement — see §6.2 (two malformed
sessions inside the 200-session window).

**`close`** — the latest close on the scan's as-of date. Established.

**`pct_above_ma50_60d`** — the share of the last 60 sessions whose close exceeded
the 50-session mean, expressed as a fraction (0–1), *not* a percentage. Pinned by
`tests/test_score_components_v2.py:153`. `stability_score` returns the value
directly, so a 0–100 input would saturate the component at 1.0.

**`avg_volume_20d`** — authoritative definition, `myra_app/ranking_inputs.py`:
the mean of `volume` over the 20 most recent sessions, computed over the
*available* values, `None` when fewer than 20 sessions exist or no observation is
in the window. It is never zero-filled by its producer.

**`avg_delivery_20d`** — authoritative definition, same module: the mean of the
**daily delivery percentage** over the 20 most recent sessions (preferring the
ingested `delivery_pct`; otherwise `delivery / volume × 100`), `None` when fewer
than 20 sessions or no observation.

> This is the most misreadable field in the system. The lake emits
> `avg_deliv_20d` = `ta.sma(Delivery_qty, 20)` — a mean of delivery **quantity**.
> Those are different quantities with different units, and the lake's version is
> **all-NaN** on the live data (`delivery_qty` is absent from `technical_data`
> and ~7% populated in the OHLCV cache), so wiring it into `avg_delivery_20d`
> would have substituted a null for a measurable value *and* looked like a fix.
> The name similarity is the trap; the consumer wants the percentage.

**`roe`, `ProfitGrowth`** — passed through from `fundamentals.roe` /
`fundamentals.profit_growth` exactly as stored (`0` stays `0`; only SQL `NULL`
becomes `None`).

**`regime`** — `EXTREME FEAR` ×1.15, `EXTREME GREED` ×0.9, otherwise ×1.0.

### 2.2 Unresolved definitions

These require an investment-strategy decision that the repository cannot settle
from evidence. **No code was written for them, and none of them may be guessed at
implementation time** — an invented definition produces numbers that look exactly
like measured ones.

| Field | The decision | Options on the table |
|---|---|---|
| `sma150` | Which series is the source of truth? | (a) compute from the lake's close, as `sma50`/`sma200` are; (b) persist a `sma_150` column in `technical_data` and read it; (c) replace the 50/150/200 stage rule with a 50/100/200 variant |
| `atr_pct` | Which ATR, and over what lookback, divided by what price? | (a) ATR(20)/close, matching the lake's `atr20`; (b) a different lookback; (c) true range variant |
| `atr5` | Is a 5-session ATR meaningful at daily granularity, or is this a 1.5-ATR squeeze band? | (a) ATR(5); (b) ATR(20)/4; (c) redefine the squeeze test in terms of the existing `atr20` |
| `keltner_upper`/`keltner_lower` | Centre line and multiplier | (a) SMA20 ± 2·ATR20; (b) EMA20 ± 2·ATR20; (c) a different multiplier |
| `drawdown` | Lookback for the trailing high | (a) 252 sessions; (b) 60; (c) all-history |
| `smart_money_score` | What the composite is made of | (a) `0.6·delivery_rank + 0.4·rdv_rank`; (b) something else — note `delivery_score` *already* blends a smart-money rank at 0.6/0.4, so a composite built from the same ingredients double-counts |
| `F_Score` | Nothing in the repository computes a Piotroski score, and the fundamentals table has neither the inputs nor the name | (a) implement it and persist it; (b) source it externally; (c) drop it from the fundamental block (a scoring change) |
| `graham_number` | — | not required unless `valuation_score` is ever wired in |

---

## 3. The producer ledger

Which module writes which consumed field, today, in this repository:

| Producer | Writes | Enabled? |
|---|---|---|
| `librarian_intelligence._compute_symbol_indicators` → the indicator lake | `sma50`, `sma200` (of the 17) | yes — once the build can write at all (§4 of the phase report) |
| `ranking_inputs.compute_ranking_inputs` → merged by `universe_loader` | `avg_volume_20d`, `avg_delivery_20d` | **no** — behind `MYRA_V25_RANKING_INPUTS`, off by default |
| `ranking_inputs.compute_fundamental_inputs` → merged by `universe_loader` | `roe`, `ProfitGrowth` | **no** — same flag |
| — nothing — | `sma150`, `pct_above_ma50_60d`, `atr_pct`, `atr5`, `keltner_upper`, `keltner_lower`, `drawdown`, `smart_money_score`, `F_Score` | n/a |

The last row is the load-bearing one. `screening_modes.V25_UNSOURCED_INPUTS`
names exactly those fields so a consumer never has to re-derive the list, and
`V25_GATED_INPUTS` names the two that a build will *not* supply because a flag is
off — a distinction the previous single "unsourced" list could not express.

### Mandatory vs optional

A gap in a mandatory input means the score cannot be published at all: the
component it feeds would be arithmetic on a substituted constant. A gap in an
optional input withholds a bonus or a penalty while leaving the score's meaning
intact.

| Class | Fields | If missing |
|---|---|---|
| **Mandatory** (7) | `sma50`, `sma150`, `sma200`, `pct_above_ma50_60d`, `avg_delivery_20d`, `avg_volume_20d`, `atr_pct` | mode is `blocked`, reason `mandatory_inputs_unsourced` |
| **Optional** (5) | `keltner_upper`, `keltner_lower`, `atr5`, `drawdown`, `smart_money_score` | mode is `degraded`, reason `optional_inputs_unsourced` |

The split is enforced in `myra_app/screening_modes.py`
(`V25_MANDATORY_INPUTS`, `V25_OPTIONAL_INPUTS`, `classify_missing_v25_inputs`)
and surfaced in `evaluate()["v25_input_contract"]`.

---

## 4. Missing-value behaviour, and the ATR defect

The scorer's own fallbacks are *documented neutral constants* — `trend 0.3`,
`delivery 0.3`/`0.4`, `liquidity 0.3`, `stability 0.3`, `roe`/`growth 0.4`,
`drawdown 1.0` (no penalty), `valuation 0.5`. They are tolerable because they are
neutral. The problem is upstream of them: `universe_loader` reads the lake with
`c.get(name, 0)`, so "the lake did not measure this" and "the lake measured zero"
both arrive at the scorer as `0`.

Two consequences were traced through the real code paths:

1. **A missing `atr_pct` is rewarded with the maximum base score.** `atr_pct`
   absent → `0` → `base_score` sees `0.0 < 0.03` → `+0.5`, the *maximum*
   formation bonus. Absence scores better than any real value.
2. **A missing `pct_above_ma50_60d` is punished as a worst case.** A `0` here is
   not neutral — `stability_score` reads it as "never above MA50" and returns 0,
   the minimum.

Recorded fix, **deliberately not applied in Phase 19** because it changes
published scores: stop zero-filling `atr_pct` in `universe_loader` and let the
absence reach the scorer's documented neutral fallback — or make `base_score`
distinguish a measured `0.0` from an absence. Either way it is a scoring
behaviour change and needs an explicit decision.

---

## 5. Classification summary

| Class | Fields | Meaning |
|---|---|---|
| **Established** | `sma50`, `sma200`, `close`, `pct_above_ma50_60d`, `avg_volume_20d`, `avg_delivery_20d`, `roe`, `ProfitGrowth`, `regime` | definition settled; the blocker is a producer or a switch |
| **Strong proposal** | `avg_delivery_20d` from the lake (rejected as a substitute, §2.1) | the mapping is decidable, and the evidence says *don't* |
| **Unresolved** | `sma150`, `atr_pct`, `atr5`, `keltner_upper`, `keltner_lower`, `drawdown`, `smart_money_score`, `F_Score` | needs an investment-strategy decision |

**Therefore:** a trustworthy `MYRA_Score_v25` is not a data-volume problem. Nine
of seventeen inputs have no enabled producer, and eight of those have no
producer at all. Building the lake is necessary and not sufficient.

---

## 6. Data-quality findings recorded alongside

### 6.1 Two sessions carry a fabricated 50-day mean

On **2026-10-13** and **2026-10-07**, `technical_data` stores `sma_50 = close`
for essentially the whole universe (3220 of 3223 rows, 3223 of 3224) and
`sma_200` as NULL for **every** symbol (3221 / 3224). Every other recent session
shows ≤7 such rows. The latest session (2026-10-16) is clean (0 NULL in both
columns).

A scan that reads the latest row is unaffected. A 50- or 200-session window that
*contains* those dates is not — which is exactly the 3.1 gap in §2.1.

### 6.2 The OHLCV cache and `technical_data` disagree by a factor of 2

For `RELIANCE`, over the 1216 sessions present in both, **736 differ** and the
ratio is **2.0000** (median exactly 2, mean 1.9975) for every session from
2021-10-01 up to and including **2026-02-01**; from 2026-02-02 onward the two
series agree exactly. The parquet OHLCV cache also carries 73 dates that
`technical_data` does not have for the symbol.

So the lake's price series is **not** the database's price series: one of them is
corporate-action adjusted and the other is not. Any moving average spanning the
boundary is inconsistent, and a lake built from this cache reconciles with
nothing in `technical_data`. Which series is authoritative is a decision, not a
bug fix — and it is upstream of every indicator in §2.1.

### 6.3 `technical_data.delivery` is a delivery *quantity*, not a percentage

Verified on the latest session: `delivery / volume × 100 == delivery_pct` for
**3190 of 3190** rows, while `delivery_qty` is non-null in **0 of 8,022,725**
rows. The column name invites exactly the misreading that §2.1 warns about, and
`delivery_ma_60` is a 60-session mean of the delivery *percentage* (first
non-null index 59, magnitude 59.55) — it is not a 20-session quantity mean and is
not interchangeable with one.

---

## 7. Open decisions

1. Authorise (or reject) the four execution repairs in the phase report.
2. Authorise the B5 lake-write repair **before** any lake build is attempted —
   without it the build fails for every symbol.
3. Resolve the eight unresolved definitions in §2.2.
4. Decide the missing-`atr_pct` zero-fill in §4.
5. Decide whether `MYRA_V25_RANKING_INPUTS` should ever be enabled, given it
   changes published ordering.
6. Decide the price-basis question in §6.2 before trusting lake indicators.
7. Decide whether the 2026-10-07 / 2026-10-13 rows are repaired or left, and
   whether the ingestion that wrote them is fixed.

Nothing in this document is authorised for implementation by its own existence.
