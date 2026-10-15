# Phase 12 — Missing-Data Safety in Investment Decisions

**Date:** 2026-10-15
**Base commit:** `7f2ac8d` ("Close residual hardcoded-API-URL instances of the same defect", Phase 11)
**Branch:** `main`

---

## 1. Verified initial repository state

| Check | Result |
|---|---|
| Branch | `main` |
| HEAD | `7f2ac8d` |
| `origin/main` | `7f2ac8d` |
| Ahead / behind | `0 / 0` |
| Working tree | clean |
| Phase 11 commits | `4009771` and `7f2ac8d` both present as commits |
| Stashes | 3 preserved, untouched |
| Port 8000 | LISTENING (backend) |
| Port 3000 | down (not required by this phase) |

The reported state matched reality. No user changes were present or overwritten.

---

## 2. What each flagged path actually was

Phase 11 flagged four locations. Each was traced from source to consumer before
anything was edited. **Two were genuine defects, two were already correct.**

| Path | Verdict | Detail |
|---|---|---|
| `fund_traction.py` quality score | **GENUINE DEFECT** (3 bugs) | Feeds a **sortable column** in `FundTractionScanner.tsx` |
| `universe_loader.py` | **GENUINE DEFECT** | Fabricated `0` for three columns that are 100% empty |
| `worker_pool.py` smart-money star | **GENUINE DEFECT** (minor) | Fabricated `0`; no decision change today |
| `scanners.py` `COALESCE(market_cap,0)` | **ALREADY CORRECT** | see below |
| `confluence_batch.py` `COALESCE(market_cap,0)` | **ALREADY CORRECT** | see below |
| `screener.py` `ProfitGrowth = 0` | **ALREADY CORRECT** | documented display fallback |

### 2.1 Why the two `COALESCE` filters are *not* defects

```sql
WHERE COALESCE(market_cap, 0) / 1e7 BETWEEN ? AND ?
```

Both `NULL` and `0` fail `> 0` and fail `BETWEEN min AND max` when `min > 0`, so
those rows are excluded either way. The `COALESCE` is redundant, not wrong.
Removing it would be a cosmetic edit to a screening path with no behavioural
difference — precisely the kind of unrelated change this phase should not make.
A test now pins that these files stay as they are, so a future reader does not
"fix" them.

### 2.2 Why `screener.py` was left alone

```python
if r.get("ProfitGrowth") is None:
    r["ProfitGrowth"] = 0
```

Sits under a comment stating this is a *display-compatibility* default, that it
fills gaps only, and never clobbers a real upstream value. It is not a ranking
input. Changing it would alter rendering without improving any evidence.

### 2.3 Defect 1 — fund-traction quality score (three bugs, one expression)

The old expression:

```sql
ROUND(COALESCE(f.roe,0)*0.4 + COALESCE(f.net_margin,0)*0.3
      + (100 - MIN(COALESCE(f.pe,100),100))*0.3, 2)
```

**a. Absent inputs silently scored zero.** Weights were never renormalised over
inputs that actually existed. Of the 3,246 rows with a market cap, **2,769 have
no ROE** (85%), 504 have no P/E, 339 have no `net_margin`. A stock with none of
the three could reach at most 0.3 while a fully measured one reached ~0.7×100.
Being unmeasured was a **de facto penalty**.

**b. Negative P/E inverted the metric's meaning.** `MIN(pe,100)` capped the cheap
end but had **no floor**:

| `pe` | old component |
|---|---|
| −341.7 | **+132.51** |
| −50 | +45.00 |
| 0 | +30.00 |
| 20 | +24.00 |
| 100 | 0.00 |

Fourteen rows have `pe < 0` with a market cap. A **loss-making company received
the largest score in the dataset** — the worst possible quality signal was the
best rewarded one.

