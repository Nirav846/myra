"""
MYRA Backtest Engine — Phase 1, Task 1.

A standalone, pluggable backtest harness for evaluating trading signals
against historical MYRA data. Designed to:

  * Read universe + prices from existing MYRA SQLite sidecars (technical,
    meta, institutional).
  * Accept an arbitrary `SignalFunction` (price-only or delivery-aware).
  * Support four independent exit modes:
      1. Fixed holding period (N trading days).
      2. 20% trailing stop from max-high-since-entry.
      3. Rule-based (5% stop OR trend break below 20d SMA).
      4. Profit-target (close at first close >= entry * (1+pct), or
         force-close after `profit_target_cap_days` trading days).
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


# ──────────────────────────────────────────────────────────────────────────────
# Kaushik "Bottom Out Hunting" Method 1 (Phase 2).
# ──────────────────────────────────────────────────────────────────────────────
KAUSHIK_LOOKBACK = 252  # rolling 52-week window (trading days)
KAUSHIK_RECOVERY_MULT = 1.20  # recover to year_low * 1.20 to fire
KAUSHIK_DELIVERY_AVG_WINDOW = 20  # trailing avg delivery % for elevation
# Entry-FILTER variant (Phase-2 Follow-up 2): elevation must be strictly above
# the symbol's own trailing baseline to trade at all. Window = 10 (train-best
# from the Follow-up-1 sweep; W=10 also minimises early-window data dropout),
# threshold = 0 (strictly elevated vs own trailing average).
KAUSHIK_DELIVERY_FILTER_WINDOW = 10
KAUSHIK_DELIVERY_ELEV_THRESHOLD = 0.0

# Module-level cache of precomputed Kaushik events:
#   {date_iso: {symbol: (overshoot, delivery_elevation_or_None)}}
# Computed once per process (all 12 Phase-2 runs share it). The event map is
# independent of window / target_pct / variant, so one precompute serves all.
_KAUSHIK_CACHE: Optional[dict[str, dict[str, tuple[float, Optional[float]]]]] = None


def _precompute_kaushik_events(
    conn: sqlite3.Connection,
    delivery_window: int = KAUSHIK_DELIVERY_AVG_WINDOW,
    recovery_mult: float = KAUSHIK_RECOVERY_MULT,
) -> dict[str, dict[str, tuple[float, Optional[float]]]]:
    """Stream ALL technical_data rows grouped by symbol and detect signals.

    Per symbol:
      - year_low[i] = min(low[i-251..i])  (rolling 252-trading-day low,
        recomputed daily, requires a full 252-day window).
      - cross[i] = close[i-1] < recovery_mult*year_low[i]
                  AND close[i] >= recovery_mult*year_low[i]
        (discrete upward crossing, NOT "currently above").
      - Cooldown: after a signal on day s (floor = year_low[s]), the symbol
        cannot signal again until year_low drops STRICTLY below `floor`
        (a fresh 52-week low), restarting the cycle. Tracks the running min
        of year_low since the last signal, which handles rolling-window lows
        that age out and rise again.
      - delivery_elevation[i] = delivery_pct[i] - mean(delivery_pct[i-W..i-1])
        (self-relative, trailing-W average excludes the signal day; W is
        `delivery_window`, 20 by default — sensitivity-swept only in the
        Phase-2 follow-up, signal/cooldown logic is untouched by W).
    """
    global _KAUSHIK_CACHE
    cache_key = (recovery_mult, delivery_window)
    if _KAUSHIK_CACHE is not None and _KAUSHIK_CACHE.get("key") == cache_key:
        return _KAUSHIK_CACHE["events"]

    from numpy.lib.stride_tricks import sliding_window_view

    events: dict[str, dict[str, tuple[float, Optional[float]]]] = {}
    cur_sym: Optional[str] = None
    dates: list[str] = []
    closes: list[float] = []
    lows: list[float] = []
    dpcts: list[float] = []

    def _flush(sym: str) -> None:
        if not dates:
            return
        n = len(dates)
        close_arr = np.asarray(closes, dtype=float)
        low_arr = np.asarray(lows, dtype=float)
        dp_arr = np.asarray(dpcts, dtype=float)
        if n < KAUSHIK_LOOKBACK + 1:
            return  # not even one full 52-week window (and a prior close)
        # Rolling 252-day low of LOW, valid from index KAUSHIK_LOOKBACK-1 on.
        year_low = np.full(n, np.nan)
        year_low[KAUSHIK_LOOKBACK - 1 :] = sliding_window_view(
            low_arr, KAUSHIK_LOOKBACK
        ).min(axis=-1)
        # Trailing-W delivery mean strictly before each day (shifted rolling).
        del_base = (
            pd.Series(dp_arr)
            .rolling(delivery_window, min_periods=delivery_window)
            .mean()
            .shift(1)
            .to_numpy()
        )
        # Sequential cooldown sweep.
        threshold = recovery_mult
        in_cd = False
        floor = np.inf
        run_min = np.inf
        for i in range(KAUSHIK_LOOKBACK, n):
            y = year_low[i]
            if np.isnan(y):
                continue
            if in_cd:
                if y < run_min:
                    run_min = y
                if run_min < floor:
                    in_cd = False  # fresh 52-week low → cycle restarts
                    # Fall through: a same-day reversal could also cross.
                else:
                    continue
            if close_arr[i - 1] < threshold * y and close_arr[i] >= threshold * y:
                overshoot = close_arr[i] / (threshold * y) - 1.0
                elev: Optional[float] = None
                if not (np.isnan(dp_arr[i]) or np.isnan(del_base[i])):
                    elev = float(dp_arr[i] - del_base[i])
                events.setdefault(dates[i], {})[sym] = (overshoot, elev)
                in_cd = True
                floor = y
                run_min = np.inf

    cur = conn.execute(
        "SELECT symbol, date, close, low, delivery_pct "
        "FROM technical_data ORDER BY symbol, date"
    )
    for sym, d, c, lo, dp in cur:
        if sym != cur_sym:
            if cur_sym is not None:
                _flush(cur_sym)
            cur_sym = sym
            dates = []
            closes = []
            lows = []
            dpcts = []
        dates.append(d)  # noqa: PG-APPEND
        closes.append(c)  # noqa: PG-APPEND
        lows.append(lo)  # noqa: PG-APPEND
        dpcts.append(dp)  # noqa: PG-APPEND
    if cur_sym is not None:
        _flush(cur_sym)

    _KAUSHIK_CACHE = {"key": cache_key, "events": events}
    return events


class KaushikBOHMethod1:
    """Kaushik 'Bottom Out Hunting' Method 1 — 20% recovery off 52-week low.

    A symbol signals on day `t` iff:
      - close[t-1] < year_low[t] * 1.20  (below the 20%-recovery line)
      - close[t]  >= year_low[t] * 1.20  (crosses UP through it)
    where year_low[t] is the rolling 252-trading-day low of the LOW price,
    recomputed daily. This is a discrete crossing event, not a "currently
    above" condition that would re-fire daily.

    Cooldown: once a symbol signals on day s (floor = year_low[s]), it cannot
    signal again until year_low drops strictly below that floor — a fresh
    52-week low — which restarts the cycle. Mirrors Kaushik's one-signal-per-
    genuine-bottom-and-recovery behavior rather than re-entering the same leg.

    score() returns a Series indexed by symbol containing only symbols that
    signal on `date`:
      - base variant: score = -overshoot (smallest overshoot = freshest
        crossing wins the top-1 tie-break, approximating a GTT limit fill
        near the trigger).
      - delivery variant: score = delivery_elevation (signal-day delivery%
        minus trailing-20-day avg), so the most delivery-confirmed candidate
        wins when there is a choice. Delivery-restricted to 2019-10-01+ via
        requires_delivery in the harness.
    """

    requires_delivery = False
    recovery_mult: float = KAUSHIK_RECOVERY_MULT

    def __init__(self, delivery_variant: bool = False):
        self.delivery_variant = delivery_variant

    def score(
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: sqlite3.Connection,
    ) -> pd.Series:
        events = _precompute_kaushik_events(conn, recovery_mult=self.recovery_mult)
        date_ts = pd.Timestamp(date)
        date_s = f"{date_ts.year:04d}-{date_ts.month:02d}-{date_ts.day:02d}"
        day_events = events.get(date_s)
        if not day_events:
            return pd.Series(dtype=float)
        univ = set(universe)
        out: dict[str, float] = {}
        for sym, (overshoot, elev) in day_events.items():
            if sym not in univ:
                continue
            if self.delivery_variant:
                if elev is None:
                    continue  # no delivery elevation to rank by
                out[sym] = float(elev)
            else:
                out[sym] = -float(overshoot)
        if not out:
            return pd.Series(dtype=float)
        return pd.Series(out, dtype=float)


class KaushikBOHMethod1Delivery(KaushikBOHMethod1):
    """Delivery-augmented variant: identical logic, but the same-day tie-break
    ranks by delivery elevation instead of overshoot."""

    requires_delivery = True

    def __init__(self):
        super().__init__(delivery_variant=True)


class KaushikBOHMethod1DeliveryFilter(KaushikBOHMethod1):
    """Delivery entry-FILTER variant (Phase-2 Follow-up 2).

    Identical entry/cooldown/exit logic to the base method, plus one hard
    condition at the crossing day: the candidate's delivery percentage must be
    strictly elevated relative to its own trailing baseline (elevation > 0,
    self-relative trailing `delivery_window` average excluding the signal day).
    If a crossing occurs but delivery is not elevated, NO trade is taken for
    that symbol that day — even if it is the only candidate. Remaining
    candidates use the base tie-break (smallest overshoot), NOT delivery
    re-ranking — this variant screens the entry, it does not rank the entries.
    """

    requires_delivery = True

    def __init__(
        self,
        elev_threshold: float = KAUSHIK_DELIVERY_ELEV_THRESHOLD,
        delivery_window: int = KAUSHIK_DELIVERY_FILTER_WINDOW,
    ):
        super().__init__(delivery_variant=False)  # base tie-break (overshoot)
        self.elev_threshold = elev_threshold
        self.delivery_window = delivery_window

    def score(
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: sqlite3.Connection,
    ) -> pd.Series:
        events = _precompute_kaushik_events(conn, delivery_window=self.delivery_window)
        date_ts = pd.Timestamp(date)
        date_s = f"{date_ts.year:04d}-{date_ts.month:02d}-{date_ts.day:02d}"
        day_events = events.get(date_s)
        if not day_events:
            return pd.Series(dtype=float)
        univ = set(universe)
        out: dict[str, float] = {}
        for sym, (overshoot, elev) in day_events.items():
            if sym not in univ:
                continue
            if elev is None or elev <= self.elev_threshold:
                continue  # delivery NOT elevated -> no trade, even if sole candidate
            out[sym] = -float(overshoot)  # base tie-break among passing candidates
        if not out:
            return pd.Series(dtype=float)
        return pd.Series(out, dtype=float)


class KaushikBOHMethod2(KaushikBOHMethod1):
    """Kaushik 'Bottom Out Hunting' Method 2 — 40% recovery off 52-week low.

    Identical entry/cooldown/exit logic to Method 1, but the recovery
    threshold is 1.40 instead of 1.20. The higher bar means fewer signals,
    but each signal has more confirmation that the stock has genuinely
    bottomed and is in a sustainable uptrend.

    Cooldown, averaging, cap mechanics — all identical to Method 1.
    Only the entry threshold differs.
    """

    recovery_mult: float = 1.40

    def __init__(self):
        super().__init__(delivery_variant=False)


SIGNAL_REGISTRY: dict[str, Callable[..., SignalFunction]] = {
    "random": RandomSignal,
    "momentum": MomentumSignal,
    "kaushik_boh_m1": KaushikBOHMethod1,
    "kaushik_boh_m1_delivery": KaushikBOHMethod1Delivery,
    "kaushik_boh_m1_delivery_filter": KaushikBOHMethod1DeliveryFilter,
    "kaushik_boh_m2": KaushikBOHMethod2,
}


# ──────────────────────────────────────────────────────────────────────────────
# Result containers.
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class BacktestConfig:
    signal: str = "random"
    exit_mode: Literal["fixed", "trailing", "rule", "profit_target"] = "fixed"
    fixed_hold_days: int = 60
    trailing_pct: float = 0.20
    rule_stop_pct: float = 0.05
    rule_sma_window: int = 20
    profit_target_pct: float = 0.075  # close at close >= entry * (1 + pct)
    profit_target_cap_days: int = (
        252  # force-close after N trading days if target not hit
    )
    window: Literal["train", "holdout", "all"] = "all"
    requires_delivery: bool = False
    start_date: Optional[str] = None  # override default window start
    end_date: Optional[str] = None  # override default window end
    average_in: bool = (
        False  # Kaushik real averaging: fresh signals on held symbols add tranches
    )
    max_tranches: int = 3  # per-symbol tranche cap (blended cost-basis position)
    recovery_mult: float = (
        KAUSHIK_RECOVERY_MULT  # entry threshold multiplier (1.20=M1, 1.40=M2)
    )


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


def _preload_universe_by_date(
    conn: sqlite3.Connection,
    trading_days: list[str],
    universe_seed: Optional[Iterable[str]] = None,
    pit_universe: Optional[dict[str, list[str]]] = None,
) -> dict[str, list[str]]:
    """Pre-compute the eligible universe for every trading day in one pass.

    Equivalent to calling ``_eligible_symbols_at_date`` per day, but does it
    with bulk queries + in-memory computation. Returns
    ``{date_iso: sorted(symbols)}`` for every day in ``trading_days``.

    Logic (mirrors ``_eligible_symbols_at_date``):
      1. EQUITY symbols only.
      2. Has technical_data within trailing 90 days (calendar).
      3. Not in a discontinuity blackout window [event_date - 5d, +5d].

    Implementation: for each (sym, date_row) in technical_data, sym becomes
    eligible for the trading-day range [date_row, date_row + 90d]. We
    build a per-sym list of (start, end) eligibility windows, then sweep
    once through trading days maintaining a count-based active set. This
    replaces ~2,200 per-day SQL fetches (~85% of ``run_backtest`` runtime)
    with one bulk query and one in-memory sweep.
    """
    if not trading_days:
        return {}

    # ---- bulk load 1: equity master list ----
    if universe_seed is not None:
        seed_list = [s for s in universe_seed]
        placeholders = ",".join("?" for _ in seed_list)
        eq_rows = conn.execute(
            f"SELECT symbol FROM symbols_master "
            f"WHERE instrument_type = 'EQUITY' AND symbol IN ({placeholders})",
            seed_list,
        ).fetchall()
    else:
        eq_rows = conn.execute(
            "SELECT symbol FROM symbols_master WHERE instrument_type = 'EQUITY'"
        ).fetchall()
    equity_syms = {r[0] for r in eq_rows}
    if not equity_syms:
        return {d: [] for d in trading_days}

    # ---- bulk load 2: all technical_data rows in [first - 90d, last] ----
    first_dt = pd.Timestamp(trading_days[0])
    last_dt = pd.Timestamp(trading_days[-1])
    start_window = f"{(first_dt - pd.Timedelta(days=RECENT_TECH_WINDOW_DAYS + 30)).year:04d}-{(first_dt - pd.Timedelta(days=RECENT_TECH_WINDOW_DAYS + 30)).month:02d}-{(first_dt - pd.Timedelta(days=RECENT_TECH_WINDOW_DAYS + 30)).day:02d}"  # noqa: PG-STRFTIME
    end_window = (
        f"{last_dt.year:04d}-{last_dt.month:02d}-{last_dt.day:02d}"  # noqa: PG-STRFTIME
    )
    if universe_seed is not None:
        placeholders = ",".join("?" for _ in seed_list)
        tech_rows = conn.execute(
            f"SELECT symbol, CAST(julianday(date) - 2440587.5 AS INTEGER) AS d_int, date AS d_iso "
            f"FROM technical_data "
            f"WHERE symbol IN ({placeholders}) AND date BETWEEN ? AND ?",
            (*seed_list, start_window, end_window),
        ).fetchall()
    else:
        tech_rows = conn.execute(
            "SELECT symbol, CAST(julianday(date) - 2440587.5 AS INTEGER) AS d_int, date AS d_iso "
            "FROM technical_data WHERE date BETWEEN ? AND ?",
            (start_window, end_window),
        ).fetchall()

    # ---- bulk load 3: discontinuity events ----
    # Mirrors the direct function's blackout semantics: BLACKOUT_HALF_WINDOW
    # is compared against CALENDAR day deltas (the original code uses
    # (event["date"] - as_of_date).abs().dt.days <= BLACKOUT_HALF_WINDOW).
    # Vectorized: for each event, compute blackout start/end as dates and
    # find the corresponding trading-day range via searchsorted.
    import bisect

    events = _load_discontinuity_events()
    td_idx = {d: i for i, d in enumerate(trading_days)}
    N_td = len(trading_days)
    blackout_by_day: dict[int, set[str]] = {}
    if not events.empty:
        for _, ev in events.iterrows():  # noqa: PG-ITERROWS
            sym = ev["symbol"]
            ev_dt = ev["date"]
            # Calendar-day window: [ev_dt - HALF_WINDOW days, ev_dt + HALF_WINDOW days]
            start_dt = ev_dt - pd.Timedelta(days=BLACKOUT_HALF_WINDOW)
            end_dt = ev_dt + pd.Timedelta(days=BLACKOUT_HALF_WINDOW)
            start_str = f"{start_dt.year:04d}-{start_dt.month:02d}-{start_dt.day:02d}"  # noqa: PG-STRFTIME
            end_str = f"{end_dt.year:04d}-{end_dt.month:02d}-{end_dt.day:02d}"  # noqa: PG-STRFTIME
            # Trading days in [start_str, end_str] (inclusive)
            si = bisect.bisect_left(trading_days, start_str)
            ei = bisect.bisect_right(trading_days, end_str)
            for i in range(si, ei):
                blackout_by_day.setdefault(i, set()).add(sym)  # noqa: PG-APPEND

    # ---- build per-sym sorted date list, also as numpy int days ----
    # sym_dates_str: {sym: sorted_list_of_iso_str}  for bisect lookups
    # sym_dates_int: {sym: np.ndarray of int days}  for fast gap computation
    sym_dates_str: dict[str, list[str]] = {}
    sym_dates_int: dict[str, np.ndarray] = {}
    for sym, d_int, d_iso in tech_rows:
        if sym not in equity_syms:
            continue
        if sym not in sym_dates_str:
            sym_dates_str[sym] = []
            sym_dates_int[sym] = []
        sym_dates_str[sym].append(d_iso)  # noqa: PG-APPEND
        sym_dates_int[sym].append(d_int)  # noqa: PG-APPEND
    for s in sym_dates_str:
        # Dedupe while preserving order
        seen = set()
        dedup_str = []
        dedup_int = []
        for s_iso, s_int in zip(sym_dates_str[s], sym_dates_int[s]):
            if s_int not in seen:
                seen.add(s_int)
                dedup_str.append(s_iso)  # noqa: PG-APPEND
                dedup_int.append(s_int)  # noqa: PG-APPEND
        sym_dates_str[s] = dedup_str
        sym_dates_int[s] = np.array(dedup_int, dtype=np.int64)

    # ---- bulk load 3: discontinuity events ----
    # Mirrors the direct function's blackout semantics: BLACKOUT_HALF_WINDOW
    # is compared against CALENDAR day deltas (the original code uses
    # (event["date"] - as_of_date).abs().dt.days <= BLACKOUT_HALF_WINDOW).
    # Vectorized: for each event, compute blackout start/end as dates and
    # find the corresponding trading-day range via searchsorted.
    import bisect

    events = _load_discontinuity_events()
    td_idx = {d: i for i, d in enumerate(trading_days)}
    N_td = len(trading_days)
    blackout_by_day: dict[int, set[str]] = {}
    if not events.empty:
        for _, ev in events.iterrows():  # noqa: PG-ITERROWS
            sym = ev["symbol"]
            ev_dt = ev["date"]
            # Calendar-day window: [ev_dt - HALF_WINDOW days, ev_dt + HALF_WINDOW days]
            start_dt = ev_dt - pd.Timedelta(days=BLACKOUT_HALF_WINDOW)
            end_dt = ev_dt + pd.Timedelta(days=BLACKOUT_HALF_WINDOW)
            start_str = f"{start_dt.year:04d}-{start_dt.month:02d}-{start_dt.day:02d}"  # noqa: PG-STRFTIME
            end_str = f"{end_dt.year:04d}-{end_dt.month:02d}-{end_dt.day:02d}"  # noqa: PG-STRFTIME
            # Trading days in [start_str, end_str] (inclusive)
            si = bisect.bisect_left(trading_days, start_str)
            ei = bisect.bisect_right(trading_days, end_str)
            for i in range(si, ei):
                blackout_by_day.setdefault(i, set()).add(sym)  # noqa: PG-APPEND

    # ---- compute per-sym eligibility intervals (proper union, not min/max) ----
    # For each sym, the eligibility for day d is "exists d_row in [d-90d, d]".
    # The union over all d_rows of [d_row, d_row + 90d] is the sym's
    # eligibility range. For syms with gaps > 90d (delisted-and-relisted
    # or long trading halts), this is MULTIPLE disjoint intervals.
    #
    # We split each sym's d_rows into runs of consecutive dates within
    # 90d of each other, and emit one (start, end) event per run.
    # Vectorized: convert d_rows to numpy datetime64, compute gaps in
    # numpy, find run boundaries, then emit one event per run.
    import bisect

    # Precompute trading_days as a numpy array for vectorized gap checks
    days_arr = pd.to_datetime(pd.Series(trading_days)).values  # datetime64
    N_td = len(days_arr)
    days_int = days_arr.astype("datetime64[D]").astype(np.int64)  # int64 days
    WINDOW_D = RECENT_TECH_WINDOW_DAYS  # 90
    td_idx = {d: i for i, d in enumerate(trading_days)}  # for bisect lookups

    events_per_sym: list[tuple[int, int, str]] = []
    for sym, d_arr in sym_dates_int.items():
        if len(d_arr) < 1:
            continue
        # Gaps between consecutive dates (in days)
        gaps = np.diff(d_arr) if len(d_arr) > 1 else np.array([], dtype=np.int64)
        # Run breaks at indices where gap > 90d
        break_idx = np.where(gaps > WINDOW_D)[0]
        # Run boundaries
        run_starts = np.concatenate([[0], break_idx + 1])
        run_ends = np.concatenate([break_idx, [len(d_arr) - 1]])
        d_rows_str = sym_dates_str[sym]  # iso strings, parallel to d_arr
        for rs, re in zip(run_starts, run_ends):
            first_iso = d_rows_str[rs]
            last_iso = d_rows_str[re]
            si = bisect.bisect_left(trading_days, first_iso)
            if si >= N_td:
                continue
            # end = first trading day strictly after last_iso + 90d
            target_dt = pd.Timestamp(last_iso) + pd.Timedelta(days=WINDOW_D)
            target_str = f"{target_dt.year:04d}-{target_dt.month:02d}-{target_dt.day:02d}"  # noqa: PG-STRFTIME
            ei = bisect.bisect_right(trading_days, target_str)
            ei = min(ei, N_td)
            if si < ei:
                events_per_sym.append((si, ei, sym))  # noqa: PG-APPEND

    # ---- sweep: for each day, which syms are in any of their intervals ----
    # A sym with multiple disjoint windows has multiple events. We track
    # active count per sym so a sym stays active as long as ANY of its
    # events covers the current day.
    events_per_sym.sort()
    end_events = sorted([(e, sym) for _, e, sym in events_per_sym])
    active_count: dict[str, int] = {}
    out: dict[str, list[str]] = {d: [] for d in trading_days}
    e_ptr = 0
    e_remove_ptr = 0
    N_e = len(events_per_sym)
    for d_idx in range(N_td):
        while e_ptr < N_e and events_per_sym[e_ptr][0] == d_idx:
            _, _, sym = events_per_sym[e_ptr]
            active_count[sym] = active_count.get(sym, 0) + 1
            e_ptr += 1
        while e_remove_ptr < N_e and end_events[e_remove_ptr][0] == d_idx:
            _, sym = end_events[e_remove_ptr]
            active_count[sym] = active_count.get(sym, 0) - 1
            if active_count[sym] == 0:
                del active_count[sym]
            e_remove_ptr += 1
        if active_count:
            out[trading_days[d_idx]] = sorted(active_count.keys())

    # Apply blackouts
    for d_i, black_syms in blackout_by_day.items():
        if d_i < N_td:
            day_iso = trading_days[d_i]
            out[day_iso] = [s for s in out[day_iso] if s not in black_syms]

    # Apply PIT universe: intersect each day's eligible set with the
    # point-in-time top-N list for that date.  This ensures historical
    # backtests use the correct per-date composition rather than a
    # static snapshot.
    if pit_universe is not None:
        for day_iso in trading_days:
            pit_syms = set(pit_universe.get(day_iso, []))
            if pit_syms:
                out[day_iso] = [s for s in out[day_iso] if s in pit_syms]
            else:
                # No PIT data for this date — fall back to seed/full
                pass

    return out


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


def _exit_profit_target(
    pos_prices: pd.DataFrame,
    entry_idx: int,
    target_pct: float,
    cap_days: int = 252,
) -> tuple[int, str]:
    """Close at the first close >= entry_close * (1 + target_pct).

    If the target is not hit within `cap_days` trading days (~1 year),
    force-close at whatever price prevails on that day. If the price
    series ends before `cap_days` elapse (window end / data end), close
    at the last available row and report `pt_eod`.

    Returns (exit_idx, reason) where reason is:
      - f"pt_target_{bp}bp"  target hit
      - "pt_252d_cap"        252-trading-day cap force-close
      - "pt_eod"             series ended before target or cap
    """
    n = len(pos_prices)
    if entry_idx >= n - 1:
        return n - 1, "pt_eod"
    entry_close = float(pos_prices["close"].iloc[entry_idx])
    trigger = entry_close * (1.0 + target_pct)
    closes = pos_prices["close"].to_numpy()
    cap_idx = entry_idx + cap_days
    # Scan the earliest of [cap boundary, last available row] for the target.
    scan_end = min(cap_idx, n - 1)
    for i in range(entry_idx + 1, scan_end + 1):
        if float(closes[i]) >= trigger:
            return i, f"pt_target_{int(round(target_pct * 1000))}bp"
    if cap_idx < n:
        # We observed the full cap window without a target hit.
        return cap_idx, "pt_252d_cap"
    return n - 1, "pt_eod"


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
    pit_universe: Optional[dict[str, list[str]]] = None,
) -> BacktestResult:
    """Execute a backtest with the given config. Returns trades + summary.

    `conn` is an open sqlite3.Connection with the required tables:
      - technical_data (symbol, date, close, high, low, volume)
      - symbols_master (symbol, instrument_type)
      - corporate_actions (symbol, date) — used by discontinuity script only
      - market_calendar (date, is_trading_day) — optional fallback

    `pit_universe`: optional per-date PIT universe dict
      ``{date_iso: [symbol, ...]}``.  When provided, each day's eligible
      set is intersected with this list, giving true point-in-time
      universe membership for historical backtests.
    """
    if config.average_in:
        return _run_backtest_averaging(conn, config, seed_universe, pit_universe)

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

    # Pre-load the per-day eligible universe in one bulk query, then look up
    # in memory during the day loop. This avoids 2,200+ repeated SQL fetches
    # (~85% of total runtime was spent in the per-day universe filter).
    universe_by_date = _preload_universe_by_date(
        conn, trading_days, universe_seed=pool, pit_universe=pit_universe
    )

    # Cache ADV per (symbol, date). Recompute every 20 trading days.
    adv_cache: dict[str, float] = {}

    for day_idx, day_iso in enumerate(trading_days):
        day_ts = pd.Timestamp(day_iso)
        # Skip pre-delivery dates if signal requires delivery
        if config.requires_delivery and day_ts < pd.Timestamp(TRAIN_START_DELIVERY):
            continue

        # 1. Universe filter — from pre-loaded in-memory map
        eligible = universe_by_date.get(day_iso)
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
        # Cap forward window at fixed_hold_days (or 200 for trailing/rule, or
        # profit_target_cap_days + 5 for the profit-target exit).
        if config.exit_mode == "profit_target":
            max_forward = config.profit_target_cap_days + 5
        elif config.exit_mode == "fixed":
            max_forward = config.fixed_hold_days + 5
        else:
            max_forward = 200
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
        elif config.exit_mode == "profit_target":
            exit_idx, reason = _exit_profit_target(
                pos_prices,
                entry_idx,
                config.profit_target_pct,
                config.profit_target_cap_days,
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


def _run_backtest_averaging(
    conn: sqlite3.Connection,
    config: BacktestConfig,
    seed_universe: Optional[Iterable[str]] = None,
    pit_universe: Optional[dict[str, list[str]]] = None,
) -> BacktestResult:
    """Profit-target backtest with Kaushik's real averaging mechanism.

    Positions are tracked per symbol with up to `config.max_tranches`
    tranches of POSITION_VALUE_INR each. While a position is open, a FRESH
    signal on the same symbol (new rolling-252-day low strictly below the
    low that triggered the previous entry, followed by a 20% recovery
    crossing — guaranteed by the shared Kaushik cooldown in
    `_precompute_kaushik_events`) averages in another tranche.

    Exit rules (per the Phase-3 design decisions):
      - the profit target applies to the volume-weighted blended entry price
        across all tranches, not the first entry price;
      - the 252-trading-day cap re-anchors to the MOST RECENT tranche's
        entry date; a position survives cap(i=0) if later tranches refreshed
        the clock;
      - once max_tranches is reached, further signals are ignored and the
        position resolves only via blended-target or re-anchored cap;
      - average-ins never block that day's top-1 new-signal entry — a held
        symbol consuming 2-3 tranches does not compete with the steady
        stream of new top-1 picks elsewhere.

    Capital accounting: each tranche is a separate +POSITION_VALUE_INR
    allocation, so a symbol can consume up to
    max_tranches * POSITION_VALUE_INR over its lifecycle. The summary's
    peak_concurrent_capital is the real rupee peak over all open tranches.
    """
    start_date, end_date = _resolve_window(
        config.requires_delivery, config.start_date, config.end_date
    )
    if config.window == "train":
        end_date = TRAIN_END
    elif config.window == "holdout":
        start_date = "2024-01-01"
        if not config.end_date:
            end_date = HOLDOUT_END

    trading_days = _trading_days(conn, start_date, end_date)
    empty_cols = [
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
    if not trading_days:
        return BacktestResult(
            trades=pd.DataFrame(columns=empty_cols),
            summary=_empty_summary(),
        )

    events = _precompute_kaushik_events(conn, recovery_mult=config.recovery_mult)
    pool = list(seed_universe) if seed_universe is not None else None
    universe_by_date = _preload_universe_by_date(
        conn, trading_days, universe_seed=pool, pit_universe=pit_universe
    )

    # Lazy per-symbol (date -> close) cache: positions can stay open longer
    # than 252 days and are re-anchored, so forward slices are insufficient.
    closes_cache: dict[str, dict[str, float]] = {}
    positions: dict[str, dict] = {}
    trades: list[dict] = []  # noqa: PG-APPEND
    peak_capital = 0.0
    last_idx = len(trading_days) - 1

    def _close_map(sym: str) -> dict[str, float]:
        if sym not in closes_cache:
            rows = conn.execute(
                "SELECT date, close FROM technical_data WHERE symbol = ? ORDER BY date",
                (sym,),
            ).fetchall()
            closes_cache[sym] = {d: float(c) for d, c in rows}
        return closes_cache[sym]

    def _entry_adv(sym: str, day_iso: str) -> Optional[float]:
        return _load_adv_window(conn, [sym], day_iso).get(sym)

    def _resolve_position(
        sym: str, exit_idx: int, exit_iso: str, close: float, reason: str
    ) -> None:
        pos = positions.pop(sym)
        n = pos["n_tranches"]
        # PnL over tranches: each tranche round-trips its own 10k at exit close.
        shares_total = sum(POSITION_VALUE_INR / p for p in pos["tranche_prices"])
        exit_value_total = shares_total * close
        pnl_gross = exit_value_total - POSITION_VALUE_INR * n
        costs_total = 0.0
        for k in range(n):
            cost_row = total_round_trip_costs(
                POSITION_VALUE_INR,
                (POSITION_VALUE_INR / pos["tranche_prices"][k]) * close,
                pos["tranche_adv"][k],
            )
            costs_total += cost_row["total"]
        trades.append(  # noqa: PG-APPEND
            {
                "entry_date": pos["tranche_days"][0],
                "exit_date": exit_iso,
                "symbol": sym,
                "entry_price": pos["tranche_prices"][0],
                "exit_price": close,
                "n_hold_days": exit_idx - pos["tranche_idx"][0],
                "pnl_gross": pnl_gross,
                "costs": costs_total,
                "pnl_net": pnl_gross - costs_total,
                "exit_reason": reason,
                "n_tranches": n,
                "blended_basis": pos["blended"],
                "tranche_dates": "|".join(pos["tranche_days"]),
                "tranche_prices": "|".join(f"{p:.2f}" for p in pos["tranche_prices"]),
            }
        )

    for day_idx, day_iso in enumerate(trading_days):
        eligible = universe_by_date.get(day_iso)
        if not eligible:
            continue

        # 1. Exits FIRST — runs EVERY trading day, not just signal days (a
        #    position must resolve on a quiet day too). The blended target /
        #    re-anchored cap applies to today's close; a position that exits
        #    today cannot average in.
        for sym in list(positions):
            pos = positions[sym]
            cm = _close_map(sym)
            close = cm.get(day_iso)
            exit_iso = day_iso
            idx = day_idx
            if close is None:
                # Data gap: look back a few trading days for the last close.
                back = 0
                for j in range(1, 11):
                    if day_idx - j < 0:
                        break
                    cj = cm.get(trading_days[day_idx - j])
                    if cj is not None:
                        close = cj
                        exit_iso = trading_days[day_idx - j]
                        idx = day_idx - j
                        back = j
                        break
                if close is None:
                    continue  # no usable price today — keep waiting
                if back >= 10:
                    # Effectively stopped trading: resolve at last known close.
                    _resolve_position(sym, idx, exit_iso, close, "pt_eod")
                    continue
            trigger = pos["blended"] * (1.0 + config.profit_target_pct)
            if close >= trigger:
                _resolve_position(
                    sym,
                    idx,
                    exit_iso,
                    close,
                    f"pt_target_{int(round(config.profit_target_pct * 1000))}bp",
                )
            elif (idx - pos["last_tranche_idx"]) >= config.profit_target_cap_days:
                _resolve_position(sym, idx, exit_iso, close, "pt_252d_cap")
            elif day_idx == last_idx:
                _resolve_position(sym, idx, exit_iso, close, "pt_eod")

        day_events = events.get(day_iso)
        if not day_events:
            continue

        univ = set(eligible)
        signals: dict[str, float] = {}
        for sym, (overshoot, _elev) in day_events.items():
            if sym in univ:
                # Same tie-break as the base method: smallest overshoot wins.
                signals[sym] = -float(overshoot)

        # 2. ENTRIES — top-1 NEW signal first (never blocked by averages),
        #    then average-ins for every signaling symbol already held.
        prev_open = set(positions)
        new_candidates = [s for s in signals if s not in prev_open]
        if new_candidates:
            top_sym = sorted(new_candidates, key=lambda s: (-signals[s], s))[0]
            entry_price = _close_map(top_sym).get(day_iso)
            if entry_price is not None:
                positions[top_sym] = {
                    "tranche_days": [day_iso],
                    "tranche_idx": [day_idx],
                    "tranche_prices": [entry_price],
                    "tranche_adv": [_entry_adv(top_sym, day_iso)],
                    "n_tranches": 1,
                    "blended": entry_price,
                    "last_tranche_idx": day_idx,
                }

        for sym in signals:
            if sym not in prev_open:
                continue  # positions opened TODAY start at tranche 1
            pos = positions.get(sym)
            if pos is None or pos["n_tranches"] >= config.max_tranches:
                continue  # tranche cap reached — resolve via target/cap only
            t_price = _close_map(sym).get(day_iso)
            if t_price is None:
                continue
            pos["tranche_days"].append(day_iso)  # noqa: PG-APPEND
            pos["tranche_idx"].append(day_idx)  # noqa: PG-APPEND
            pos["tranche_prices"].append(t_price)  # noqa: PG-APPEND
            pos["tranche_adv"].append(_entry_adv(sym, day_iso))  # noqa: PG-APPEND
            pos["n_tranches"] += 1
            # Share-weighted blended basis: total invested / total shares.
            # This matches the PnL formula (shares_total * close - capital).
            # Using arithmetic mean here would inflate the exit trigger for
            # multi-tranche trades, delaying exits and distorting PnL.
            total_invested = POSITION_VALUE_INR * pos["n_tranches"]
            total_shares = sum(POSITION_VALUE_INR / p for p in pos["tranche_prices"])
            pos["blended"] = total_invested / total_shares
            pos["last_tranche_idx"] = day_idx

        daily_capital = (
            sum(p["n_tranches"] for p in positions.values()) * POSITION_VALUE_INR
        )
        peak_capital = max(peak_capital, daily_capital)

    trades_df = pd.DataFrame(trades)
    summary = _compute_summary(trades_df, config, peak_capital_override=peak_capital)
    if not trades_df.empty:
        dist = trades_df["n_tranches"].value_counts().to_dict()
        summary["n_tranche_distribution"] = {
            int(k): int(v) for k, v in sorted(dist.items())
        }
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


def _compute_summary(
    trades: pd.DataFrame,
    config: BacktestConfig,
    peak_capital_override: Optional[float] = None,
) -> dict:
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
    # Averaging mode overrides with the real rupee peak over all open tranches,
    # since a 2-3 tranche symbol consumes 20k-30k, not one position slot.
    active_count_max = _max_concurrent_positions(trades)
    peak_concurrent_capital = (
        peak_capital_override
        if peak_capital_override is not None
        else active_count_max * POSITION_VALUE_INR
    )

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
# Post-hoc MAE/MFE enrichment and stop-sensitivity sweep.
# ──────────────────────────────────────────────────────────────────────────────


def compute_mae_mfe(
    trades_df: pd.DataFrame,
    conn: sqlite3.Connection,
) -> pd.DataFrame:
    """Post-hoc: add mae_pct and mfe_pct columns to a trade log.

    MAE (Maximum Adverse Excursion) = worst unrealized return (%) observed
    at any point during the hold, relative to the position's cost basis.
    MFE (Maximum Favorable Excursion) = best unrealized return (%).

    For single-tranche trades the basis is entry_price.
    For multi-tranche (averaging) trades the basis is the share-weighted
    blended cost basis (total invested / total shares = harmonic mean of
    tranche prices), matching both the exit trigger and PnL calculation.
    The trade log's blended_basis column stores this same share-weighted value.

    Uses the existing _load_prices_window() for OHLCV reload — no new data.
    """
    if trades_df.empty:
        out = trades_df.copy()
        out["mae_pct"] = pd.Series(dtype="float64")
        out["mfe_pct"] = pd.Series(dtype="float64")
        return out

    # Batch price loading per symbol to avoid repeated DB round-trips.
    price_cache: dict[str, pd.DataFrame] = {}
    symbols = trades_df["symbol"].unique().tolist()
    global_min = trades_df["entry_date"].min()
    global_max = trades_df["exit_date"].max()
    all_prices = _load_prices_window(conn, symbols, global_min, global_max)
    if not all_prices.empty:
        for sym, grp in all_prices.groupby("symbol"):
            price_cache[sym] = grp.set_index("date").sort_index()

    mae_list: list[float] = []
    mfe_list: list[float] = []

    for _, row in trades_df.iterrows():
        sym = row["symbol"]
        n_tr = row.get("n_tranches", 1) if "n_tranches" in trades_df.columns else 1
        if n_tr > 1 and "tranche_prices" in trades_df.columns:
            # Share-weighted basis: total_invested / total_shares.
            # Each tranche invests POSITION_VALUE_INR, so:
            #   basis = (n * POSITION_VALUE_INR) / sum(POSITION_VALUE_INR / p_i)
            #         = n / sum(1 / p_i)   (harmonic mean)
            try:
                t_prices = [
                    float(p) for p in str(row["tranche_prices"]).split("|") if p
                ]
                total_shares = sum(POSITION_VALUE_INR / p for p in t_prices)
                basis = (POSITION_VALUE_INR * len(t_prices)) / total_shares
            except (ValueError, ZeroDivisionError):
                basis = row["entry_price"]
        else:
            basis = row["entry_price"]
        prices = price_cache.get(sym)
        if prices is None or pd.isna(basis) or basis <= 0:
            mae_list.append(0.0)
            mfe_list.append(0.0)
            continue

        window = prices.loc[row["entry_date"] : row["exit_date"]]
        if window.empty:
            mae_list.append(0.0)
            mfe_list.append(0.0)
            continue

        low_min = float(window["low"].min())
        high_max = float(window["high"].max())
        mae_list.append((low_min / basis - 1.0) * 100.0)
        mfe_list.append((high_max / basis - 1.0) * 100.0)

    out = trades_df.copy()
    out["mae_pct"] = mae_list
    out["mfe_pct"] = mfe_list
    return out


def retrospective_stop_sweep(
    trades_df: pd.DataFrame,
    start_pct: float = 2.0,
    end_pct: float = 30.0,
    step_pct: float = 1.0,
) -> pd.DataFrame:
    """Sweep fixed stop-loss levels over MAE-enriched trade log.

    For each stop level s (as a positive percentage), evaluates the full
    trade population under two exit rules:
      - Non-breached trades keep their actual pnl_net.
      - Breached trades have PnL replaced with the realized loss at the
        stop level, approximated as:
          stop_pnl = -POSITION_VALUE_INR * n_tranches * s / 100
        (each tranche loses s% of its invested capital at the stop level;
         this is exact when the stop fires at the MAE price, and a
         close approximation when the stock gaps through the stop).

    Columns:
      - stop_pct:              the tested stop level
      - n_stopped:             trades whose MAE breached the stop
      - pct_stopped:           fraction of total
      - avg_pnl_full_pop:      mean pnl across ALL trades (stopped trades
                               replaced with stop-level loss) — directly
                               comparable to the no-stop baseline
      - avg_pnl_survivors:     mean pnl among non-breached trades only
      - winners_killed:        profitable trades stopped out prematurely
      - losers_stopped:        losing trades cut earlier

    Returns a DataFrame indexed by stop_pct.
    """
    if trades_df.empty or "mae_pct" not in trades_df.columns:
        return pd.DataFrame(
            columns=[
                "stop_pct",
                "n_stopped",
                "pct_stopped",
                "avg_pnl_full_pop",
                "avg_pnl_survivors",
                "winners_killed",
                "losers_stopped",
            ]
        )

    total = len(trades_df)
    is_winner = trades_df["pnl_net"] > 0

    # Pre-compute n_tranches per trade for stop-level PnL.
    if "n_tranches" in trades_df.columns:
        n_tr = trades_df["n_tranches"].fillna(1).astype(int).values
    else:
        n_tr = np.ones(total, dtype=int)
    pnlActual = trades_df["pnl_net"].values.astype(float)

    rows = []
    stop_levels = []
    level = start_pct
    while level <= end_pct + 1e-9:
        stop_levels.append(round(level, 2))
        level += step_pct

    for stop in stop_levels:
        breached = trades_df["mae_pct"].values <= -stop
        n_stopped = int(breached.sum())

        # Full-population average: stopped trades replaced with stop-level loss.
        stop_pnl_per_trade = np.where(
            breached,
            -POSITION_VALUE_INR * n_tr * stop / 100.0,
            pnlActual,
        )
        avg_full = float(stop_pnl_per_trade.mean())

        # Survivor-only average (kept for reference).
        survivors_mask = ~breached
        avg_surv = (
            float(pnlActual[survivors_mask].mean()) if survivors_mask.any() else 0.0
        )

        winners_killed = int((breached & is_winner).sum())
        losers_stopped = int((breached & ~is_winner).sum())
        rows.append(
            {
                "stop_pct": stop,
                "n_stopped": n_stopped,
                "pct_stopped": round(n_stopped / total * 100, 2) if total else 0.0,
                "avg_pnl_full_pop": round(avg_full, 2),
                "avg_pnl_survivors": round(avg_surv, 2),
                "winners_killed": winners_killed,
                "losers_stopped": losers_stopped,
            }
        )

    return pd.DataFrame(rows)


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
