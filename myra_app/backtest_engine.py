"""
MYRA Backtest Engine — Phase 1, Task 1.

A standalone, pluggable backtest harness for evaluating trading signals
against historical MYRA data. Designed to:

  * Read universe + prices from existing MYRA SQLite sidecars (technical,
    meta, institutional).
  * Accept an arbitrary `SignalFunction` (price-only or delivery-aware).
  * Support three independent exit modes:
      1. Fixed holding period (N trading days).
      2. 20% trailing stop from max-high-since-entry.
      3. Rule-based (5% stop OR trend break below 20d SMA).
  * Apply NSE-style frictions (STT, brokerage, impact) on each entry/exit.
  * Run on train / holdout / all windows with `window='train'|'holdout'|'all'`.

Output: per-trade DataFrame + summary metrics.

NOTE — Distribution/Markdown Exit
---------------------------------
After reviewing the codebase (strategies/wyckoff_automaton.py,
strategies/climax_accumulation.py, etc.) there is no existing reusable
distribution/markdown detector exposed for downstream exit evaluation.
Following the spec, we use the **simplified proxy** for the rule-based exit:
  - close < close_at_entry * 0.95  (5% stop)
  - OR close < 20-day SMA on the exit day (trend break)
"""
from __future__ import annotations

import math
import os
import pickle
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore


# ──────────────────────────────────────────────────────────────────────────────
# COST MODEL — single source of truth for transaction frictions.
# Update these constants and re-run backtests if cost assumptions change.
# ──────────────────────────────────────────────────────────────────────────────
COST_MODEL = {
    # Securities Transaction Tax — NSE delivery equity, sell side only
    "stt_pct_sell_side": 0.025,  # 0.025%
    # Discount broker: lower of flat ₹20 per executed order or 0.03% of trade value
    "brokerage_flat_inr": 20.0,
    "brokerage_pct": 0.03,  # 0.03%
    # Impact cost model: impact = IMPACT_K * sqrt(position_value / ADV_value)
    # ADV_value = ADV_shares * close. If ADV missing -> flat fallback.
    "impact_k": 0.001,  # 0.1%
    "impact_fallback_pct": 0.005,  # 0.5% flat
}


# ──────────────────────────────────────────────────────────────────────────────
# Train / Holdout date boundaries (per spec).
# ──────────────────────────────────────────────────────────────────────────────
TRAIN_START_PRICE_ONLY = "2015-01-01"
TRAIN_START_DELIVERY = "2019-10-01"  # signals using delivery skip earlier dates
TRAIN_END = "2023-12-31"
HOLDOUT_END = "2026-09-04"  # latest available per Phase 0 freshness check

# Universe filter windows
RECENT_TECH_WINDOW_DAYS = 90
BLACKOUT_HALF_WINDOW = 5  # ±5 trading days around each discontinuity event

# Position sizing
POSITION_VALUE_INR = 10_000


# ──────────────────────────────────────────────────────────────────────────────
# Discontinuity cache: precomputed z>6 events without CA match.
# Path is fixed; build script lives in tools/compute_discontinuity.py
# (created alongside this engine in Phase 1 Task 1).
# ──────────────────────────────────────────────────────────────────────────────
DISCONTINUITY_CACHE = (
    Path(__file__).resolve().parents[1]
    / ".agent"
    / "cache"
    / "discontinuity_events.pkl"
)


def _load_discontinuity_events() -> pd.DataFrame:
    """Load precomputed discontinuity events from disk.

    Returns DataFrame with columns ['symbol','date','close','z'] or empty if
    cache missing. Caller decides whether missing cache is fatal.
    """
    if not DISCONTINUITY_CACHE.exists():
        return pd.DataFrame(columns=["symbol", "date", "close", "z"])
    with open(DISCONTINUITY_CACHE, "rb") as f:
        events = pickle.load(f)
    if not isinstance(events, pd.DataFrame):
        return pd.DataFrame(columns=["symbol", "date", "close", "z"])
    # Normalize types
    events = events.copy()
    if not events.empty:
        events["symbol"] = events["symbol"].astype(str)
        events["date"] = pd.to_datetime(events["date"])
    return events


