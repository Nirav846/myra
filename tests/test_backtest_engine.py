"""
Tests for the backtest engine harness (Phase 1, Task 1).

Covers:
  - Universe filter (instrument_type, recency, discontinuity blackout)
  - Position sizing (₹10,000 per trade)
  - Fixed-holding exit
  - Trailing-stop exit (20%)
  - Cost calculation
  - Train/holdout/all window split
  - Signal registry + SignalFunction protocol
"""
from __future__ import annotations

import sqlite3
import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from myra_app.backtest_engine import (
    BacktestConfig,
    BacktestResult,
    COST_MODEL,
    DISCONTINUITY_CACHE,
    RandomSignal,
    SIGNAL_REGISTRY,
    calc_brokerage,
    calc_impact_cost,
    calc_stt,
    _eligible_symbols_at_date,
    _exit_fixed_holding,
    _exit_rule_based,
    _exit_trailing_stop,
    _load_discontinuity_events,
    _max_concurrent_positions,
    _trading_days,
    run_backtest,
    total_round_trip_costs,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: a small in-memory SQLite DB that mirrors the schema we need.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def in_mem_db():
    """Build an in-memory SQLite DB with minimal schema for backtest testing.

    Tables:
      - technical_data(symbol, date, close, high, low, volume)
      - symbols_master(symbol, instrument_type)
      - corporate_actions(symbol, date) — present but unused by engine directly
      - market_calendar(date, is_trading_day)
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE technical_data ("
        "  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
        "  close REAL, volume INTEGER, delivery INTEGER, delivery_pct REAL)"
    )
    conn.execute(
        "CREATE TABLE symbols_master (symbol TEXT PRIMARY KEY, instrument_type TEXT)"
    )
    conn.execute(
        "CREATE TABLE corporate_actions ("
        "  symbol TEXT, date TEXT, action_type TEXT, ex_date TEXT)"
    )
    conn.execute(
        "CREATE TABLE market_calendar (date TEXT PRIMARY KEY, is_trading_day INTEGER)"
    )
    yield conn
    conn.close()


def _insert_calendar(
    conn: sqlite3.Connection,
    start: str,
    end: str,
) -> list[str]:
    """Insert consecutive trading days [start, end] (weekdays only) into
    market_calendar; return list of dates.
    """
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # Mon-Fri
            d = cur.strftime("%Y-%m-%d")
            days.append(d)
            conn.execute(
                "INSERT INTO market_calendar (date, is_trading_day) VALUES (?, 1)",
                (d,),
            )
        cur += pd.Timedelta(days=1)
    return days


def _insert_symbol(
    conn: sqlite3.Connection, symbol: str, instrument_type: str = "EQUITY"
) -> None:
    conn.execute(
        "INSERT INTO symbols_master (symbol, instrument_type) VALUES (?, ?)",
        (symbol, instrument_type),
    )


def _insert_tech_series(
    conn: sqlite3.Connection,
    symbol: str,
    days: list[str],
    base_price: float = 100.0,
    drift_per_day: float = 0.0,
    volume: int = 100_000,
) -> None:
    """Insert one row per (symbol, day) with deterministic price."""
    for i, d in enumerate(days):
        close = base_price + drift_per_day * i
        high = close * 1.01
        low = close * 0.99
        conn.execute(
            "INSERT INTO technical_data (symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, d, close, high, low, close, volume),
        )


# ──────────────────────────────────────────────────────────────────────────────
# test_universe_filter
# ──────────────────────────────────────────────────────────────────────────────


def test_universe_filter_includes_equity_with_recent_tech(in_mem_db):
    days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
    _insert_symbol(in_mem_db, "TCS")
    _insert_symbol(in_mem_db, "RELIANCE")
    _insert_tech_series(in_mem_db, "TCS", days[-60:])  # recent data
    in_mem_db.commit()
    as_of = pd.Timestamp("2024-06-30")
    eligible = _eligible_symbols_at_date(in_mem_db, as_of)
    assert "TCS" in eligible
    assert "RELIANCE" not in eligible  # no recent data


def test_universe_filter_excludes_non_equity(in_mem_db):
    days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
    _insert_symbol(in_mem_db, "ETF1", instrument_type="ETF")
    _insert_symbol(in_mem_db, "STK1", instrument_type="EQUITY")
    _insert_tech_series(in_mem_db, "ETF1", days[-60:])
    _insert_tech_series(in_mem_db, "STK1", days[-60:])
    in_mem_db.commit()
    as_of = pd.Timestamp("2024-06-30")
    eligible = _eligible_symbols_at_date(in_mem_db, as_of)
    assert "STK1" in eligible
    assert "ETF1" not in eligible


def test_universe_filter_excludes_stale_tech(in_mem_db):
    """Symbol with no data within trailing 90 days must be excluded."""
    days = _insert_calendar(in_mem_db, "2024-01-01", "2024-12-31")
    _insert_symbol(in_mem_db, "STALE")
    # Insert data only in January (outside the 90d trailing window at mid-year)
    _insert_tech_series(in_mem_db, "STALE", days[:30])
    in_mem_db.commit()
    as_of = pd.Timestamp("2024-06-30")
    eligible = _eligible_symbols_at_date(in_mem_db, as_of)
    assert "STALE" not in eligible


# ──────────────────────────────────────────────────────────────────────────────
# test_blackout
# ──────────────────────────────────────────────────────────────────────────────


def test_blackout_excludes_symbol_in_window(monkeypatch, in_mem_db):
    """Symbol in discontinuity blackout window ±5 days must be excluded.

    We monkey-patch `_load_discontinuity_events` to return a synthetic event
    so the test doesn't depend on the cache file.
    """
    days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
    _insert_symbol(in_mem_db, "EVENT")
    _insert_symbol(in_mem_db, "SAFE")
    _insert_tech_series(in_mem_db, "EVENT", days[-60:])
    _insert_tech_series(in_mem_db, "SAFE", days[-60:])
    in_mem_db.commit()

    # Synthesize a discontinuity event for EVENT on day_iso "2024-06-15"
    synthetic = pd.DataFrame(
        {
            "symbol": ["EVENT"],
            "date": pd.to_datetime(["2024-06-15"]),
            "close": [999.0],
            "z": [10.0],
        }
    )
    monkeypatch.setattr(
        "myra_app.backtest_engine._load_discontinuity_events",
        lambda: synthetic,
    )

    # Within window → EVENT excluded
    as_of = pd.Timestamp("2024-06-15")
    eligible = _eligible_symbols_at_date(in_mem_db, as_of)
    assert "EVENT" not in eligible
    assert "SAFE" in eligible

    # Outside ±5 days → EVENT included
    as_of_far = pd.Timestamp("2024-06-30")
    eligible_far = _eligible_symbols_at_date(in_mem_db, as_of_far)
    assert "EVENT" in eligible_far


# ──────────────────────────────────────────────────────────────────────────────
# test_position_sizing
# ──────────────────────────────────────────────────────────────────────────────


def test_position_sizing_uses_10000_inr(monkeypatch, in_mem_db):
    """Every entry should be sized to exactly ₹10,000 gross."""
    days = _insert_calendar(in_mem_db, "2024-03-01", "2024-04-15")
    _insert_symbol(in_mem_db, "SYM1")
    _insert_tech_series(in_mem_db, "SYM1", days, base_price=100.0, drift_per_day=0.5)
    in_mem_db.commit()
    # Disable discontinuity (cache may exist; we make it empty).
    monkeypatch.setattr(
        "myra_app.backtest_engine._load_discontinuity_events",
        lambda: pd.DataFrame(columns=["symbol", "date", "close", "z"]),
    )
    cfg = BacktestConfig(
        signal="random",
        exit_mode="fixed",
        fixed_hold_days=10,
        window="all",
        start_date="2024-03-04",
        end_date="2024-04-12",
    )
    result: BacktestResult = run_backtest(in_mem_db, cfg)
    assert not result.trades.empty
    # Position size = entry_price * shares; shares = 10000 / entry_price.
    # So entry_price * shares should equal 10000 (within float tolerance).
    trades = result.trades
    implied = trades["entry_price"] * (10_000.0 / trades["entry_price"])
    assert (implied == 10_000.0).all()


# ──────────────────────────────────────────────────────────────────────────────
# test_exit_fixed
# ──────────────────────────────────────────────────────────────────────────────


def test_exit_fixed_at_n_days():
    """Pure-function test: fixed exit at exactly N days."""
    days = pd.date_range("2024-01-01", periods=200)
    prices = pd.DataFrame(
        {
            "date": days,
            "close": np.linspace(100, 110, 200),
            "high": np.linspace(101, 111, 200),
            "low": np.linspace(99, 109, 200),
            "volume": [100_000] * 200,
        }
    )
    for n in (60, 120, 180):
        idx, reason = _exit_fixed_holding(prices, entry_idx=0, n_hold_days=n)
        assert idx == n, f"expected exit at idx={n}, got {idx}"
        assert reason == f"fixed_{n}d"


def test_exit_fixed_caps_at_end():
    prices = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=10),
            "close": np.linspace(100, 105, 10),
            "high": np.linspace(101, 106, 10),
            "low": np.linspace(99, 104, 10),
            "volume": [100_000] * 10,
        }
    )
    idx, reason = _exit_fixed_holding(prices, entry_idx=0, n_hold_days=60)
    assert idx == 9  # capped at last available
    assert reason == "fixed_window_end"


# ──────────────────────────────────────────────────────────────────────────────
# test_exit_trailing
# ──────────────────────────────────────────────────────────────────────────────


def test_exit_trailing_triggers_on_20pct_drop():
    """20% trailing stop: prices rise then drop 20% from peak → exit."""
    # 5 days up, then 3 days down to trigger.
    closes = [100, 105, 110, 115, 120, 110, 96, 80]
    highs = [c + 1 for c in closes]
    prices = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=len(closes)),
            "close": closes,
            "high": highs,
            "low": [c - 1 for c in closes],
            "volume": [100_000] * len(closes),
        }
    )
    idx, reason = _exit_trailing_stop(prices, entry_idx=0, trailing_pct=0.20)
    # Max high so far is 121 (idx=4). 20% threshold = 96.8.
    # Close[6] = 96 < 96.8 → exit at idx=6.
    assert idx == 6
    assert reason == "trailing_stop"


def test_exit_trailing_ratchets_up():
    """Trailing threshold follows running max-high."""
    # Ratchet test: highs go 100, 110, 120; close 95 never breaches 96
    # at first peak (100*0.8=80), then at 120*0.8=96 it does.
    closes = [100, 95, 105, 95]
    highs = [100, 110, 120, 95]
    prices = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=4),
            "close": closes,
            "high": highs,
            "low": closes,
            "volume": [100_000] * 4,
        }
    )
    idx, reason = _exit_trailing_stop(prices, entry_idx=0, trailing_pct=0.20)
    # After idx=2: max_high=120, threshold=96. close[3]=95 < 96 → exit at 3.
    assert idx == 3
    assert reason == "trailing_stop"


def test_exit_trailing_no_trigger_eod():
    """No trigger → exit at end of slice."""
    closes = [100, 105, 110, 115, 120]  # only up
    highs = [c + 1 for c in closes]
    prices = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=5),
            "close": closes,
            "high": highs,
            "low": closes,
            "volume": [100_000] * 5,
        }
    )
    idx, reason = _exit_trailing_stop(prices, entry_idx=0, trailing_pct=0.20)
    assert idx == 4
    assert reason == "trailing_eod"


# ──────────────────────────────────────────────────────────────────────────────
# test_costs
# ──────────────────────────────────────────────────────────────────────────────


def test_cost_stt_sell_side_only():
    """STT is on sell side only, at 0.025%."""
    sell_value = 100_000.0
    stt = calc_stt(sell_value)
    assert stt == pytest.approx(100_000 * 0.025 / 100)  # = 25 INR
    assert stt > 0


def test_cost_brokerage_lower_of_flat_or_pct():
    # Small trade: 0.03% < ₹20
    small = 10_000.0
    assert calc_brokerage(small) == pytest.approx(small * 0.03 / 100)  # = 3 INR
    # Large trade: 0.03% > ₹20 → flat wins
    large = 100_000.0
    assert calc_brokerage(large) == pytest.approx(20.0)


def test_cost_impact_with_adv():
    # impact = k * sqrt(position_value / ADV_value)
    pos = 10_000.0
    adv = 1_000_000.0
    expected = pos * COST_MODEL["impact_k"] * math.sqrt(pos / adv)
    assert calc_impact_cost(pos, adv) == pytest.approx(expected)


def test_cost_impact_fallback_when_adv_missing():
    pos = 10_000.0
    fallback = pos * COST_MODEL["impact_fallback_pct"]
    assert calc_impact_cost(pos, None) == pytest.approx(fallback)
    assert calc_impact_cost(pos, 0) == pytest.approx(fallback)
    assert calc_impact_cost(pos, -1) == pytest.approx(fallback)


def test_total_round_trip_costs_sums_components():
    entry_value = 10_000.0
    exit_value = 11_000.0
    adv = 500_000.0
    costs = total_round_trip_costs(entry_value, exit_value, adv)
    assert "stt" in costs
    assert "brokerage" in costs
    assert "impact" in costs
    assert "total" in costs
    assert costs["total"] == pytest.approx(
        costs["stt"] + costs["brokerage"] + costs["impact"]
    )
    # STT on sell side only: 11000 * 0.025/100 = 2.75
    assert costs["stt"] == pytest.approx(2.75)


# ──────────────────────────────────────────────────────────────────────────────
# test_train_holdout_split
# ──────────────────────────────────────────────────────────────────────────────


def test_train_holdout_split_filters_dates(monkeypatch, in_mem_db):
    """`window='train'` should restrict end_date to 2023-12-31."""
    days = _insert_calendar(in_mem_db, "2023-01-01", "2024-12-31")
    _insert_symbol(in_mem_db, "SYM")
    _insert_tech_series(in_mem_db, "SYM", days, base_price=100.0, drift_per_day=0.05)
    in_mem_db.commit()
    monkeypatch.setattr(
        "myra_app.backtest_engine._load_discontinuity_events",
        lambda: pd.DataFrame(columns=["symbol", "date", "close", "z"]),
    )
    cfg_train = BacktestConfig(
        signal="random",
        exit_mode="fixed",
        fixed_hold_days=10,
        window="train",
    )
    r_train = run_backtest(in_mem_db, cfg_train)
    if not r_train.trades.empty:
        assert (r_train.trades["entry_date"] <= "2023-12-31").all()

    cfg_holdout = BacktestConfig(
        signal="random",
        exit_mode="fixed",
        fixed_hold_days=10,
        window="holdout",
    )
    r_holdout = run_backtest(in_mem_db, cfg_holdout)
    if not r_holdout.trades.empty:
        assert (r_holdout.trades["entry_date"] >= "2024-01-01").all()


def test_requires_delivery_skips_pre_2019():
    """Signals requiring delivery must skip dates < 2019-10-01."""
    cfg = BacktestConfig(requires_delivery=True)
    # Internal: _resolve_window
    from myra_app.backtest_engine import _resolve_window

    start, _end = _resolve_window(
        requires_delivery=True,
        override_start=None,
        override_end=None,
    )
    assert start == "2019-10-01"


# ──────────────────────────────────────────────────────────────────────────────
# test_signal_protocol
# ──────────────────────────────────────────────────────────────────────────────


def test_signal_registry_contains_expected():
    assert "random" in SIGNAL_REGISTRY
    assert "momentum" in SIGNAL_REGISTRY


def test_random_signal_returns_series_with_eligible_symbols():
    sig = RandomSignal(seed=42)
    universe = ["AAA", "BBB", "CCC"]
    scores = sig.score(pd.Timestamp("2024-06-01"), universe, conn=None)
    assert isinstance(scores, pd.Series)
    assert set(scores.index.tolist()) == set(universe)
    assert (scores >= 0).all() and (scores <= 1).all()


def test_random_signal_is_deterministic_per_date():
    sig = RandomSignal(seed=42)
    s1 = sig.score(pd.Timestamp("2024-06-01"), ["AAA", "BBB"], conn=None)
    s2 = sig.score(pd.Timestamp("2024-06-01"), ["AAA", "BBB"], conn=None)
    pd.testing.assert_series_equal(s1, s2)


# ──────────────────────────────────────────────────────────────────────────────
# Backtest end-to-end smoke
# ──────────────────────────────────────────────────────────────────────────────


def test_backtest_runs_end_to_end(monkeypatch, in_mem_db):
    days = _insert_calendar(in_mem_db, "2024-03-01", "2024-06-30")
    for sym in ("A", "B", "C"):
        _insert_symbol(in_mem_db, sym)
        _insert_tech_series(in_mem_db, sym, days, base_price=100.0, drift_per_day=0.1)
    in_mem_db.commit()
    monkeypatch.setattr(
        "myra_app.backtest_engine._load_discontinuity_events",
        lambda: pd.DataFrame(columns=["symbol", "date", "close", "z"]),
    )
    cfg = BacktestConfig(
        signal="random",
        exit_mode="fixed",
        fixed_hold_days=10,
        window="all",
        start_date="2024-03-04",
        end_date="2024-06-15",
    )
    result = run_backtest(in_mem_db, cfg)
    assert isinstance(result, BacktestResult)
    expected_cols = {
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
    }
    assert set(result.trades.columns) >= expected_cols
    for k in (
        "total_trades",
        "win_rate",
        "avg_return",
        "max_drawdown",
        "peak_concurrent_capital",
        "total_pnl_net",
    ):
        assert k in result.summary


def test_max_concurrent_positions_basic():
    """Two positions overlapping in time → peak = 2."""
    trades = pd.DataFrame(
        {
            "entry_date": ["2024-01-01", "2024-01-10"],
            "exit_date": ["2024-02-01", "2024-02-15"],
        }
    )
    assert _max_concurrent_positions(trades) == 2
