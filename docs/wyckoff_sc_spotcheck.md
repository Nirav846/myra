# Wyckoff SC Spot-Check (Post Look-Ahead Fix)

Manual verification that 12 Selling Climax (SC) events emitted by the `WyckoffAutomaton` scanner genuinely satisfy the SC gates without look-ahead bias. Each event's window row-count is in **[59, 99]** (see `rows_in_window`) - these only exist post-fix; the old code required a >=100-row window due to the calendar-vs-trading-day row miscount bug, so these events were never detectable before the fix.

## Method

The 12 events were sampled via `random.Random(42).sample` from the `tools/backtest_wyckoff.py --dump-sc` output (400-symbol sample, seed 42, 12 scan dates). For each event the raw OHLCV slice for the symbol over the **exact scan window** `[scan_date - 90d, scan_date]` was independently rebuilt and the gate conditions were recomputed:

1. **Volume gate**: `volume > 1.8 x expanding-mean(volume)`
2. **Range gate**: `close <= expanding-min(low) x 1.15`
3. **Delivery gate**: `delivery_pct > 40`
4. **No look-ahead (prefix-independence)**: the same baselines are recomputed from a window truncated to `event_date` (all post-event rows dropped). If the baseline at the signal candle is byte-identical with and without future rows, no future data influenced the signal. `_detect_events()` is also re-run on the truncated df where feasible.

## Results

| # | Symbol | Event date | Vol | Range | Del | Baseline stable | Re-detect survive | Row cnt | Pass/Fail |
|---|--------|------------|-----|-------|-----|-----------------|-------------------|---------|-----------|
| 1 | ENIL | 2025-12-24 | Y | Y | Y | Y | guarded | 61 | PASS |
| 2 | CONCOR | 2025-08-07 | Y | Y | Y | Y | Y | 65 | PASS |
| 3 | BAJAJFINSV | 2025-06-09 | Y | Y | Y | Y | guarded | 61 | PASS |
| 4 | TVSELECT | 2025-09-12 | Y | Y | Y | Y | guarded | 62 | PASS |
| 5 | INDORAMA | 2025-07-23 | Y | Y | Y | Y | guarded | 62 | PASS |
| 6 | APTUS | 2025-08-01 | Y | Y | Y | Y | guarded | 63 | PASS |
| 7 | SADHNANIQ | 2025-07-10 | Y | Y | Y | Y | guarded | 63 | PASS |
| 8 | JINDALSTEL | 2026-01-28 | Y | Y | Y | Y | guarded | 63 | PASS |
| 9 | SHAKTIPUMP | 2025-06-06 | Y | Y | Y | Y | guarded | 65 | PASS |
| 10 | KCP | 2025-12-19 | Y | Y | Y | Y | guarded | 63 | PASS |
| 11 | SVLL | 2025-10-24 | Y | Y | Y | Y | guarded | 62 | PASS |
| 12 | GHCL | 2025-07-15 | Y | Y | Y | Y | guarded | 65 | PASS |

**Summary: 12 PASS / 0 FAIL** (all 12 must PASS or be explicitly flagged below).

## Independent vs recorded values

| # | Symbol | Event date | vol_ratio (dump/recomp) | range_low_90 (dump/recomp) | del_pct (dump/recomp) | baseline (full/trunc) |
|---|--------|------------|------------------------|----------------------------|-----------------------|-----------------------|
| 1 | ENIL | 2025-12-24 | 2.90/2.90 | 109.95/109.95 | 70.5/70.5 | 2.9/2.9 |
| 2 | CONCOR | 2025-08-07 | 2.95/2.95 | 529.20/529.20 | 48.0/48.0 | 2.95/2.95 |
| 3 | BAJAJFINSV | 2025-06-09 | 2.14/2.14 | 1823.50/1823.50 | 43.0/43.0 | 2.14/2.14 |
| 4 | TVSELECT | 2025-09-12 | 2.09/2.09 | 388.35/388.35 | 100.0/100.0 | 2.09/2.09 |
| 5 | INDORAMA | 2025-07-23 | 2.09/2.09 | 49.00/49.00 | 100.0/100.0 | 2.09/2.09 |
| 6 | APTUS | 2025-08-01 | 2.94/2.94 | 316.55/316.55 | 45.9/45.9 | 2.94/2.94 |
| 7 | SADHNANIQ | 2025-07-10 | 11.42/11.42 | 5.89/5.89 | 100.0/100.0 | 11.42/11.42 |
| 8 | JINDALSTEL | 2026-01-28 | 1.87/1.87 | 977.10/977.10 | 46.1/46.1 | 1.87/1.87 |
| 9 | SHAKTIPUMP | 2025-06-06 | 3.30/3.30 | 827.00/827.00 | 45.4/45.4 | 3.3/3.3 |
| 10 | KCP | 2025-12-19 | 2.08/2.08 | 172.30/172.30 | 56.7/56.7 | 2.08/2.08 |
| 11 | SVLL | 2025-10-24 | 5.35/5.35 | 754.00/754.00 | 99.2/99.2 | 5.35/5.35 |
| 12 | GHCL | 2025-07-15 | 1.85/1.85 | 568.00/568.00 | 55.7/55.7 | 1.85/1.85 |

## Data sources

- **OHLCV + delivery**: `technical_data` in `myra_app/db/myra_technical.db` (via `load_ohlcv_for_universe` / `rows_for_symbol`, `COLUMNS_13`).
- **Scanner logic**: `myra_app/strategies/wyckoff_automaton.py` `_detect_events()`.
- **Inspection date**: 29 Aug 2026 (max technical_data date 2026-08-26).