**c. Mixed-scale `net_margin` swamped everything.** Observed values run from
~0.13 (fraction) to −26,570 (percent or corrupt). At 0.3 weight, `NECLIFE`
contributed **−5,070** and `RAINBOWPAP` **−7,971**, overwhelming every other
term combined.

### 2.4 Defect 2 — `universe_loader.py` fabricated evidence for the whole universe

```python
"PE":    f.get("pe"),                 # None preserved  (correct)
"ROE":   f.get("roe"),                # None preserved  (correct)
"ProfitGrowth": f.get("profit_growth", 0) or 0,   # NULL -> 0
"SalesGrowth":   f.get("sales_growth", 0) or 0,   # NULL -> 0
"Debt_Equity":   f.get("debt_to_equity", 0) or 0,# NULL -> 0
```

Phase 11 measured all three columns at **0 of 3,948 rows populated**. The default
therefore asserted *"0% profit growth"* and *"zero debt-to-equity"* for **every
symbol in the database**. `SalesGrowth` and `Debt_Equity` have no consumer
anywhere in the repo, so the fabrication was inert — but it was still false
evidence in every emitted result row, and `ProfitGrowth` reaches
`precompute_ranks` whenever Phase 6's opt-in is enabled.

### 2.5 Defect 3 — `smart_money_score`

No producer exists anywhere (Phase 11 confirmed this; re-verified here — no
writer, only readers). `worker_pool.py` did `funda.get("smart_money_score", 0) > 0.7`
to award a star. Behaviourally the star was **never** awarded, so there was no
ranking inversion — but the code fabricated a numeric `0` to compare against, and
`None > 0.7` would raise `TypeError` if the value were ever simply absent.

---

## 3. Corrected missing-data semantics

Applied narrowly, per field, not as a universal policy:

| Input | Missing (`NULL`/`None`) | Valid zero | Invalid |
|---|---|---|---|
| `roe` | component absent, weight redistributed | still `0` — breakeven is real | — |
| `pe` | component absent | `pe = 0` → absent (implies infinite earnings) | `pe < 0` → **absent**, never a bonus |
| `net_margin` | — | — | **excluded entirely** (scale unresolved) |
| `profit_growth`, `sales_growth`, `debt_to_equity` | stays `None` | stays `0.0` | — |
| `smart_money_score` | stays `None` | — | — never a proxy |

**Scale preservation.** The renormalising factor is `total_weight / present_weight`,
so a row with *every* component scores **exactly what it scored before**. This
matters: an earlier attempt used a plain weighted *average*, which moved a
complete row from 34.05 to 48.57 and would have flipped the UI's 0-40 / 40-70 /
70+ bands — a policy change disguised as a bug fix. That version was discarded.

The 4:3 relative emphasis between ROE and P/E is unchanged, and the weights
remain the original 0.4 and 0.3.

---

## 4. Before / after

Synthetic, reproducible (`tools/phase12_decision_impact.py`):

| symbol | old | new | why |
|---|---|---|---|
| `COMPLETE` | 34.05 | **34.00** | essentially unchanged — scale preserved |
| `REAL_ZERO_ROE` | 24.00 | **24.00** | valid zero preserved |
| `NO_ROE` | 24.05 | **56.00** | no longer penalised for being unmeasured |
| `NO_PE` | 10.04 | **17.50** | same |
| `LOSS` (pe −341.7) | **132.51** | **unavailable** | inversion removed |
| `CORRUPT_NM` (−26,570) | **−7,937.00** | **34.00** | corruption no longer dominates |
| `ALL_MISSING` | **0.00** | **unavailable** | fabricated 0 gone |
| `ZERO_PE` | 40.00 | 17.50 | `pe=0` is not a valuation multiple |

Only the two well-formed rows keep their established magnitude.

---

## 5. Measured decision impact (`tools/phase12_real_impact.py`, read-only)

Over the 3,246 `fundamentals` rows with a market cap:

