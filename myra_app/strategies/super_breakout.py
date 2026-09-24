"""
Modified Super Breakout — backtest signal + exit evaluators.

Entry (all conditions on the SAME bar):
  1. Precondition: close > SMA(5) AND close > SMA(10) AND close > SMA(15)
  2. Trigger (discrete): close[t-1] < SMA(50)[t-1] AND close[t] >= SMA(50)[t]
  3. Context: close[t] < SMA(200)[t] AND SMA(50)[t] < SMA(200)[t]

Cooldown: after a signal at price P, no re-signal until close < SMA(50)
(i.e. price must first close back below the 50MA, resetting the breakout).

Tie-break (when multiple symbols qualify same day):
  - "self_relative": score = (close * delivery_qty) / trailing_20d_mean(close * delivery_qty)
  - "raw_delivery":  score = close * delivery_qty

Exit variants:
  1. Fixed target: profit_target_pct, no trailing. Cap at 252 days.
  2. MA trailing: trail at SMA(20) once position first closes >= entry * 1.02.
  3. ATR trailing: trail at highest_close - N * ATR(14), same activation.

Protective stop (all variants): if close < SMA(50) BEFORE position first
closes >= entry * 1.02, exit immediately. Deactivates once 2% profit is
first reached.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from myra_app.backtest_engine import (
    BacktestConfig,
    BacktestResult,
    COST_MODEL,
    HOLDOUT_END,
    POSITION_VALUE_INR,
    TRAIN_END,
    TRAIN_START_PRICE_ONLY,
    _compute_summary,
    _empty_summary,
    _load_adv_window,
    _load_prices_window,
    _preload_universe_by_date,
    _trading_days,
    calc_brokerage,
    calc_impact_cost,
    calc_stt,
    total_round_trip_costs,
)

# ── SMA windows ──────────────────────────────────────────────────────────────
SMA_SHORT_WINDOWS = (5, 10, 15)
SMA_FAST = 50
SMA_SLOW = 200
ATR_WINDOW = 14
DELIVERY_BASELINE_WINDOW = 20
PROTECTIVE_STOP_ACTIVATION_PCT = 0.02  # 2% — protective stop deactivates after this
ATR_TRAIL_N = 2.5  # multiplier for ATR trailing stop


def check_precondition_context(
    close: float,
    sma5: float,
    sma10: float,
    sma15: float,
    sma50: float,
    sma200: float,
) -> bool:
    """Check whether a bar satisfies the Super Breakout precondition + context.

    Precondition: close > SMA(5) AND close > SMA(10) AND close > SMA(15).
    Context: close < SMA(200) AND SMA(50) < SMA(200).

    Returns True if all conditions hold (the symbol is a candidate for the
    SMA(50) crossover trigger, but has NOT necessarily crossed yet).
    """
    if close <= sma5 or close <= sma10 or close <= sma15:
        return False
    if close >= sma200:
        return False
    if sma50 >= sma200:
        return False
    return True


# ── Signal detection (precomputed sweep) ─────────────────────────────────────

def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Fast rolling mean via cumsum. NaN for first (window-1) elements."""
    n = len(arr)
    out = np.full(n, np.nan)
    if n < window:
        return out
    cs = np.cumsum(arr)
    out[window - 1] = cs[window - 1] / window
    out[window:] = (cs[window:] - cs[:-window]) / window
    return out


