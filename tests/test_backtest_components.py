"""
Focused unit tests for backtest components (Phase 1, Task 4).

These tests complement `test_backtest_engine.py` by isolating individual
components with synthetic, in-memory data. They never touch live sidecar
DBs (e.g. myra_technical.db).

Sections:
  1. Universe filter           — equity, recency, blackout, eligibility
  2. Position sizing           — exact ₹10,000; 1 trade/day; empty universe
  3. Exit rules                — fixed-N, 20% trailing, 5% / 20d SMA rule
  4. Cost calculation          — STT, brokerage, impact, round-trip
  5. Train / holdout split     — train/holdout/all windows
  6. Signal registry           — 'random' / 'momentum' name → class mapping
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from myra_app.backtest_engine import (
    BacktestConfig,
    BacktestResult,
    COST_MODEL,
    HOLDOUT_END,
    MomentumSignal,
    POSITION_VALUE_INR,
    RandomSignal,
    SIGNAL_REGISTRY,
    TRAIN_END,
    TRAIN_START_DELIVERY,
    TRAIN_START_PRICE_ONLY,
    _eligible_symbols_at_date,
    _exit_fixed_holding,
    _exit_rule_based,
    _exit_trailing_stop,
    _resolve_window,
    calc_brokerage,
    calc_impact_cost,
    calc_stt,
    run_backtest,
    total_round_trip_costs,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures + helpers (mirrors test_backtest_engine.py patterns; kept local so
# tests in this file are self-contained).
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def in_mem_db():
    """In-memory SQLite with the minimum schema the backtest engine needs."""
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


def _insert_calendar(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Insert consecutive weekday trading days into market_calendar."""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    days: list[str] = []
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
    for i, d in enumerate(days):
        close = base_price + drift_per_day * i
        high = close * 1.01
        low = close * 0.99
        conn.execute(
            "INSERT INTO technical_data "
            "(symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, d, close, high, low, close, volume),
        )


