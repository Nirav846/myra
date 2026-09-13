# MYRA Backtest Engine — Reference Manual

> Single source of truth for the backtest harness. Every claim in this
> document is grounded in a specific file and line number; verify against
> code before modifying behaviour.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Configuration: BacktestConfig](#2-configuration-backtestconfig)
3. [Signal Function Protocol & SIGNAL_REGISTRY](#3-signal-function-protocol--signal_registry)
4. [Two Execution Paths](#4-two-execution-paths)
5. [Exit Modes](#5-exit-modes)
6. [Transaction Cost Model](#6-transaction-cost-model)
7. [PIT Universe & Eligibility](#7-pit-universe--eligibility)
8. [Train / Holdout Split](#8-train--holdout-split)
9. [Validation Checklist](#9-validation-checklist)
10. [How to Add a New Strategy](#10-how-to-add-a-new-strategy)
11. [Historical Bugs & Why They Matter](#11-historical-bugs--why-they-matter)

---

## 1. Architecture Overview

```
myra_app/backtest_engine.py          — core harness (1850 lines)
├── BacktestConfig                   — line 486: all knobs in one dataclass
├── SignalFunction Protocol          — line 119: contract every signal must satisfy
├── SIGNAL_REGISTRY                  — line 471: name → factory mapping
├── run_backtest()                   — line 1111: main entry (single-tranche)
├── _run_backtest_averaging()        — line 1305: multi-tranche averaging path
├── Exit evaluators                  — lines 980–1091: four built-in exits
├── Cost helpers                     — lines 522–564: STT, brokerage, impact
├── _preload_universe_by_date()      — line 632: PIT universe construction
├── _resolve_window()                — line 1099: train/holdout/all date clipping
├── _compute_summary()               — line 1554: aggregate stats from trades
├── compute_mae_mfe()                — line 1651: post-hoc MAE/MFE enrichment
└── retrospective_stop_sweep()       — line 1729: sensitivity sweep on stop levels
```

**Key design principle:** the engine is a *library*, not a CLI. It is called
by `tools/backtest_super_breakout.py`, `tools/mae_mfe_analysis.py`,
`tools/method2_validation.py`, and the live API via `run_backtest_myra()`
(line 1830). There is no `__main__` block — all invocations go through a
driver that owns the database connection.

---

## 2. Configuration: BacktestConfig

`backtest_engine.py:486–509`

```python
@dataclass
class BacktestConfig:
    signal: str = "random"                                  # registry key
    exit_mode: Literal["fixed","trailing","rule","profit_target"] = "fixed"
    fixed_hold_days: int = 60
    trailing_pct: float = 0.20
    rule_stop_pct: float = 0.05
    rule_sma_window: int = 20
    profit_target_pct: float = 0.075
    profit_target_cap_days: int = 252
    window: Literal["train","holdout","all"] = "all"
    requires_delivery: bool = False
    start_date: Optional[str] = None   # override auto window start
    end_date: Optional[str] = None     # override auto window end
    average_in: bool = False           # activate multi-tranche path
    max_tranches: int = 3              # per-symbol tranche cap
    recovery_mult: float = 1.20        # BHM1=1.20, BHM2=1.40
```

**What each field controls:**

| Field | Effect |
|-------|--------|
| `signal` | Key into `SIGNAL_REGISTRY`; determines which `SignalFunction` is instantiated |
| `exit_mode` | Selects the exit evaluator (Section 5) |
| `fixed_hold_days` | Used by `"fixed"` exit; close after exactly N trading days |
| `trailing_pct` | Used by `"trailing"` exit; 20% trailing stop from max-high |
| `profit_target_pct` | Used by `"profit_target"` exit; first close ≥ entry × (1+pct) |
| `profit_target_cap_days` | Used by `"profit_target"` exit; force-close after N trading days |
| `window` | `"train"` → ends 2023-12-31; `"holdout"` → starts 2024-01-01; `"all"` → full range |
| `requires_delivery` | Shifts train start from 2015-01-01 to 2019-10-01 |
| `average_in` | Routes to `_run_backtest_averaging()` when `True` |
| `max_tranches` | Cap on number of tranches per symbol (averaging path only) |
| `recovery_mult` | Kaushik entry threshold multiplier (1.20 or 1.40) |

---

## 3. Signal Function Protocol & SIGNAL_REGISTRY

### SignalFunction Protocol

`backtest_engine.py:119–136`

```python
@runtime_checkable
class SignalFunction(Protocol):
    requires_delivery: bool

    def score(
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: sqlite3.Connection,
    ) -> pd.Series: ...
```

**Contract:**
- Returns a `pd.Series` with `index=symbol`, `value=score` (higher = better).
- Symbols absent from the returned Series are treated as ineligible.
- The engine calls `score()` once per trading day.
- For the single-tranche path, the engine picks the top-1 symbol.
- For the averaging path, `_precompute_kaushik_events()` is called once upfront and the per-day loop does not invoke `score()`.

### SIGNAL_REGISTRY

`backtest_engine.py:471–478`

```python
SIGNAL_REGISTRY: dict[str, Callable[..., SignalFunction]] = {
    "random": RandomSignal,
    "momentum": MomentumSignal,
    "kaushik_boh_m1": KaushikBOHMethod1,
    "kaushik_boh_m1_delivery": KaushikBOHMethod1Delivery,
    "kaushik_boh_m1_delivery_filter": KaushikBOHMethod1DeliveryFilter,
    "kaushik_boh_m2": KaushikBOHMethod2,
}
```

**Runtime rebinding at import time:**
- `strategies/random_control.py:134` — `_register()` rebinds `"random"` → `RandomControl` (sorted universe, deterministic).
- `strategies/pure_momentum.py:247` — `_register()` rebinds `"momentum"` → `PureMomentum`.

### Inline signal implementations (BHM1 variants)

BHM1 signals are defined directly in `backtest_engine.py`:

| Class | Line | `requires_delivery` | Recovery | Notes |
|-------|------|---------------------|----------|-------|
| `KaushikBOHMethod1` | 334 | `False` | 1.20× | Base: 20% recovery, tie-break by -overshoot |
| `KaushikBOHMethod1Delivery` | 394 | `True` | 1.20× | Tie-break by delivery elevation |
| `KaushikBOHMethod1DeliveryFilter` | 404 | `True` | 1.20× | Blocks non-elevated candidates |
| `KaushikBOHMethod2` | 453 | `False` | 1.40× | Higher bar, fewer signals |

**Signal detection:** `_precompute_kaushik_events()` (line 224) pre-computes all signals upfront by streaming the entire `technical_data` table. Module-level cache `_KAUSHIK_CACHE` (line 221) ensures this runs once per process.

### Super Breakout (standalone backtest)

Super Breakout does **not** register in `SIGNAL_REGISTRY`. It has its own entry point: `run_super_breakout_backtest()` in `strategies/super_breakout.py:362`, which accepts a `SuperBreakoutConfig` (line 348), not `BacktestConfig`.

---

## 4. Two Execution Paths

### Path A: `run_backtest()` — Single Tranche

`backtest_engine.py:1111–1302`

Called when `config.average_in == False`. One new position per trading day.

**Day loop (line 1187):**
1. Resolve eligible universe from preloaded map (line 1194).
2. Call `signal_obj.score()` → pick top-1 via `sort_values(ascending=False, kind="mergesort")` (line 1213).
3. Load forward price window for exit evaluation (line 1239).
4. Dispatch to exit evaluator (lines 1250–1268).
5. Compute P&L: `shares = 10,000 / entry_price`, `pnl = shares × (exit − entry)` (lines 1278–1281).
6. Deduct round-trip costs (line 1282–1283).
7. Append trade row (lines 1285–1298).

**Performance:** ADV cache is refreshed every 20 trading days (line 1219). Universe preloading eliminates 2,200+ per-day SQL round-trips (line 1180).

### Path B: `_run_backtest_averaging()` — Multi-Tranche

`backtest_engine.py:1305–1540`

Called when `config.average_in == True`. Up to `max_tranches` entries per symbol.

**Key differences from Path A:**
1. **Pre-computed signals:** Calls `_precompute_kaushik_events()` directly (line 1366), not `signal_obj.score()`.
2. **Exits run every trading day** for all open positions (line 1437), not just signal days.
3. **Blended entry price:** Share-weighted harmonic mean `total_invested / total_shares` (lines 1519–1525). Profit target applies to this blended price.
4. **252-day cap re-anchors** to the most recent tranche (line 1471).
5. **Top-1 new entry is never blocked** by existing held positions (lines 1487–1503).
6. **Average-in:** If a signaling symbol is already held AND `n_tranches < max_tranches`, a new tranche is added (lines 1505–1526).
7. **Per-tranche costs:** Each tranche round-trips its own ₹10k allocation (lines 1397–1408).
8. **Capital tracking:** `peak_capital` is a real rupee peak over all open tranches (lines 1528–1531).

**Additional trade columns:** `n_tranches`, `blended_basis`, `tranche_dates`, `tranche_prices` (lines 1421–1424).

**Lazy price cache:** `_close_map()` (line 1380) provides per-symbol `(date → close)` lookup for positions that stay open >252 days.

---

## 5. Exit Modes

All exit evaluators are in `backtest_engine.py:980–1091`. Each returns `(exit_idx, reason_string)`.

### Fixed Holding Period

`_exit_fixed_holding()` — line 980

Close at exactly N trading days after entry. Reason: `"fixed_{n}d"` or `"fixed_window_end"`.

### Trailing Stop

`_exit_trailing_stop()` — line 993

20% trailing stop from highest high since entry. Ratchets up; never moves down. Reason: `"trailing_stop"` or `"trailing_eod"`.

### Rule-Based

`_exit_rule_based()` — line 1017

Either: 5% stop from entry price, OR close < 20-day SMA (trend break). Reasons: `"rule_stop_5pct"`, `"rule_trend_break_sma20"`, `"rule_eod"`.

### Profit Target

`_exit_profit_target()` — line 1058

First close ≥ entry × (1 + pct). If target never hit, force-close after cap_days. Reasons: `"pt_target_{bp}bp"`, `"pt_252d_cap"`, `"pt_eod"`.

### Super Breakout Exits (standalone)

`strategies/super_breakout.py` defines its own exit evaluators:

| Exit Mode | Function | Line | Behaviour |
|-----------|----------|------|-----------|
| `fixed` | `_exit_fixed_target()` | 217 | First close ≥ entry × (1+target_pct). Cap at cap_days. |
| `ma_trail` | `_exit_ma_trailing()` | 235 | **Two-phase:** Protective stop: close < SMA(50) before 2% profit → immediate exit (`"protective_stop_50ma"`). MA trailing: once 2% first reached, trail on SMA(sma_window) → exit when close < SMA(sma_window) (`"ma_trail_sma{window}"`). |
| `atr_trail` | `_exit_atr_trailing()` | 284 | Same two-phase protective stop. ATR trailing: exit when close < highest_close − N × ATR(14) (`"atr_trail_n{atr_n}"`). |

---

## 6. Transaction Cost Model

`backtest_engine.py:52–62`

```python
COST_MODEL = {
    "stt_pct_sell_side": 0.025,       # 0.025% sell-side only
    "brokerage_flat_inr": 20.0,        # flat ₹20
    "brokerage_pct": 0.03,             # 0.03%
    "impact_k": 0.001,                 # 0.1% (sqrt model)
    "impact_fallback_pct": 0.005,      # 0.5% flat fallback
}
```

### Cost functions (lines 522–564)

| Function | Line | Formula |
|----------|------|---------|
| `calc_stt()` | 522 | `sell_value × 0.025 / 100` (sell-side only) |
| `calc_brokerage()` | 527 | `min(20, trade_value × 0.03 / 100)` |
| `calc_impact_cost()` | 535 | If ADV: `pos_value × 0.001 × sqrt(pos_value / adv_value)`. If ADV missing: `pos_value × 0.005` |
| `total_round_trip_costs()` | 545 | Entry: brokerage + impact (no STT on buy). Exit: STT + brokerage + impact. Returns `{stt, brokerage, impact, total}` |

---

## 7. PIT Universe & Eligibility

### Point-in-Time Universe

`backtest_engine.py:632–867` — `_preload_universe_by_date()`

**The problem:** Historical backtests must use the *per-date* composition of the eligible universe, not a static snapshot. A stock that was delisted in 2020 must not appear in the 2018 eligible set even if it was relisted later.

**Solution: Interval-union eligibility** (lines 778–819):
- For each symbol, eligibility = union of `[d_row, d_row + 90d]` windows over all rows where it has data.
- Gaps > 90d (delisted-and-relisted, long trading halts) produce **disjoint intervals**.
- Each interval emits one `(start, end)` event pair for the sweep.

**PIT intersection** (lines 854–862):
```python
if pit_universe is not None:
    for day_iso in trading_days:
        pit_syms = set(pit_universe.get(day_iso, []))
        if pit_syms:
            out[day_iso] = [s for s in out[day_iso] if s in pit_syms]
```

The PIT universe file is `.backtest_scratch/_phase4_mcap_top500_corrected.json`, loaded by `_load_pit_universe()` in each tool script (e.g., `tools/backtest_super_breakout.py:46`).

**⚠ Important:** `mcap_rank_daily` is a live-only, current-snapshot table. It must NEVER be used to approximate historical universe membership. Historical work always uses the offline PIT reconstruction.

### Eligibility rules (applied per day)

| Rule | Implementation | Line |
|------|----------------|------|
| Instrument type = EQUITY | `symbols_master.instrument_type` filter | 660–672 |
| Recent data | `d_row` within 90-day window | 778–821 |
| No blackout | ±5 trading days around discontinuity events | 698–752 |
| PIT intersection | Per-date top-N list from offline JSON | 854–862 |

### mcap_rank_daily (live scanner universe)

`myra_app/mcap_rank_builder.py` — builds daily market-cap ranks in `myra_scoring.db`. Used by scanners for *live* universe only, not historical backtests.

Six embedded corporate-action corrections (lines 82–89) handle stock splits that weren't reflected in stored prices.

---

## 8. Train / Holdout Split

### Date boundaries

`backtest_engine.py:68–71`

```python
TRAIN_START_PRICE_ONLY = "2015-01-01"
TRAIN_START_DELIVERY   = "2019-10-01"
TRAIN_END              = "2023-12-31"
HOLDOUT_END            = "2026-09-04"
```

### Window resolution

`_resolve_window()` (line 1099) → `run_backtest()` (lines 1137–1143):

| `window` | Start | End |
|----------|-------|-----|
| `"train"` | `2015-01-01` (price) or `2019-10-01` (delivery) | `2023-12-31` |
| `"holdout"` | `2024-01-01` | `2026-09-04` |
| `"all"` | `2015-01-01` or `2019-10-01` | `2026-09-04` |

**Delivery-aware start:** If `requires_delivery == True`, train always starts from `2019-10-01` regardless of price-only start. This is enforced both in `_resolve_window()` and in the day-loop skip (line 1190).

---

## 9. Validation Checklist

This checklist is derived from how MYRA strategies were actually validated. Each step is backed by specific tests or tools in the codebase.

### Step 1: Fire frequency analysis

Count signals by day, by symbol, and by train/holdout window.

**Tool:** `tools/backtest_super_breakout.py:65–123` — `fire_frequency()`

Returns:
```python
{
    "total_signal_days": ...,
    "total_entries": ...,
    "unique_symbols": ...,
    "train_signal_days": ...,
    "holdout_signal_days": ...,
    "avg_entries_per_signal_day": ...,
}
```

**Why this matters:** A signal that fires on 3,000 days is not selective. A signal that fires on 12 days is suspiciously rare. Both are red flags.

### Step 2: Train / holdout window sanity

Verify that trades respect the date boundaries.

**Tests:** `tests/test_backtest_components.py:610–675` (class `TestTrainHoldoutSplit`)
- `test_train_window_price_only_starts_2015` (line 613)
- `test_train_window_delivery_starts_2019_10` (line 621)
- `test_holdout_window_2024_to_latest` (line 627)
- `test_train_window_end_filter_is_2023_12_31` (line 641)
- `test_holdout_window_start_filter_is_2024_01_01` (line 661)

### Step 3: MAE/MFE post-hoc diagnostics

Enrich trades with maximum adverse excursion (MAE) and maximum favorable excursion (MFE).

**Function:** `backtest_engine.py:1651` — `compute_mae_mfe()`

**Tool:** `tools/mae_mfe_analysis.py` — full MAE/MFE report by variant × target.

**Key questions answered:**
- How many losing trades were profitable at some point? (MFE > 0 on a net-losing trade)
- What is the loss distribution shape?
- How many trades hit a given stop level?

**Tests:** `tests/test_backtest_components.py:1327–1494` (class `TestComputeMaeMfe`)

### Step 4: Retrospective stop sweep

Sweep fixed stop levels (2%–30%) over MAE-enriched trades. Count winners killed vs losers stopped at each level.

**Function:** `backtest_engine.py:1729` — `retrospective_stop_sweep()`

**Tests:** `tests/test_backtest_components.py:1502–1598` (class `TestRetrospectiveStopSweep`)
- `test_known_mae_values` (line 1505): verifies winners_killed / losers_stopped counts.
- `test_survivor_avg_return` (line 1574): full-population baseline vs survivor average.

### Step 5: Hand-verified test cases

Create synthetic data where the expected output is known analytically.

**Example:** `tests/test_bottom_hunter_m1.py:45` — `test_detect_hand_verified_crossing()`:
> 252 flat bars, dip to 80, recover to 96.2 → crossing on the recovery day with year_low 80, threshold 96.0, overshoot = 0.2083%.

### Step 6: Selection stability & deterministic tie-break

Verify that the random control and momentum signals produce identical results regardless of input universe ordering.

**Tests:** `tests/test_backtest_components.py:706–774` (class `TestSelectionStability`)
- `test_random_control_output_index_is_sorted` (line 722)
- `test_random_control_is_order_invariant` (line 734)
- `test_top1_picker_breaks_ties_alphabetically` (line 745)

### Step 7: Scanner parity

Verify that live scanners and backtest code produce identical results on the same input.

**Tests:** `tests/test_super_breakout_scanner.py:284–352` (class `TestCanonicalSignalDetection`)
- `test_scanner_uses_canonical_not_inline` (line 340): structural test that inspects source code to verify the scanner calls `detect_super_breakout_signals()`.

**Tests:** `tests/test_bulk_loader.py:129–212` (class `TestScannerBulkParity`)
- `_scanner_parity()` helper runs each scanner via both bulk and per-symbol DB paths, asserts identical results.

### Step 8: Out-of-sample gate

A strategy must pass the validation gate on the holdout window to be shipped.

**Example from Wyckoff weight calibration** (`docs/PERFORMANCE.md:95–116`):
> Gate: PROCEED only if the selected set's VALIDATION Q5-Q1 > 0 AND beats the shipped defaults' VALIDATION Q5-Q1; otherwise ABANDON and keep the current weights.

---

## 10. How to Add a New Strategy

This section uses **Super Breakout** as the worked example, since it was the most recent strategy added (with its own backtest loop).

### Option A: Register in SIGNAL_REGISTRY (for strategies that fit the single-tranche model)

1. **Define the signal class** implementing `SignalFunction`:

```python
class MySignal:
    requires_delivery = False

    def score(self, date, universe, conn):
        # Return pd.Series with index=symbol, value=score
        ...
```

2. **Register in SIGNAL_REGISTRY** at `backtest_engine.py:471`:

```python
SIGNAL_REGISTRY: dict[str, Callable[..., SignalFunction]] = {
    ...,
    "my_signal": MySignal,
}
```

3. **Run:**
```python
cfg = BacktestConfig(signal="my_signal", exit_mode="profit_target", ...)
result = run_backtest(conn, cfg)
```

### Option B: Standalone backtest (for strategies that need custom multi-tranche logic)

Super Breakout followed this path because it needed:
- Multiple candidates per day (`max_positions_per_day: int = 3`)
- Custom exit evaluators (protective stop + MA/ATR trailing)
- Delivery-precondition context gating

1. **Define `SuperBreakoutConfig`** (`strategies/super_breakout.py:348–359`):
```python
@dataclass
class SuperBreakoutConfig:
    ranking: Literal["self_relative", "raw_delivery"] = "self_relative"
    exit_mode: Literal["fixed", "ma_trail", "atr_trail"] = "fixed"
    ...
    max_positions_per_day: int = 3
```

2. **Implement signal detection** as a standalone function (`detect_super_breakout_signals()`, line 104).

3. **Implement exit evaluators** as standalone functions (`_exit_fixed_target()`, `_exit_ma_trailing()`, `_exit_atr_trailing()`).

4. **Implement `run_super_breakout_backtest()`** (`line 362`) that:
   - Resolves the same window constants (`TRAIN_START_PRICE_ONLY`, `TRAIN_END`, `HOLDOUT_END`)
   - Calls `_compute_summary()` from the engine for summary metrics

5. **Implement live scanner** (`super_breakout_scanner.py`) that:
   - Calls the same `detect_super_breakout_signals()` function (canonical detection)
   - Has its own state persistence (`super_breakout_state.py`)
   - Replays gaps bar-by-bar through the same exit evaluators

### Key invariants for any strategy

| Invariant | Enforcement |
|-----------|-------------|
| Signal detection must be shared between backtest and scanner | Structural test: `test_scanner_uses_canonical_not_inline` |
| Gap replay must match backtest evaluator | Parity test: `TestScannerGapReplay` |
| Exit evaluation on live positions must match backtest | O3 parity gate in scanner tests |
| Universe eligibility must be PIT | `_preload_universe_by_date()` with `pit_universe` argument |

---

## 11. Historical Bugs & Why They Matter

Three bugs were found and fixed during the MYRA backtest engine's development. Each one changed the measured performance of strategies and is documented here as a reminder of what to guard against.

### Bug 1: Blended Cost Basis (Arithmetic Mean → Harmonic Mean)

**Commit:** `30d8478` (Sep 11, 2026)

**Problem:** In the averaging path (`_run_backtest_averaging()`), `pos['blended']` was computed as the arithmetic mean of tranche prices. For multi-tranche trades, arithmetic mean *overstates* the cost basis, inflating the exit trigger and delaying exits.

**Example:** Tranche 1 at ₹100, Tranche 2 at ₹80:
- Arithmetic mean: ₹90 → profit target at ₹99 (9.0% above ₹90)
- Harmonic mean: ₹88.89 → profit target at ₹97.78 (10% above ₹88.89)

**Fix** (`backtest_engine.py:1519–1525`):
```python
total_invested = POSITION_VALUE_INR * pos["n_tranches"]
total_shares = sum(POSITION_VALUE_INR / p for p in pos["tranche_prices"])
pos["blended"] = total_invested / total_shares
```

**Impact:** On PIT large-cap universe: 0 exit-day changes. On full universe: +₹113K aggregate PnL swing.

**Guard test:** `tests/test_backtest_components.py:1359` — `test_multi_tranche_uses_share_weighted_basis`.

---

### Bug 2: Fast/Slow Driver Divergence (Selection Instability)

**Commit:** `20dd11e` (Sep 6, 2026)

**Problem:** Two code paths through the engine (canonical slow vs. optimized fast driver) passed the eligible universe list in different orders to `RandomControl.score()`. Since `RandomControl` iterated the universe in caller order and the engine used `Series.idxmax()`, different "random" winners were picked despite identical underlying universes.

**Fix** (`strategies/random_control.py:103–114`):
```python
sorted_universe = sorted(universe)
rng = random.Random(day_seed)
scores = [rng.random() for _ in range(len(sorted_universe))]
return pd.Series(scores, index=pd.Index(sorted_universe, name="symbol"))
```

Plus deterministic tie-break in the engine (`backtest_engine.py:1213`):
```python
sorted_scores = scores.sort_values(ascending=False, kind="mergesort")
top_sym = sorted_scores.index[0]
```

**Regression check:** Two independent engine runs on the same 230-day slice produce identical trades (225/225 match).

**Guard tests:** `tests/test_backtest_components.py:706–774` (class `TestSelectionStability`).

---

### Bug 3: Super Breakout Signal Detection Duplication

**Commit:** Aug 17, 2026 (scanner refactor)

**Problem:** The Super Breakout scanner previously had its own inline SMA crossover logic, duplicating the canonical `detect_super_breakout_signals()` function. This meant the scanner and backtest could disagree on whether a signal fired on a given day.

**Fix:** Scanner now calls `detect_super_breakout_signals()` directly (`super_breakout_scanner.py:261`).

**Guard test:** `tests/test_super_breakout_scanner.py:340` — `test_scanner_uses_canonical_not_inline`:
```python
def test_scanner_uses_canonical_not_inline(self):
    import inspect
    from myra_app.strategies.super_breakout_scanner import SuperBreakoutScanner
    source = inspect.getsource(SuperBreakoutScanner.scan)
    assert "sma50 = pd.Series(closes).rolling" not in source
    assert "detect_super_breakout_signals" in source
```

---

## Appendix: Key Constants

| Constant | File | Line | Value | Purpose |
|----------|------|------|-------|---------|
| `TRAIN_START_PRICE_ONLY` | `backtest_engine.py` | 68 | `"2015-01-01"` | Earliest date for price-only signals |
| `TRAIN_START_DELIVERY` | `backtest_engine.py` | 69 | `"2019-10-01"` | Earliest date for delivery-aware signals |
| `TRAIN_END` | `backtest_engine.py` | 70 | `"2023-12-31"` | End of training window |
| `HOLDOUT_END` | `backtest_engine.py` | 71 | `"2026-09-04"` | End of holdout window |
| `RECENT_TECH_WINDOW_DAYS` | `backtest_engine.py` | 74 | `90` | Recency filter for eligibility |
| `BLACKOUT_HALF_WINDOW` | `backtest_engine.py` | 75 | `5` | Discontinuity exclusion ±N days |
| `POSITION_VALUE_INR` | `backtest_engine.py` | 78 | `10,000` | Fixed position sizing |
| `KAUSHIK_LOOKBACK` | `backtest_engine.py` | 207 | `252` | Rolling 52-week window |
| `KAUSHIK_RECOVERY_MULT` | `backtest_engine.py` | 208 | `1.20` | BHM1 recovery threshold |
| `ATR_TRAIL_N` | `strategies/super_breakout.py` | 62 | `2.5` | ATR multiplier for SB trailing |