def detect_super_breakout_signals(
    conn: sqlite3.Connection,
    universe_by_date: dict[str, list[str]],
    ranking: Literal["self_relative", "raw_delivery"] = "self_relative",
) -> dict[str, list[tuple[str, float]]]:
    """Scan technical_data for super breakout signals.

    Only loads symbols present in the PIT universe for efficiency.
    Returns {date_iso: [(symbol, score), ...]}.

    ranking:
      - "self_relative": score = (close * delivery_qty) / trailing_mean(close * delivery_qty, 20d)
      - "raw_delivery":  score = close * delivery_qty (unnormalized)
    """
    # Collect all unique symbols from PIT universe
    pit_symbols = set()
    for syms in universe_by_date.values():
        pit_symbols.update(syms)
    if not pit_symbols:
        return {}

    # Only load PIT symbols
    placeholders = ",".join("?" for _ in pit_symbols)
    cur = conn.execute(
        f"SELECT symbol, date, close, COALESCE(delivery_qty, delivery, 0) as del_qty "
        f"FROM technical_data "
        f"WHERE symbol IN ({placeholders}) "
        f"ORDER BY symbol, date",
        tuple(pit_symbols),
    )

    signals: dict[str, list[tuple[str, float]]] = {}
    cur_sym: Optional[str] = None
    dates: list[str] = []
    closes: list[float] = []
    delQtys: list[float] = []
    in_cooldown = False

    def _flush(sym: str) -> None:
        nonlocal in_cooldown
        if not dates or len(dates) < SMA_SLOW + 1:
            return
        c = np.asarray(closes, dtype=float)
        dq = np.asarray(delQtys, dtype=float)
        n = len(dates)

        # Vectorized SMA computation via cumsum
        sma5 = _rolling_mean(c, 5)
        sma10 = _rolling_mean(c, 10)
        sma15 = _rolling_mean(c, 15)
        sma50 = _rolling_mean(c, 50)
        sma200 = _rolling_mean(c, 200)

        # Delivery value and baseline
        dval = c * dq
        dval_mean_full = _rolling_mean(dval, DELIVERY_BASELINE_WINDOW)
        # Shift baseline by 1 (exclude current day)
        dval_baseline = np.full(n, np.nan)
        dval_baseline[1:] = dval_mean_full[:-1]

        # Cooldown sweep
        in_cooldown = False
        for i in range(SMA_SLOW, n):
            if np.isnan(sma50[i]) or np.isnan(sma200[i]):
                continue
            if in_cooldown:
                if c[i] < sma50[i]:
                    in_cooldown = False
                else:
                    continue
            # Precondition + context (shared with near-trigger)
            if not check_precondition_context(
                c[i], sma5[i], sma10[i], sma15[i], sma50[i], sma200[i]
            ):
                continue
            # Trigger: discrete crossover above SMA(50)
            if c[i - 1] >= sma50[i - 1]:
                continue
            if c[i] < sma50[i]:
                continue
            # Score
            if ranking == "self_relative":
                bl = dval_baseline[i]
                if np.isnan(bl) or bl <= 0:
                    continue
                score = float(dval[i] / bl)
            else:
                score = float(dval[i])
            if score > 0:
                signals.setdefault(dates[i], []).append((sym, score))
            in_cooldown = True

    for sym, d, c_val, dq in cur:
        if sym != cur_sym:
            if cur_sym is not None:
                _flush(cur_sym)
            cur_sym = sym
            dates = []
            closes = []
            delQtys = []
            in_cooldown = False
        dates.append(d)
        closes.append(float(c_val) if c_val is not None else np.nan)
        delQtys.append(float(dq) if dq is not None else 0.0)

    if cur_sym is not None:
        _flush(cur_sym)

    return signals


# ── Exit evaluators (custom for this strategy) ────────────────────────────────

def _exit_fixed_target(
    pos_prices: pd.DataFrame,
    entry_idx: int,
    target_pct: float,
    cap_days: int = 252,
) -> tuple[int, str]:
    """Fixed profit target, no trailing. Cap at cap_days."""
    n = len(pos_prices)
    entry_close = float(pos_prices["close"].iloc[entry_idx])
    trigger = entry_close * (1.0 + target_pct)
    closes = pos_prices["close"].to_numpy()
    cap_idx = min(entry_idx + cap_days, n - 1)
    for i in range(entry_idx + 1, cap_idx + 1):
        if float(closes[i]) >= trigger:
            return i, f"fixed_target_{int(target_pct*100)}pct"
    return cap_idx, "fixed_cap"