# ──────────────────────────────────────────────────────────────────────────────
# Signal function protocol + default registry.
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class SignalFunction(Protocol):
    """Pluggable signal function.

    `score` must return a pandas Series with index = symbol, value = score
    (higher = better candidate). Symbols absent from the returned Series are
    treated as ineligible.
    """

    requires_delivery: bool

    def score(  # noqa: E704
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: sqlite3.Connection,
    ) -> pd.Series:
        ...


class RandomSignal:
    """Dummy signal: uniform random score. For harness sanity tests only.

    Implementation note: uses ``np.random.default_rng(seed)`` per call so
    deterministic when the caller passes the same seed.
    """

    requires_delivery = False

    def __init__(self, seed: int = 42):
        self.seed = seed

    def score(
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: sqlite3.Connection,
    ) -> pd.Series:
        rng = np.random.default_rng(self.seed)
        # Re-seed with date for per-date determinism
        seed_val = int(self.seed) + int(pd.Timestamp(date).timestamp()) % (2**32)
        rng = np.random.default_rng(seed_val)
        scores = rng.random(len(universe))
        return pd.Series(scores, index=pd.Index(universe, name="symbol"))


class MomentumSignal:
    """Stub momentum signal — Task 2 will fill the real implementation.

    For Task 1 this returns a placeholder score (close vs close 20d ago).
    Sufficient for harness testing.
    """

    requires_delivery = False

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def score(
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: sqlite3.Connection,
    ) -> pd.Series:
        if not universe:
            return pd.Series(dtype=float)
        date_ts = pd.Timestamp(date)
        date_s = f"{date_ts.year:04d}-{date_ts.month:02d}-{date_ts.day:02d}"
        cutoff_ts = date_ts - pd.Timedelta(days=self.lookback + 5)
        cutoff_s = f"{cutoff_ts.year:04d}-{cutoff_ts.month:02d}-{cutoff_ts.day:02d}"
        placeholders = ",".join("?" for _ in universe)
        rows = conn.execute(
            f"SELECT symbol, close FROM technical_data "
            f"WHERE symbol IN ({placeholders}) "
            f"AND date BETWEEN ? AND ?",
            (*universe, cutoff_s, date_s),
        ).fetchall()
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows, columns=["symbol", "close"])
        # Score = most-recent close per symbol
        df = df.groupby("symbol")["close"].last()
        return df.reindex(universe).fillna(0.0)


SIGNAL_REGISTRY: dict[str, Callable[..., SignalFunction]] = {
    "random": RandomSignal,
    "momentum": MomentumSignal,
}


# ──────────────────────────────────────────────────────────────────────────────
# Result containers.
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class BacktestConfig:
    signal: str = "random"
    exit_mode: Literal["fixed", "trailing", "rule"] = "fixed"
    fixed_hold_days: int = 60
    trailing_pct: float = 0.20
    rule_stop_pct: float = 0.05
    rule_sma_window: int = 20
    window: Literal["train", "holdout", "all"] = "all"
    requires_delivery: bool = False
    start_date: Optional[str] = None  # override default window start
    end_date: Optional[str] = None  # override default window end


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    summary: dict


# ──────────────────────────────────────────────────────────────────────────────
# Cost helpers — pure functions for testability.
# ──────────────────────────────────────────────────────────────────────────────


def calc_stt(sell_value_inr: float) -> float:
    """STT — sell side only, 0.025% of sell value."""
    return sell_value_inr * COST_MODEL["stt_pct_sell_side"] / 100.0


def calc_brokerage(trade_value_inr: float) -> float:
    """Brokerage = min(flat ₹20, 0.03% of trade value)."""
    return min(
        COST_MODEL["brokerage_flat_inr"],
        trade_value_inr * COST_MODEL["brokerage_pct"] / 100.0,
    )


def calc_impact_cost(
    position_value_inr: float, adv_value_inr: Optional[float]
) -> float:
    """Impact = k * sqrt(position_value / ADV_value). Flat 0.5% fallback if ADV missing."""
    if adv_value_inr is None or adv_value_inr <= 0:
        return position_value_inr * COST_MODEL["impact_fallback_pct"]
    ratio = position_value_inr / adv_value_inr
    return position_value_inr * COST_MODEL["impact_k"] * math.sqrt(ratio)