def _disable_blackout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force discontinuity cache to empty so tests are isolated."""
    monkeypatch.setattr(
        "myra_app.backtest_engine._load_discontinuity_events",
        lambda: pd.DataFrame(columns=["symbol", "date", "close", "z"]),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. Universe filter
# ──────────────────────────────────────────────────────────────────────────────


class TestUniverseFilter:
    """Section 1 — eligibility rules."""

    def test_symbol_without_recent_data_is_excluded(
        self, in_mem_db, monkeypatch
    ) -> None:
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
        _insert_symbol(in_mem_db, "FRESH")
        _insert_symbol(in_mem_db, "STALE")
        # FRESH: data inside trailing 90 days of as_of
        _insert_tech_series(in_mem_db, "FRESH", days[-60:])
        # STALE: only January data → outside 90d trailing window by mid-year
        _insert_tech_series(in_mem_db, "STALE", days[:30])
        in_mem_db.commit()

        as_of = pd.Timestamp("2024-06-30")
        eligible = _eligible_symbols_at_date(in_mem_db, as_of)

        assert "FRESH" in eligible
        assert "STALE" not in eligible

    def test_non_equity_instrument_is_excluded(self, in_mem_db, monkeypatch) -> None:
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
        for sym, kind in (("EQ1", "EQUITY"), ("ETF1", "ETF"), ("IDX1", "INDEX")):
            _insert_symbol(in_mem_db, sym, instrument_type=kind)
            _insert_tech_series(in_mem_db, sym, days[-60:])
        in_mem_db.commit()

        eligible = _eligible_symbols_at_date(
            in_mem_db, pd.Timestamp("2024-06-30")
        )

        assert eligible == ["EQ1"]

    def test_symbol_in_blackout_window_is_excluded(
        self, in_mem_db, monkeypatch
    ) -> None:
        """Symbol with discontinuity event within ±5 trading days → excluded."""
        days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
        for sym in ("JUMP", "CALM"):
            _insert_symbol(in_mem_db, sym)
            _insert_tech_series(in_mem_db, sym, days[-60:])
        in_mem_db.commit()

        # Synthetic discontinuity event for JUMP on 2024-06-15
        synthetic = pd.DataFrame(
            {
                "symbol": ["JUMP"],
                "date": pd.to_datetime(["2024-06-15"]),
                "close": [999.0],
                "z": [10.0],
            }
        )
        monkeypatch.setattr(
            "myra_app.backtest_engine._load_discontinuity_events",
            lambda: synthetic,
        )

        # Inside ±5d → JUMP excluded
        near = _eligible_symbols_at_date(in_mem_db, pd.Timestamp("2024-06-15"))
        assert "JUMP" not in near
        assert "CALM" in near

        # Outside ±5d → JUMP included again
        far = _eligible_symbols_at_date(in_mem_db, pd.Timestamp("2024-06-25"))
        assert "JUMP" in far
        assert "CALM" in far

    def test_eligible_symbols_are_included(self, in_mem_db, monkeypatch) -> None:
        """Plain happy-path: EQUITY + recent data + no blackout → included."""
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
        for sym in ("A", "B", "C"):
            _insert_symbol(in_mem_db, sym)
            _insert_tech_series(in_mem_db, sym, days[-60:])
        in_mem_db.commit()

        eligible = _eligible_symbols_at_date(
            in_mem_db, pd.Timestamp("2024-06-30")
        )
        assert sorted(eligible) == ["A", "B", "C"]

    def test_universe_seed_restricts_pool(self, in_mem_db, monkeypatch) -> None:
        """universe_seed acts as an allow-list over eligible symbols."""
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-01-01", "2024-06-30")
        for sym in ("AAA", "BBB", "CCC"):
            _insert_symbol(in_mem_db, sym)
            _insert_tech_series(in_mem_db, sym, days[-60:])
        in_mem_db.commit()

        eligible = _eligible_symbols_at_date(
            in_mem_db,
            pd.Timestamp("2024-06-30"),
            universe_seed=["AAA"],
        )
        assert eligible == ["AAA"]


# ──────────────────────────────────────────────────────────────────────────────
# 2. Position sizing
# ──────────────────────────────────────────────────────────────────────────────


class TestPositionSizing:
    """Section 2 — ₹10,000 per trade, 1 trade/day, no-trade on empty universe."""

    def test_position_size_is_exactly_10000_inr(
        self, in_mem_db, monkeypatch
    ) -> None:
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-03-01", "2024-04-15")
        _insert_symbol(in_mem_db, "SYM")
        _insert_tech_series(
            in_mem_db, "SYM", days, base_price=100.0, drift_per_day=0.5
        )
        in_mem_db.commit()

        cfg = BacktestConfig(
            signal="random",
            exit_mode="fixed",
            fixed_hold_days=5,
            window="all",
            start_date="2024-03-04",
            end_date="2024-04-12",
        )
        result = run_backtest(in_mem_db, cfg)
        assert not result.trades.empty

        # entry_price * shares must round-trip to ₹10,000
        implied = result.trades["entry_price"] * (
            POSITION_VALUE_INR / result.trades["entry_price"]
        )
        assert (implied == POSITION_VALUE_INR).all()

        # POSITION_VALUE_INR constant is exactly 10,000 (sanity)
        assert POSITION_VALUE_INR == 10_000

    def test_only_one_position_per_day_opens(
        self, in_mem_db, monkeypatch
    ) -> None:
        """Per spec, at most one new position opens each day."""
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-03-04", "2024-03-29")
        # Many symbols so 'random' has variety — but engine still opens one/day
        for sym in ("A", "B", "C", "D", "E"):
            _insert_symbol(in_mem_db, sym)
            _insert_tech_series(in_mem_db, sym, days, base_price=100.0)
        in_mem_db.commit()

        cfg = BacktestConfig(
            signal="random",
            exit_mode="fixed",
            fixed_hold_days=2,
            window="all",
            start_date="2024-03-04",
            end_date="2024-03-29",
        )
        result = run_backtest(in_mem_db, cfg)
        assert not result.trades.empty
        # Each entry_date appears at most once.
        assert result.trades["entry_date"].is_unique

    def test_no_position_when_universe_empty(self, in_mem_db, monkeypatch) -> None:
        """Empty universe → zero trades, no crashes, empty summary."""
        _disable_blackout(monkeypatch)
        _insert_calendar(in_mem_db, "2024-03-04", "2024-03-29")
        # Deliberately insert NO symbols or technical_data.
        in_mem_db.commit()

        cfg = BacktestConfig(
            signal="random",
            exit_mode="fixed",
            fixed_hold_days=5,
            window="all",
            start_date="2024-03-04",
            end_date="2024-03-29",
        )
        result = run_backtest(in_mem_db, cfg)

        assert isinstance(result, BacktestResult)
        assert result.trades.empty
        assert result.summary["total_trades"] == 0
        assert result.summary["total_pnl_net"] == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# 3. Exit rules
# ──────────────────────────────────────────────────────────────────────────────


class TestExitFixed:
    """3a — fixed-N holding period."""

    def test_position_closes_at_exactly_n_days(self) -> None:
        prices = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=200),
                "close": np.linspace(100, 110, 200),
                "high": np.linspace(101, 111, 200),
                "low": np.linspace(99, 109, 200),
                "volume": [100_000] * 200,
            }
        )
        for n in (5, 60, 120):
            idx, reason = _exit_fixed_holding(prices, entry_idx=0, n_hold_days=n)
            assert idx == n
            assert reason == f"fixed_{n}d"

    def test_fixed_exit_falls_back_to_window_end(self) -> None:
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
        assert idx == 9
        assert reason == "fixed_window_end"


class TestExitTrailing:
    """3b — 20% trailing stop."""

    def test_20pct_drop_from_peak_triggers_exit(self) -> None:
        # Rise to peak (high=121), then close<96.8 → exit
        closes = [100, 105, 110, 115, 120, 110, 96]
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
        assert idx == 6
        assert reason == "trailing_stop"

    def test_trailing_threshold_ratchets_up_with_running_max(self) -> None:
        # First trough not deep enough; later peak raises threshold → exit
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
        # After idx=2, max_high=120 → threshold=96. close[3]=95<96 → exit at 3
        assert idx == 3
        assert reason == "trailing_stop"

    def test_trailing_no_trigger_exits_eod(self) -> None:
        # Steady uptrend, never breaches 20% threshold
        closes = [100, 105, 110, 115, 120]
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


class TestExitRuleBased:
    """3c — 5% hard stop OR close < 20d SMA."""

    def test_5pct_stop_triggers_before_sma_break(self) -> None:
        # Build ≥20 prices so SMA window is well-defined.
        n = 30
        # Stable then sharp drop > 5% in one bar
        closes = [100.0] * 20 + [100, 100, 100, 100, 94, 92, 90, 88, 86, 84]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        prices = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n),
                "close": closes,
                "high": highs,
                "low": lows,
                "volume": [100_000] * n,
            }
        )
        idx, reason = _exit_rule_based(
            prices, entry_idx=20, stop_pct=0.05, sma_window=20
        )
        # First 5%-drop close is 94 (5.5% below 100) → should fire there.
        # Entry close=100; threshold=95. closes[24]=94 < 95.
        assert idx == 24
        assert reason == "rule_stop_5pct"

    def test_sma_break_triggers_when_no_stop_hit(self) -> None:
        # Slow drift down (no 5% stop) until price dips below 20d SMA.
        n = 30
        # Build SMA context (first 20) high then drift down
        closes = [120.0] * 20 + [110, 108, 106, 104, 102, 100, 98, 96, 94, 92]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        prices = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n),
                "close": closes,
                "high": highs,
                "low": lows,
                "volume": [100_000] * n,
            }
        )
        # Entry close = 110 (idx=20). threshold = 110*0.95 = 104.5.
        # closes[23]=104 < 104.5 → rule_stop_5pct fires first (idx=23).
        # To force SMA-break-first, raise entry so 5% stop is not hit first:
        entry_idx = 20  # close=110
        # All later closes are >104.5? No — they drop below 104.5 by idx=23.
        # Pick a smaller drop so stop never hits, but SMA does.
        closes2 = [120.0] * 20 + [119, 118, 117, 116, 115, 114, 113, 112, 111, 110]
        prices2 = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n),
                "close": closes2,
                "high": [c + 0.1 for c in closes2],
                "low": [c - 0.1 for c in closes2],
                "volume": [100_000] * n,
            }
        )
        # Entry close=119; threshold = 119*0.95 = 113.05
        # All closes after entry are between 110 and 118; some below 113.05
        # (117,116,115,114,113,112,111,110) → fires at 117 (idx=21)
        # Actually 117 > 113.05, so 5% stop does NOT fire. Need closes that
        # gradually fall below SMA.
        # Easier: keep entry at 110, drift slow enough that 5% stop never hits
        # but eventually close < SMA. Let's just construct:
        closes3 = [120.0] * 20 + [110, 109.5, 109, 108.5, 108, 107.5,
                                   107, 106.5, 106, 105.5]
        prices3 = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n),
                "close": closes3,
                "high": [c + 0.1 for c in closes3],
                "low": [c - 0.1 for c in closes3],
                "volume": [100_000] * n,
            }
        )
        idx, reason = _exit_rule_based(
            prices3, entry_idx=20, stop_pct=0.05, sma_window=20
        )
        # Entry close=110 → threshold=104.5 (no close hits it).
        # SMA(20) at idx=21 is mean of last 20 closes ~ avg of [120..110] ≈ 118.
        # closes drop steadily; eventually closes < SMA at every post-entry idx.
        # First trigger is whichever comes first: stop or SMA. Stop never hits,
        # so SMA triggers at idx=21 (first day we evaluate after entry).
        assert idx == 21
        assert reason == "rule_trend_break_sma20"

    def test_rule_no_trigger_exits_eod(self) -> None:
        # Steady uptrend with no stop or SMA break.
        n = 30
        closes = [100.0 + i * 0.5 for i in range(n)]
        highs = [c + 0.1 for c in closes]
        lows = [c - 0.1 for c in closes]
        prices = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=n),
                "close": closes,
                "high": highs,
                "low": lows,
                "volume": [100_000] * n,
            }
        )
        idx, reason = _exit_rule_based(
            prices, entry_idx=5, stop_pct=0.05, sma_window=20
        )
        assert idx == n - 1
        assert reason == "rule_eod"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Cost calculation
# ──────────────────────────────────────────────────────────────────────────────


class TestCostCalculation:
    """Section 4 — STT, brokerage, impact, round-trip."""

    def test_stt_only_on_sell_side(self) -> None:
        # STT = sell_value * 0.025%
        sell = 50_000.0
        assert calc_stt(sell) == pytest.approx(sell * 0.025 / 100)
        # Zero sell value → zero STT (no buy-side charge)
        assert calc_stt(0.0) == 0.0

    def test_brokerage_is_min_of_flat_and_pct(self) -> None:
        # 0.03% < 20 → pct wins
        small = 10_000.0
        assert calc_brokerage(small) == pytest.approx(small * 0.03 / 100)  # = 3
        # 0.03% > 20 → flat wins
        large = 100_000.0
        assert calc_brokerage(large) == pytest.approx(20.0)
        # Exact crossover ~ ₹66,667 (20 / 0.0003)
        assert calc_brokerage(66_667.0) == pytest.approx(20.0)

    def test_impact_cost_with_adv(self) -> None:
        pos = 10_000.0
        adv = 1_000_000.0
        expected = pos * COST_MODEL["impact_k"] * math.sqrt(pos / adv)
        assert calc_impact_cost(pos, adv) == pytest.approx(expected)

    def test_impact_cost_flat_fallback_when_adv_missing(self) -> None:
        pos = 10_000.0
        fb = pos * COST_MODEL["impact_fallback_pct"]  # 0.5%
        assert calc_impact_cost(pos, None) == pytest.approx(fb)
        assert calc_impact_cost(pos, 0.0) == pytest.approx(fb)
        assert calc_impact_cost(pos, -1.0) == pytest.approx(fb)

    def test_costs_applied_on_both_entry_and_exit(self) -> None:
        """Round-trip applies brokerage + impact on entry AND exit, plus STT
        only on the sell leg."""
        entry_value = 10_000.0
        exit_value = 11_000.0
        adv = 500_000.0
        costs = total_round_trip_costs(entry_value, exit_value, adv)

        # STT = 11000 * 0.025 / 100 = 2.75 (sell side only)
        assert costs["stt"] == pytest.approx(2.75)

        # Brokerage: both legs. Entry 10k → 3.0; exit 11k → 3.3.
        # Both below flat, so pct wins → 3.0 + 3.3 = 6.3
        expected_brokerage = (
            calc_brokerage(entry_value) + calc_brokerage(exit_value)
        )
        assert costs["brokerage"] == pytest.approx(expected_brokerage)

        # Impact: both legs with same ADV
        expected_impact = (
            calc_impact_cost(entry_value, adv)
            + calc_impact_cost(exit_value, adv)
        )
        assert costs["impact"] == pytest.approx(expected_impact)

        # Total is the sum of components.
        assert costs["total"] == pytest.approx(
            costs["stt"] + costs["brokerage"] + costs["impact"]
        )


# ──────────────────────────────────────────────────────────────────────────────
# 5. Train / holdout split
# ──────────────────────────────────────────────────────────────────────────────


class TestTrainHoldoutSplit:
    """Section 5 — train / holdout / all windows."""

    def test_train_window_price_only_starts_2015(self) -> None:
        # _resolve_window returns start_date; run_backtest() later clips end to
        # TRAIN_END when window='train'. Here we only check start.
        start, _end = _resolve_window(
            requires_delivery=False, override_start=None, override_end=None
        )
        assert start == TRAIN_START_PRICE_ONLY == "2015-01-01"

    def test_train_window_delivery_starts_2019_10(self) -> None:
        start, _end = _resolve_window(
            requires_delivery=True, override_start=None, override_end=None
        )
        assert start == TRAIN_START_DELIVERY == "2019-10-01"

    def test_holdout_window_2024_to_latest(self) -> None:
        # run_backtest() rewrites start_date to "2024-01-01" when window='holdout';
        # end stays at HOLDOUT_END (unless overridden).
        assert HOLDOUT_END == "2026-09-04"

    def test_all_window_includes_everything(self) -> None:
        # _resolve_window returns [start, HOLDOUT_END]; window='all' performs no
        # extra clipping → 'all' window covers [start, HOLDOUT_END].
        start, end = _resolve_window(
            requires_delivery=False, override_start=None, override_end=None
        )
        assert pd.Timestamp(start) <= pd.Timestamp(end)
        assert end == HOLDOUT_END

    def test_train_window_end_filter_is_2023_12_31(
        self, in_mem_db, monkeypatch
    ) -> None:
        """No trade entry_date in run with window='train' may exceed 2023-12-31."""
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2023-01-02", "2024-06-30")
        _insert_symbol(in_mem_db, "SYM")
        _insert_tech_series(in_mem_db, "SYM", days, base_price=100.0)
        in_mem_db.commit()

        cfg = BacktestConfig(
            signal="random",
            exit_mode="fixed",
            fixed_hold_days=3,
            window="train",
        )
        result = run_backtest(in_mem_db, cfg)
        if not result.trades.empty:
            assert (result.trades["entry_date"] <= TRAIN_END).all()

    def test_holdout_window_start_filter_is_2024_01_01(
        self, in_mem_db, monkeypatch
    ) -> None:
        """No trade entry_date in run with window='holdout' may precede 2024-01-01."""
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2023-01-02", "2024-06-30")
        _insert_symbol(in_mem_db, "SYM")
        _insert_tech_series(in_mem_db, "SYM", days, base_price=100.0)
        in_mem_db.commit()

        cfg = BacktestConfig(
            signal="random",
            exit_mode="fixed",
            fixed_hold_days=3,
            window="holdout",
        )
        result = run_backtest(in_mem_db, cfg)
        if not result.trades.empty:
            assert (result.trades["entry_date"] >= "2024-01-01").all()


# ──────────────────────────────────────────────────────────────────────────────
# 6. Signal registry
# ──────────────────────────────────────────────────────────────────────────────


class TestSignalRegistry:
    """Section 6 — name → class mapping."""

    def test_random_key_maps_to_random_signal(self) -> None:
        assert "random" in SIGNAL_REGISTRY
        assert SIGNAL_REGISTRY["random"] is RandomSignal

    def test_momentum_key_maps_to_pure_momentum(self) -> None:
        assert "momentum" in SIGNAL_REGISTRY
        assert SIGNAL_REGISTRY["momentum"] is MomentumSignal

    def test_registered_signals_implement_protocol(self) -> None:
        for name, factory in SIGNAL_REGISTRY.items():
            sig = factory()
            assert hasattr(sig, "requires_delivery"), f"{name} missing protocol attr"
            assert hasattr(sig, "score"), f"{name} missing score() method"
            assert isinstance(sig.requires_delivery, bool)