| Measure | Value |
|---|---|
| Rows scored | 3,246 |
| Rows whose score changed | **2,942 (90.6%)** |
| Rows now genuinely **unavailable** (`NULL`) | **481** |
| Top-10 overlap (old vs new by quality) | **4 / 10** |

Largest movements:

| symbol | old | new | cause |
|---|---|---|---|
| `NECLIFE` | −5,070.16 | unavailable | corrupt `net_margin` removed |
| `WINSOME` | −640.29 | unavailable | corrupt `net_margin` removed |
| `WINNY_SME` | 624.98 | 1,040.64 | no-ROE rescale |
| `ESSARSHPNG` | 238.87 | 67.42 | no-ROE rescale |
| `ADVIKCA` | −462.26 | −10.08 | corrupt `net_margin` removed |

Four names left the top 10. **This is not a claim that the new order is
financially better** — it is a statement that an unavailable input no longer
outranks a measured one, and that a loss-making company is no longer the highest
quality score in the dataset.

---

## 6. Fields still untrustworthy

- **`net_margin`** — mixed scales (0.13 fraction vs 699.9 percent vs −26,570).
  Now **excluded** from the quality score. Normalisation requires a source-level
  decision and is **deferred to a separately scoped phase**; no per-row unit was
  guessed.
- **`profit_growth`, `sales_growth`, `debt_to_equity`, `roe_ttm`** — no
  functioning producer (dead `MS_CANONICAL_MAP` entries). Now honestly `None`.
- **`smart_money_score`** — no producer. Stays `None`, never a zero, never a
  proxy, never activated in ranking.

---

## 7. Validation

| Gate | Result |
|---|---|
| Full pytest suite | **1463 passed**, 0 failed |
| Live-database writes from tests | **none** |
| `black` 26.10.1 | clean |
| `flake8` | clean |
| `tsc --noEmit` | clean |
| `npm run build` | success |
| Performance guard | **274 / 0 CRITICAL** (+1, the same `.append()`-in-loop false positive) |

New: `tests/test_missing_data_safety.py` (24 tests) covering `NULL`, `None`,
valid zero, negative `pe`, `pe = 0`, very high `pe`, negative ROE, corrupt
`net_margin`, all-missing, scale preservation, weight preservation, Phase 6
opt-in, `smart_money_score` unavailability, and pins that the three
"already-correct" files stay untouched.

Two of my own test expectations were wrong and were corrected against the
implementation (daily-cadence boundaries in Phase 11; the `HIGH_PE` score and a
source-grep that matched my own explanatory comments in this phase).

**Database safety:** all inspection was read-only (`mode=ro`). The only writes
were to `tmp_path` synthetic fixtures, deleted with their directory. No live
technical or metadata database was used as scratch space. The backend was
stopped for test runs because its scheduler writes to the metadata sidecar, and
restarted once afterwards.

---

## 8. Remaining risks and next step

1. **`net_margin` is the highest-value follow-up.** One data-repair decision
   (source unit + historical reprocessing) would let a genuinely populated
   field — 83.8% coverage — re-enter the score with a real 0.3 weight.
2. **Dead producers** for `profit_growth` / `sales_growth` / `debt_to_equity`.
   Either the Morningstar field list or the write path is broken; a repaired
   producer would make the Phase 6 growth rank meaningful.
3. **481 rows now show "unavailable"** where a number used to appear. That is
   correct, but the UI's empty state for a null `quality_score` should be
   reviewed so operators read it as "not measured", not "broken".
4. **`smart_money_score`** still has no defensible definition; none was invented.

---

## 9. Guardrails honoured

No scoring weights, thresholds or rank directions changed · Phase 6 ranking
inputs remain opt-in and untouched · no invented data or smart-money signals ·
`net_margin` not normalised by guessing · no historical records rewritten · no
unrelated refactoring · no new scheduler · no weakened tests · no database
artifacts committed · push was a normal fast-forward and no pushed commit was
amended.