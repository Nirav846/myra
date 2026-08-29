# MYRA Performance Guide

This guide documents measured performance characteristics and how to keep scans fast.

> **Important:** Scanner latency varies with machine, universe size, and current data. The figures below are representative snapshots, not hardcommitted SLAs. Use `GET /api/pipeline/status` (task durations) and `/api/data-health` as your measurement tools. Capture your own numbers on your machine for the most accurate guidance.

---

## Why scans got faster

Two core optimisations are live in the scanner pipeline:

### 1. Bulk loader (`myra_app/db/bulk_loader.py`)

Historically each scanner opened a **separate SQLite connection and index SELECT per symbol** (~31 ms/symbol) to fetch OHLCV + enrichment rows. The bulk loader replaces that loop with **one bulk SQL query per scan** for the whole universe:

```python
from myra_app.db.bulk_loader import load_ohlcv_for_universe

data = load_ohlcv_for_universe(start_date, end_date, symbols)  # {symbol: DataFrame}
```

- 13 of 15 scanners now use it (Invisible Hand, Trigger, Liquidity Flip, DCB Bargain, Smart Money Bargain, Operator Fingerprint, Float Exhaustion, Seasonal Delivery, Darvas, Wyckoff, Bottom Hunter, Climax Accumulation, Delivery Divergence).
- This eliminated thousands of per-symbol round-trips (on the order of ~83 s saved on a full scan).

### 2. Enrichment backfill (`tools/enrich_history.py`)

The SMC enrichment backfill (~2.2M rows) was rewritten from a slow per-date loop to a **single Polars load + vectorised per-date enrichment + batched writes**:

- Before: ~**20 hours**
- After: ~**57 minutes** (uses `feature_enrichment.enrich_from_dataframe` + batch writes every 50 dates)

### 3. Holding-universe filter

Restricting a scan to your `myra_portfolio.db` holdings (a small symbol set) instead of the full 3,000+ symbol universe dramatically cuts per-scan work.

---

## Representative scan times

Typical wall-clock for a full-universe scan vs a holdings-filtered scan (local machine, representative snapshot):

| Scanner | Full universe | With holding filter |
|---------|--------------|---------------------|
| DCB Bargain | ~58 s | **~8 s** |
| Invisible Hand | ~58 s | **~8–12 s** |
| The Trigger | ~40 s | ~5–8 s |
| Bottom Hunter | ~30 s | ~5 s |
| (other heavy scanners) | tens of seconds | a few seconds |

> These are order-of-magnitude figures. Actual times depend on hardware and how many symbols pass the market-cap universe gate. Use the streamed `progress` field (10–92%) to observe scan progress live.

---

## Optimisation tips

1. **Use the holding-universe filter** — the single biggest lever. If you only care about your portfolio, filter to holdings and a full scan becomes a few seconds.
2. **Widen the market-cap range mindfully** — a larger `_get_universe()` means more symbols to slice and score; tighten it when possible.
3. **Reuse caches** — every scanner caches its JSON result. A repeat `POST /{name}/scan` with unchanged params reads from cache instead of recomputing. Only clear when you want fresh logic to re-run (`DELETE /api/cache/{name}`).
4. **Keep enrichment current** — scanners are meaningless on stale columns. If `/api/data-health` shows `enrichment_complete_pct < 100`, run enrichment so scanners don't re-derive indicators.
5. **Batch data access** — when writing new code, never query within a per-symbol loop. Use `load_ohlcv_for_universe` and slice per-symbol in memory.
6. **WAL mode** — keep SQLite WAL on (never `journal_mode=DELETE`) for concurrent scanner + pipeline access.

---

## Measuring your own numbers

- **Per-task durations:** `GET /api/pipeline/status` returns `duration` for each pipeline task (logged by `myra_app/tasks/executor.py` via `time.perf_counter`).
- **Data freshness:** `GET /api/data-health` shows `latest_ohlcv_date`, `days_behind`, and enrichment %.

---

## Test-suite performance

- 367 tests collect in ~20 s (`pytest tests/ --collect-only`).
- CI runs the full `tests/` suite on Python 3.12 plus a frontend build (Node 22) on every push/PR to `main`.

---

## Spring `spring_score` weight calibration

`tools/calibrate_wyckoff_weights.py` systematically calibrates the six
`DEFAULT_SPRING_WEIGHTS` (delivery_absorption, lower_wick, close_location,
grab_depth, equal_low_bonus, two_candle_bonus) that feed the Spring
`spring_score` against forward returns.

### Methodology

- **Data:** `technical_data` only (no fundamentals — leak-free, correctly
  time-aligned). 400-symbol sample from the 510–530,000 Cr universe, 12 scan
  dates, seed 42.
- **Signal flow:** reuses the backtest harness' event detection; each weight set
  reprobes candidates and re-scores `spring_score` via the scanner's own static
  helpers (pinned by `tests/test_wyckoff_calibration.py`).
- **Split:** chronological 70/15/15 TRAIN/VALIDATION/HOLDOUT by scan date.
  Search selects on TRAIN only; the holdout never influences selection. No
  cross-split (symbol, event_date) leakage (guarded by `_verify_no_leak`).
- **Search:** random (default 800 combos, deterministic seed) or grid; base
  weights normalised to sum 100, bonuses added on top.
- **Gate:** PROCEED only if the selected set's VALIDATION Q5-Q1 > 0 **and**
  beats the shipped defaults' VALIDATION Q5-Q1; otherwise ABANDON and keep the
  current weights.

### Result (2026-08, re-verified 2026-08-29)

| Split | Best-on-TRAIN set | Shipped defaults |
|-------|-------------------|------------------|
| TRAIN Q5-Q1  | +13.68% | +5.22% |
| **VALIDATION Q5-Q1** | **−2.14%** | **+11.21%** |
| HOLDOUT Q5-Q1 | +0.49% | +4.06% |

The best-on-TRAIN set overfits: its VALIDATION Q5-Q1 (−2.14%) is far **below**
the shipped defaults' +11.21% (gap −13.35%). **ABANDONED** — no candidate
passed the out-of-sample gate, so the shipping weights (30/30/20/10 + 10/5)
remain optimal. This is the correct outcome of a leak-free calibration: the
hand-tuned weights generalise better than any searched combination.

The training/holdout split is by **event cohort** (first-detectable scan date),
not by return-window calendar — a TRAIN event's 120-day forward-return window
may extend into later calendar periods, but each event belongs to exactly one
split's label set and the holdout/validation labels never influence selection.

### Output

Every evaluated weight set (TRAIN + VALIDATION metrics) is written to
`tools/calibrate_wyckoff_weights_output.csv` (gitignored).

---

## Related reading

- [USER_GUIDE.md](USER_GUIDE.md) — how to run scans and filters
- [ARCHITECTURE.md](ARCHITECTURE.md) — bulk loader + enrichment pipeline internals