def _exit_ma_trailing(
    pos_prices: pd.DataFrame,
    entry_idx: int,
    sma_window: int = 20,
) -> tuple[int, str]:
    """MA-based trailing: exit when close < SMA(sma_window).

    Protective stop: if close < SMA(50) BEFORE first 2% profit, exit immediately.
    Once 2% profit is first reached, protective stop deactivates and trailing activates.
    """
    n = len(pos_prices)
    if entry_idx >= n - 1:
        return n - 1, "ma_trail_eod"

    closes = pos_prices["close"].to_numpy()
    entry_close = float(closes[entry_idx])
    activation_price = entry_close * (1.0 + PROTECTIVE_STOP_ACTIVATION_PCT)

    # Compute SMA(50) for protective stop and SMA(sma_window) for trailing
    sma50 = (
        pos_prices["close"]
        .rolling(window=SMA_FAST, min_periods=SMA_FAST)
        .mean()
        .to_numpy()
    )
    sma_trail = (
        pos_prices["close"]
        .rolling(window=sma_window, min_periods=sma_window)
        .mean()
        .to_numpy()
    )

    ever_activated = False
    for i in range(entry_idx + 1, n):
        c = float(closes[i])
        # Check if ever reached activation threshold
        if not ever_activated and c >= activation_price:
            ever_activated = True
        if not ever_activated:
            # Protective stop: close < SMA(50)
            if not math.isnan(sma50[i]) and c < sma50[i]:
                return i, "protective_stop_50ma"
        else:
            # Trailing: close < SMA(sma_window)
            if not math.isnan(sma_trail[i]) and c < sma_trail[i]:
                return i, f"ma_trail_sma{sma_window}"
    return n - 1, "ma_trail_eod"


def _exit_atr_trailing(
    pos_prices: pd.DataFrame,
    entry_idx: int,
    atr_n: float = ATR_TRAIL_N,
    atr_window: int = ATR_WINDOW,
) -> tuple[int, str]:
    """ATR-based trailing: exit when close < highest_close_since_entry - N * ATR(14).

    Protective stop: same as MA trailing — close < SMA(50) before 2% profit.
    """
    n = len(pos_prices)
    if entry_idx >= n - 1:
        return n - 1, "atr_trail_eod"

    closes = pos_prices["close"].to_numpy()
    highs = pos_prices["high"].to_numpy()
    lows = pos_prices["low"].to_numpy()
    entry_close = float(closes[entry_idx])
    activation_price = entry_close * (1.0 + PROTECTIVE_STOP_ACTIVATION_PCT)

    # Compute ATR(14)
    tr_list = []
    for i in range(n):
        if i == 0:
            tr_list.append(float(highs[i]) - float(lows[i]))
        else:
            h = float(highs[i])
            l = float(lows[i])
            pc = float(closes[i - 1])
            tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
    tr_arr = np.array(tr_list)
    atr = pd.Series(tr_arr).rolling(atr_window, min_periods=atr_window).mean().to_numpy()

    # Compute SMA(50) for protective stop
    sma50 = (
        pos_prices["close"]
        .rolling(window=SMA_FAST, min_periods=SMA_FAST)
        .mean()
        .to_numpy()
    )

    ever_activated = False
    highest_close = entry_close
    for i in range(entry_idx + 1, n):
        c = float(closes[i])
        if c > highest_close:
            highest_close = c
        if not ever_activated and c >= activation_price:
            ever_activated = True
        if not ever_activated:
            # Protective stop
            if not math.isnan(sma50[i]) and c < sma50[i]:
                return i, "protective_stop_50ma"
        else:
            # ATR trailing stop
            if not math.isnan(atr[i]):
                floor = highest_close - atr_n * atr[i]
                if c < floor:
                    return i, f"atr_trail_n{atr_n}"
    return n - 1, "atr_trail_eod"


# ── Full backtest loop ────────────────────────────────────────────────────────

@dataclass
class SuperBreakoutConfig:
    ranking: Literal["self_relative", "raw_delivery"] = "self_relative"
    exit_mode: Literal["fixed", "ma_trail", "atr_trail"] = "fixed"
    fixed_target_pct: float = 0.10  # 10% default for fixed target
    fixed_cap_days: int = 252
    ma_trail_window: int = 20
    atr_trail_n: float = ATR_TRAIL_N
    window: Literal["train", "holdout", "all"] = "all"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_positions_per_day: int = 3  # top-N per day