def total_round_trip_costs(
    entry_value_inr: float,
    exit_value_inr: float,
    adv_value_inr: Optional[float],
) -> dict:
    """Return dict with each cost component for an entry+exit round trip."""
    # Entry: brokerage + impact on buy. No STT on buy side.
    entry_brokerage = calc_brokerage(entry_value_inr)
    entry_impact = calc_impact_cost(entry_value_inr, adv_value_inr)
    # Exit: STT on sell + brokerage + impact on sell.
    exit_brokerage = calc_brokerage(exit_value_inr)
    exit_impact = calc_impact_cost(exit_value_inr, adv_value_inr)
    exit_stt = calc_stt(exit_value_inr)
    costs = {
        "stt": exit_stt,
        "brokerage": entry_brokerage + exit_brokerage,
        "impact": entry_impact + exit_impact,
    }
    costs["total"] = sum(costs.values())
    return costs


# ──────────────────────────────────────────────────────────────────────────────
# Universe filter.
# ──────────────────────────────────────────────────────────────────────────────


def _eligible_symbols_at_date(
    conn: sqlite3.Connection,
    as_of_date: pd.Timestamp,
    universe_seed: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return eligible EQUITY symbols at as_of_date.

    Criteria:
      1. instrument_type = 'EQUITY' in symbols_master
      2. Has technical_data within trailing 90 days (date BETWEEN t-90d AND t)
      3. Not in a discontinuity blackout window [event_date - 5d, + 5d]
    """
    cutoff_ts = pd.Timestamp(as_of_date) - pd.Timedelta(days=RECENT_TECH_WINDOW_DAYS)
    cutoff = f"{cutoff_ts.year:04d}-{cutoff_ts.month:02d}-{cutoff_ts.day:02d}"
    as_of_ts = pd.Timestamp(as_of_date)
    as_of = f"{as_of_ts.year:04d}-{as_of_ts.month:02d}-{as_of_ts.day:02d}"

    # 1 + 2: EQUITY symbols with recent technical data
    if universe_seed is not None:
        seed_list = [s for s in universe_seed]
        placeholders = ",".join("?" for _ in seed_list)
        rows = conn.execute(
            f"SELECT DISTINCT t.symbol FROM technical_data t "
            f"JOIN symbols_master m ON m.symbol = t.symbol "
            f"WHERE m.instrument_type = 'EQUITY' "
            f"AND t.symbol IN ({placeholders}) "
            f"AND t.date BETWEEN ? AND ?",
            (*seed_list, cutoff, as_of),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT t.symbol FROM technical_data t "
            "JOIN symbols_master m ON m.symbol = t.symbol "
            "WHERE m.instrument_type = 'EQUITY' "
            "AND t.date BETWEEN ? AND ?",
            (cutoff, as_of),
        ).fetchall()
    eligible = {r[0] for r in rows}

    # 3: discontinuity blackout
    events = _load_discontinuity_events()
    if not eligible or events.empty:
        return sorted(eligible)

    # Filter events to those within ±∞ of as_of_date (cheap: just check +/- 90 days max)
    # Vectorized comparison for speed
    mask = (
        events["date"] >= (as_of_date - pd.Timedelta(days=BLACKOUT_HALF_WINDOW + 30))
    ) & (events["date"] <= (as_of_date + pd.Timedelta(days=BLACKOUT_HALF_WINDOW + 30)))
    near = events[mask]
    blackout_syms: set[str] = set()
    # Vectorize: for each row, mark blackout if within ±5 days of as_of_date.
    if not near.empty:
        deltas = (near["date"] - as_of_date).abs().dt.days
        within = near[deltas <= BLACKOUT_HALF_WINDOW]
        blackout_syms = set(within["symbol"].astype(str).tolist())

    return sorted(eligible - blackout_syms)


# ──────────────────────────────────────────────────────────────────────────────
# Trading-day helpers.
# ──────────────────────────────────────────────────────────────────────────────


def _trading_days(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Return list of trading dates (ISO) within [start, end] from market_calendar
    if present, else from DISTINCT date in technical_data.
    """
    try:
        rows = conn.execute(
            "SELECT date FROM market_calendar "
            "WHERE is_trading_day = 1 AND date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
    except sqlite3.OperationalError:
        pass
    rows = conn.execute(
        "SELECT DISTINCT date FROM technical_data WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def _next_n_trading_days(trading_days: list[str], start_idx: int, n: int) -> list[str]:
    """Return N trading days starting from start_idx (inclusive)."""
    return trading_days[start_idx : start_idx + n + 1]


# ──────────────────────────────────────────────────────────────────────────────
# Price loader — single bulk fetch for the whole backtest window.
# ──────────────────────────────────────────────────────────────────────────────


def _load_prices_window(
    conn: sqlite3.Connection,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(
            columns=["symbol", "date", "close", "high", "low", "volume"]
        )
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, date, close, high, low, volume FROM technical_data "
        f"WHERE symbol IN ({placeholders}) "
        f"AND date BETWEEN ? AND ? ORDER BY symbol, date",
        (*symbols, start_date, end_date),
    ).fetchall()
    if not rows:
        return pd.DataFrame(
            columns=["symbol", "date", "close", "high", "low", "volume"]
        )
    df = pd.DataFrame(
        rows, columns=["symbol", "date", "close", "high", "low", "volume"]
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


# ──────────────────────────────────────────────────────────────────────────────
# ADV (average daily volume) loader — used for impact cost.
# ──────────────────────────────────────────────────────────────────────────────


def _load_adv_window(
    conn: sqlite3.Connection,
    symbols: list[str],
    as_of_date: str,
    window: int = 20,
) -> dict[str, float]:
    """Return {symbol: ADV_value_inr} where ADV_value = mean(volume * close) over
    the trailing `window` calendar days up to as_of_date. Symbols without
    enough data are omitted (caller falls back to flat impact).
    """
    if not symbols:
        return {}
    start_ts = pd.Timestamp(as_of_date) - pd.Timedelta(days=window + 10)
    start = f"{start_ts.year:04d}-{start_ts.month:02d}-{start_ts.day:02d}"
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, date, close, volume FROM technical_data "
        f"WHERE symbol IN ({placeholders}) "
        f"AND date BETWEEN ? AND ? ORDER BY symbol, date",
        (*symbols, start, as_of_date),
    ).fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["symbol", "date", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df["dollar_vol"] = df["close"].astype(float) * df["volume"].astype(float)
    # Per-symbol last `window` rows
    out: dict[str, float] = {}
    for sym, g in df.groupby("symbol"):
        tail = g.tail(window)
        if len(tail) < 5:  # require minimum history
            continue
        out[sym] = float(tail["dollar_vol"].mean())
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Exit evaluators — pure functions over per-position price slice.
# ──────────────────────────────────────────────────────────────────────────────


def _exit_fixed_holding(
    pos_prices: pd.DataFrame,
    entry_idx: int,
    n_hold_days: int,
) -> tuple[int, str]:
    """Close at exactly n_hold_days after entry. Return (exit_idx, reason)."""
    target_idx = entry_idx + n_hold_days
    if target_idx >= len(pos_prices):
        target_idx = len(pos_prices) - 1
        return target_idx, "fixed_window_end"
    return target_idx, f"fixed_{n_hold_days}d"


def _exit_trailing_stop(
    pos_prices: pd.DataFrame, entry_idx: int, trailing_pct: float
) -> tuple[int, str]:
    """20% trailing stop from max-high-since-entry.

    Scan forward from entry+1; track running max(high); exit when
    close < (1 - trailing_pct) * running_max. If no trigger, exit at last day.
    """
    if entry_idx >= len(pos_prices) - 1:
        return len(pos_prices) - 1, "trailing_eod"
    highs = pos_prices["high"].to_numpy()
    closes = pos_prices["close"].to_numpy()
    max_high = float(highs[entry_idx])
    threshold = (1.0 - trailing_pct) * max_high
    for i in range(entry_idx + 1, len(pos_prices)):
        h = float(highs[i])
        if h > max_high:
            max_high = h
            threshold = (1.0 - trailing_pct) * max_high
        if float(closes[i]) < threshold:
            return i, "trailing_stop"
    return len(pos_prices) - 1, "trailing_eod"


def _exit_rule_based(
    pos_prices: pd.DataFrame,
    entry_idx: int,
    stop_pct: float,
    sma_window: int,
) -> tuple[int, str]:
    """Rule-based exit (simplified proxy per spec):

    - close < close_at_entry * (1 - stop_pct)  →  exit at that day
    - close < sma(window)                       →  exit at that day

    Whichever triggers first wins. If neither, exit at last day.
    """
    n = len(pos_prices)
    if entry_idx >= n - 1:
        return n - 1, "rule_eod"
    entry_close = float(pos_prices["close"].iloc[entry_idx])
    threshold = entry_close * (1.0 - stop_pct)
    closes = pos_prices["close"].to_numpy()
    # Pre-compute rolling SMA on close over sma_window
    sma = (
        pos_prices["close"]
        .rolling(window=sma_window, min_periods=sma_window)
        .mean()
        .to_numpy()
    )
    for i in range(entry_idx + 1, n):
        c = float(closes[i])
        triggered = False
        reason = ""
        if c < threshold:
            triggered = True
            reason = f"rule_stop_{int(stop_pct * 100)}pct"
        elif not math.isnan(sma[i]) and c < float(sma[i]):
            triggered = True
            reason = f"rule_trend_break_sma{sma_window}"
        if triggered:
            return i, reason
    return n - 1, "rule_eod"


# ──────────────────────────────────────────────────────────────────────────────
# Main backtest loop.
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_window(
    requires_delivery: bool,
    override_start: Optional[str],
    override_end: Optional[str],
) -> tuple[str, str]:
    start = override_start or (
        TRAIN_START_DELIVERY if requires_delivery else TRAIN_START_PRICE_ONLY
    )
    end = override_end or HOLDOUT_END
    return start, end


def run_backtest(
    conn: sqlite3.Connection,
    config: BacktestConfig,
    seed_universe: Optional[Iterable[str]] = None,
) -> BacktestResult:
    """Execute a backtest with the given config. Returns trades + summary.

    `conn` is an open sqlite3.Connection with the required tables:
      - technical_data (symbol, date, close, high, low, volume)
      - symbols_master (symbol, instrument_type)
      - corporate_actions (symbol, date) — used by discontinuity script only
      - market_calendar (date, is_trading_day) — optional fallback
    """
    start_date, end_date = _resolve_window(
        config.requires_delivery, config.start_date, config.end_date
    )

    # Resolve window for train/holdout/all
    if config.window == "train":
        end_date = TRAIN_END
    elif config.window == "holdout":
        start_date = "2024-01-01"
        if not config.end_date:
            end_date = HOLDOUT_END

    # Build trading-day list
    trading_days = _trading_days(conn, start_date, end_date)
    if not trading_days:
        return BacktestResult(
            trades=pd.DataFrame(
                columns=[
                    "entry_date",
                    "exit_date",
                    "symbol",
                    "entry_price",
                    "exit_price",
                    "n_hold_days",
                    "pnl_gross",
                    "costs",
                    "pnl_net",
                    "exit_reason",
                ]
            ),
            summary=_empty_summary(),
        )

    # Resolve signal
    sig_factory = SIGNAL_REGISTRY[config.signal]
    signal_obj = sig_factory()

    # Pre-allocate trade list — `# noqa: PG-APPEND` is intentional: trade count
    # is unknown up-front and per-day we add at most one row.
    trades: list[dict] = []  # noqa: PG-APPEND

    # Build symbol pool once for ADV — limit to seed_universe if provided.
    pool = list(seed_universe) if seed_universe is not None else None

    # Cache ADV per (symbol, date). Recompute every 20 trading days.
    adv_cache: dict[str, float] = {}

    for day_idx, day_iso in enumerate(trading_days):
        day_ts = pd.Timestamp(day_iso)
        # Skip pre-delivery dates if signal requires delivery
        if config.requires_delivery and day_ts < pd.Timestamp(TRAIN_START_DELIVERY):
            continue

        # 1. Universe filter
        eligible = _eligible_symbols_at_date(conn, day_ts, universe_seed=pool)
        if not eligible:
            continue

        # 2. Signal — restrict to eligible universe
        scores = signal_obj.score(day_ts, eligible, conn)
        if scores is None or scores.empty:
            continue
        # Drop scores for non-eligible / NaN
        scores = scores.dropna()
        scores = scores[scores.index.isin(eligible)]
        if scores.empty:
            continue

        # 3. Pick top-1
        # Sort by score DESC, then symbol ASC for a deterministic, reproducible
        # tie-break. This protects against floating-point ties in the momentum
        # signal and ensures the random control's winner is stable regardless
        # of the iteration order of the input universe.
        sorted_scores = scores.sort_values(ascending=False, kind="mergesort")
        top_sym = sorted_scores.index[0]
        # If top_sym already has multiple positions we still open (concurrent).
        # Per spec: "a new position opens each day regardless of existing positions".

        # 4 + 5: ADV for impact cost (cached every 20 days)
        if day_idx % 20 == 0 or not adv_cache:
            adv_cache = _load_adv_window(
                conn, [top_sym] + eligible[:50], day_iso, window=20
            )
        adv_value = adv_cache.get(top_sym)

        # 6. Forward slice — load forward window for exit evaluation. This also
        # yields the entry price at row 0, so we avoid a separate per-day query.
        # Cap forward window at fixed_hold_days (or 200 for trailing/rule).
        max_forward = max(
            config.fixed_hold_days + 5,
            200 if config.exit_mode != "fixed" else config.fixed_hold_days + 5,
        )
        fwd_end_idx = min(day_idx + max_forward, len(trading_days) - 1)
        fwd_dates = trading_days[day_idx : fwd_end_idx + 1]
        if len(fwd_dates) < 2:
            continue
        pos_prices = _load_prices_window(conn, [top_sym], day_iso, fwd_dates[-1])
        if pos_prices.empty:
            continue
        pos_prices = pos_prices.sort_values("date").reset_index(drop=True)
        # entry_idx is 0
        entry_idx = 0
        if pd.isna(pos_prices["close"].iloc[0]):
            continue
        entry_price = float(pos_prices["close"].iloc[0])

        # 7. Compute exit
        if config.exit_mode == "fixed":
            exit_idx, reason = _exit_fixed_holding(
                pos_prices, entry_idx, config.fixed_hold_days
            )
        elif config.exit_mode == "trailing":
            exit_idx, reason = _exit_trailing_stop(
                pos_prices, entry_idx, config.trailing_pct
            )
        else:  # rule
            exit_idx, reason = _exit_rule_based(
                pos_prices, entry_idx, config.rule_stop_pct, config.rule_sma_window
            )
        exit_row = pos_prices.iloc[exit_idx]
        exit_price = float(exit_row["close"])
        _ed = exit_row["date"]
        # noqa: PG-STRFTIME  — single Timestamp per iteration; .dt.strftime() would
        # copy the whole Series; f-string is cheaper.
        exit_date = f"{_ed.year:04d}-{_ed.month:02d}-{_ed.day:02d}"  # noqa: PG-STRFTIME
        n_hold = exit_idx - entry_idx

        # 8. P&L and costs
        entry_value = POSITION_VALUE_INR
        shares = entry_value / entry_price
        exit_value = shares * exit_price
        pnl_gross = exit_value - entry_value
        costs = total_round_trip_costs(entry_value, exit_value, adv_value)
        pnl_net = pnl_gross - costs["total"]

        trades.append(  # noqa: PG-APPEND
            {
                "entry_date": day_iso,
                "exit_date": exit_date,
                "symbol": top_sym,
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
    summary = _compute_summary(trades_df, config)
    return BacktestResult(trades=trades_df, summary=summary)


def _empty_summary() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "avg_return": 0.0,
        "max_drawdown": 0.0,
        "peak_concurrent_capital": 0.0,
        "total_pnl_net": 0.0,
    }


def _compute_summary(trades: pd.DataFrame, config: BacktestConfig) -> dict:
    if trades.empty:
        return _empty_summary()
    pnls = trades["pnl_net"].astype(float)
    total_trades = len(trades)
    win_rate = float((pnls > 0).sum()) / total_trades
    avg_return = float(trades["pnl_net"].mean())

    # Approx concurrent capital: assume position_value + cost per active day.
    # For daily concurrent positions of size POSITION_VALUE_INR, peak concurrent
    # capital grows linearly with number of overlapping positions (worst case).
    # We approximate by counting trades whose entry_date <= today AND exit_date >= today.
    # Simpler proxy: peak_concurrent_capital = max(active_count) * POSITION_VALUE_INR.
    active_count_max = _max_concurrent_positions(trades)
    peak_concurrent_capital = active_count_max * POSITION_VALUE_INR

    # Max drawdown on cumulative PnL (over time, ordered by entry_date)
    sorted_trades = trades.sort_values("entry_date")
    cum = sorted_trades["pnl_net"].cumsum()
    max_dd = float(_max_drawdown_from_cumsum(cum))

    return {
        "total_trades": int(total_trades),
        "win_rate": float(win_rate),
        "avg_return": float(avg_return),
        "max_drawdown": float(max_dd),
        "peak_concurrent_capital": float(peak_concurrent_capital),
        "total_pnl_net": float(pnls.sum()),
    }


def _max_concurrent_positions(trades: pd.DataFrame) -> int:
    """Compute the maximum number of concurrently-open positions over time.

    Vectorized: build two event arrays (entry=+1, exit=-1), sort by date with
    exits-first tiebreak, then sweep.
    """
    if trades.empty:
        return 0
    # Convert to numpy datetime64 once.
    entry_dates = pd.to_datetime(trades["entry_date"], errors="coerce").to_numpy()
    exit_dates = pd.to_datetime(trades["exit_date"], errors="coerce").to_numpy()
    # Drop rows where either date failed to parse.
    valid = ~(np.isnat(entry_dates) | np.isnat(exit_dates))
    if not valid.any():
        return 0
    entry_dates = entry_dates[valid]
    exit_dates = exit_dates[valid]

    # Stack exits first (-1) then entries (+1) at same date so we don't double count.
    # We sort by (date, delta) where delta is +1 for entry, -1 for exit; ascending
    # delta means exits process first.
    all_dates = np.concatenate([exit_dates, entry_dates])
    deltas = np.concatenate(
        [
            np.full(len(exit_dates), -1, dtype=np.int8),
            np.full(len(entry_dates), +1, dtype=np.int8),
        ]
    )
    # Lexsort: primary key = dates, secondary = deltas (ascending → -1 before +1)
    order = np.lexsort((deltas, all_dates))
    cur = 0
    peak = 0
    for d in deltas[order]:
        cur += int(d)
        if cur > peak:
            peak = cur
    return peak


def _max_drawdown_from_cumsum(cum: pd.Series) -> float:
    """Max drawdown from a cumulative P&L series. Returns positive number (the
    drawdown magnitude, i.e. peak - trough)."""
    if cum.empty:
        return 0.0
    running_max = cum.cummax()
    drawdown = running_max - cum
    if drawdown.empty:
        return 0.0
    return float(drawdown.max())


# ──────────────────────────────────────────────────────────────────────────────
# Convenience entry-point — open MYRA DB connections and run.
# ──────────────────────────────────────────────────────────────────────────────


def run_backtest_myra(config: BacktestConfig) -> BacktestResult:
    """Open MYRA's actual sidecar DBs and run a backtest. Convenience for
    notebooks / CLI usage. Library callers should pass `conn` directly.
    """
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    meta_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
    inst_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["institutional"])
    cal_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["calendar"])

    # Attach all 4 DBs into one connection via ATTACH so JOINs work cleanly.
    # Fallback: open tech as primary and attach others read-only.
    primary = tech_db
    conn = sqlite3.connect(primary)
    conn.execute(f"ATTACH DATABASE '{meta_db}' AS meta")
    conn.execute(f"ATTACH DATABASE '{inst_db}' AS inst")
    if os.path.exists(cal_db):
        conn.execute(f"ATTACH DATABASE '{cal_db}' AS cal")
    try:
        return run_backtest(conn, config)
    finally:
        conn.close()