def run_super_breakout_backtest(
    conn: sqlite3.Connection,
    config: SuperBreakoutConfig,
    pit_universe: Optional[dict[str, list[str]]] = None,
) -> BacktestResult:
    """Run Modified Super Breakout backtest with the given config.

    Each signal is evaluated independently: load forward price window, apply
    exit evaluator, record trade. Multiple signals on the same day are all
    taken (up to max_positions_per_day), matching the spec's multi-candidate
    requirement.
    """
    # Resolve window
    start_date = config.start_date or TRAIN_START_PRICE_ONLY
    end_date = config.end_date or HOLDOUT_END
    if config.window == "train":
        end_date = TRAIN_END
    elif config.window == "holdout":
        start_date = "2024-01-01"
        if not config.end_date:
            end_date = HOLDOUT_END

    trading_days = _trading_days(conn, start_date, end_date)
    if not trading_days:
        return BacktestResult(trades=pd.DataFrame(), summary=_empty_summary())

    # Preload universe
    universe_by_date = _preload_universe_by_date(
        conn, trading_days, pit_universe=pit_universe
    )

    # Detect all signals
    raw_signals = detect_super_breakout_signals(conn, universe_by_date, config.ranking)

    # Filter signals to PIT universe
    signals: dict[str, list[tuple[str, float]]] = {}
    for day_iso, candidates in raw_signals.items():
        eligible = set(universe_by_date.get(day_iso, []))
        filtered = [(s, sc) for s, sc in candidates if s in eligible]
        if filtered:
            signals[day_iso] = filtered

    # ADV cache
    adv_cache: dict[str, float] = {}
    trades: list[dict] = []
    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    for day_iso, candidates in sorted(signals.items()):
        # Sort by score descending, take top-N
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = candidates[: config.max_positions_per_day]

        for sym, score in top_candidates:
            day_idx = day_to_idx.get(day_iso)
            if day_idx is None:
                continue
            # Load forward window: entry day + max forward days
            max_forward = (
                config.fixed_cap_days + 5
                if config.exit_mode == "fixed"
                else 260
            )
            fwd_end_idx = min(day_idx + max_forward, len(trading_days) - 1)
            fwd_end = trading_days[fwd_end_idx]
            pos_prices = _load_prices_window(conn, [sym], day_iso, fwd_end)
            if pos_prices.empty or len(pos_prices) < 2:
                continue
            pos_prices = pos_prices.sort_values("date").reset_index(drop=True)
            # Verify first row is the entry day
            # Scalar Timestamp — .dt accessor N/A; NaT-raise behavior is intentional here.
            first_date = pos_prices["date"].iloc[0].strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
            if first_date != day_iso:
                # Find the entry row
                date_strs = pos_prices["date"].dt.strftime("%Y-%m-%d").tolist()
                if day_iso in date_strs:
                    entry_idx = date_strs.index(day_iso)
                else:
                    continue
            else:
                entry_idx = 0

            entry_price = float(pos_prices["close"].iloc[entry_idx])
            if entry_price <= 0 or np.isnan(entry_price):
                continue

            # Choose exit evaluator
            if config.exit_mode == "fixed":
                exit_idx, reason = _exit_fixed_target(
                    pos_prices, entry_idx, config.fixed_target_pct, config.fixed_cap_days
                )
            elif config.exit_mode == "ma_trail":
                exit_idx, reason = _exit_ma_trailing(
                    pos_prices, entry_idx, config.ma_trail_window
                )
            else:  # atr_trail
                exit_idx, reason = _exit_atr_trailing(
                    pos_prices, entry_idx, config.atr_trail_n
                )

            exit_price = float(pos_prices["close"].iloc[exit_idx])
            # Scalar Timestamp — .dt accessor N/A; NaT-raise behavior is intentional here.
            exit_date = pos_prices["date"].iloc[exit_idx].strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
            n_hold = exit_idx - entry_idx

            # P&L
            shares = POSITION_VALUE_INR / entry_price
            exit_value = shares * exit_price
            pnl_gross = exit_value - POSITION_VALUE_INR
            # ADV for impact cost
            if day_idx % 20 == 0 or sym not in adv_cache:
                adv_cache.update(
                    _load_adv_window(conn, [sym], day_iso, window=20)
                )
            adv_val = adv_cache.get(sym)
            costs = total_round_trip_costs(POSITION_VALUE_INR, exit_value, adv_val)
            pnl_net = pnl_gross - costs["total"]

            trades.append(
                {
                    "entry_date": day_iso,
                    "exit_date": exit_date,
                    "symbol": sym,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "n_hold_days": n_hold,
                    "pnl_gross": pnl_gross,
                    "costs": costs["total"],
                    "pnl_net": pnl_net,
                    "exit_reason": reason,
                }
            )

    trades_df = pd.DataFrame(trades)
    bc = BacktestConfig(signal="super_breakout", window=config.window)
    summary = _compute_summary(trades_df, bc)
    return BacktestResult(trades=trades_df, summary=summary)
