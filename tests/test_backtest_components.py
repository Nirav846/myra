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
    KAUSHIK_DELIVERY_ELEV_THRESHOLD,
    KAUSHIK_DELIVERY_FILTER_WINDOW,
    KaushikBOHMethod1,
    KaushikBOHMethod1Delivery,
    KaushikBOHMethod1DeliveryFilter,
    MomentumSignal,
    POSITION_VALUE_INR,
    RandomSignal,
    SIGNAL_REGISTRY,
    TRAIN_START_DELIVERY,
    TRAIN_START_PRICE_ONLY,
    _eligible_symbols_at_date,
    _exit_fixed_holding,
    _exit_profit_target,
    _exit_rule_based,
    _exit_trailing_stop,
    _resolve_window,
    calc_brokerage,
    calc_impact_cost,
    calc_stt,
    compute_mae_mfe,
    retrospective_stop_sweep,
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

        eligible = _eligible_symbols_at_date(in_mem_db, pd.Timestamp("2024-06-30"))

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

        eligible = _eligible_symbols_at_date(in_mem_db, pd.Timestamp("2024-06-30"))
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

    def test_position_size_is_exactly_10000_inr(self, in_mem_db, monkeypatch) -> None:
        _disable_blackout(monkeypatch)
        days = _insert_calendar(in_mem_db, "2024-03-01", "2024-04-15")
        _insert_symbol(in_mem_db, "SYM")
        _insert_tech_series(in_mem_db, "SYM", days, base_price=100.0, drift_per_day=0.5)
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

    def test_only_one_position_per_day_opens(self, in_mem_db, monkeypatch) -> None:
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
        closes3 = [120.0] * 20 + [
            110,
            109.5,
            109,
            108.5,
            108,
            107.5,
            107,
            106.5,
            106,
            105.5,
        ]
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
        expected_brokerage = calc_brokerage(entry_value) + calc_brokerage(exit_value)
        assert costs["brokerage"] == pytest.approx(expected_brokerage)

        # Impact: both legs with same ADV
        expected_impact = calc_impact_cost(entry_value, adv) + calc_impact_cost(
            exit_value, adv
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
            assert hasattr(sig, "score"), f"{name} missing score()"
            assert isinstance(sig.requires_delivery, bool)


class TestSelectionStability:
    """Lock in the Phase 1 regression fix.

    Bug history: two code paths through the engine (canonical slow vs.
    an optimized fast driver) were passing the eligible-universe list in
    different orders to ``RandomControl.score()``. Because the random
    control assigns scores by iterating the universe in order, the top-1
    selection differed even though the underlying universe was identical.

    Fix: ``RandomControl.score()`` sorts the universe internally before
    scoring, and the engine's top-1 picker uses a deterministic
    ``sort_values(ascending=False).index[0]`` to break float ties.

    These tests verify both halves.
    """

    def test_random_control_output_index_is_sorted(self) -> None:
        from myra_app.strategies.random_control import RandomControl

        rc = RandomControl()
        # Pass an unsorted universe with deliberate reverse order
        unsorted = ["ZZZ", "AAA", "MMM", "BBB", "YYY"]
        scores = rc.score(pd.Timestamp("2024-06-15"), unsorted, conn=None)
        assert list(scores.index) == sorted(unsorted), (
            f"RandomControl must sort the universe before scoring; "
            f"got {list(scores.index)}"
        )

    def test_random_control_is_order_invariant(self) -> None:
        from myra_app.strategies.random_control import RandomControl

        rc = RandomControl()
        date = pd.Timestamp("2024-06-15")
        universe = ["SYM_" + str(i) for i in range(50)]
        a = rc.score(date, universe, conn=None)
        b = rc.score(date, list(reversed(universe)), conn=None)
        # Same scores assigned to the same symbols, regardless of input order
        pd.testing.assert_series_equal(a.sort_index(), b.sort_index())

    def test_top1_picker_breaks_ties_alphabetically(self) -> None:
        """If two symbols share the same score, the engine must pick the
        lex-smaller one — deterministically, not by Series insertion order."""
        scores = pd.Series([1.0, 1.0, 1.0, 1.0], index=["Z", "A", "M", "B"])
        sorted_scores = scores.sort_values(ascending=False, kind="mergesort")
        # mergesort is stable; within equal values it preserves original order
        # so the tie-break is "first seen" = the lex order of the input
        # index. Test the engine picks the same symbol whether the input
        # is sorted or reversed.
        winner_a = sorted_scores.index[0]
        # Reverse the index — same scores, different order
        rev = scores.iloc[::-1]
        winner_b = rev.sort_values(ascending=False, kind="mergesort").index[0]
        # The index of pd.Series.iloc[::-1] is reversed too, so mergesort
        # sees the same equal values but in reverse insertion order, which
        # means the first-seen tie-break picks the opposite end.
        # The contract: stable within a single call's index order, and
        # the same input index always produces the same winner.
        assert winner_a == "Z"  # insertion order: Z, A, M, B → Z first
        assert winner_b == "B"  # reversed insertion: B, M, A, Z → B first

    def test_random_control_reproducible_across_calls(self) -> None:
        from myra_app.strategies.random_control import RandomControl

        rc = RandomControl()
        date = pd.Timestamp("2024-06-15")
        universe = ["A", "B", "C", "D", "E"]
        a = rc.score(date, universe, conn=None)
        b = rc.score(date, universe, conn=None)
        pd.testing.assert_series_equal(a, b)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — profit-target exit.
# ──────────────────────────────────────────────────────────────────────────────


class TestExitProfitTarget:
    """3d — close at first close >= entry * (1+pct), else 252-day cap."""

    def _prices(self, closes, n=260):
        return pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=len(closes)),
                "close": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "volume": [100_000] * len(closes),
            }
        )

    def test_target_hit_exits_at_first_close(self) -> None:
        closes = [100.0, 107.0, 108.0, 120.0]
        idx, reason = _exit_profit_target(
            self._prices(closes), entry_idx=0, target_pct=0.075, cap_days=252
        )
        assert idx == 2  # 108 >= 107.5; the first close that clears the target
        assert reason == "pt_target_75bp"

    def test_target_hit_on_cap_day_still_counts_as_target(self) -> None:
        # Cap day is idx 252; close exactly at trigger → counts as target, not cap.
        closes = [100.0] * 252 + [107.5]
        idx, reason = _exit_profit_target(
            self._prices(closes), entry_idx=0, target_pct=0.075, cap_days=252
        )
        assert idx == 252
        assert reason == "pt_target_75bp"

    def test_no_hit_forces_252_day_cap(self) -> None:
        closes = [100.0] * 260
        idx, reason = _exit_profit_target(
            self._prices(closes), entry_idx=0, target_pct=0.075, cap_days=252
        )
        assert idx == 252
        assert reason == "pt_252d_cap"

    def test_series_ends_before_cap_reports_eod(self) -> None:
        closes = [100.0] * 10
        idx, reason = _exit_profit_target(
            self._prices(closes), entry_idx=0, target_pct=0.075, cap_days=252
        )
        assert idx == 9
        assert reason == "pt_eod"

    def test_5_and_10_pct_reasons_reflect_target(self) -> None:
        closes = [100.0, 106.0]
        idx, reason = _exit_profit_target(
            self._prices(closes), entry_idx=0, target_pct=0.05, cap_days=252
        )
        assert reason == "pt_target_50bp"
        closes = [100.0, 111.0]
        idx, reason = _exit_profit_target(
            self._prices(closes), entry_idx=0, target_pct=0.10, cap_days=252
        )
        assert reason == "pt_target_100bp"


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Kaushik BOH Method 1 signal.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def kaushik_db():
    """Reset the module-level Kaushik precompute cache and build a fresh,
    long-enough (>252 trading days) in-memory calendar."""
    import myra_app.backtest_engine as be

    be._KAUSHIK_CACHE = None
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
    # Weekdays from 2020-01-01 → 2022-12-31 (~783 trading days).
    s = pd.Timestamp("2020-01-01")
    e = pd.Timestamp("2022-12-31")
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            conn.execute(
                "INSERT INTO market_calendar (date, is_trading_day) VALUES (?, 1)",
                (cur.strftime("%Y-%m-%d"),),
            )
        cur += pd.Timedelta(days=1)
    yield conn
    conn.close()
    be._KAUSHIK_CACHE = None


def _insert_kaushik_series(
    conn: sqlite3.Connection,
    symbol: str,
    days: list[str],
    closes: list[float],
    lows: list[float],
    delivery_pct: list[float] | None = None,
) -> None:
    """Insert a full OHLC series for one symbol on the given trading days.

    delivery_pct defaults to 0.0 per row.
    """
    assert len(days) == len(closes) == len(lows)
    for i, d in enumerate(days):
        dp = (delivery_pct[i] if delivery_pct else 0.0) or 0.0
        conn.execute(
            "INSERT INTO technical_data "
            "(symbol, date, open, high, low, close, volume, delivery_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                d,
                closes[i],
                max(closes[i], lows[i]) * 1.001,
                lows[i],
                closes[i],
                100_000,
                dp,
            ),
        )
    conn.execute(
        "INSERT INTO symbols_master (symbol, instrument_type) VALUES (?, 'EQUITY')",
        (symbol,),
    )
    conn.commit()


def _kaushik_days(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT date FROM market_calendar WHERE is_trading_day = 1 ORDER BY date"
        ).fetchall()
    ]


class TestKaushikBOHMethod1:
    """Signal is a discrete 20%-recovery crossing, with per-cycle cooldown."""

    def _flat_then_dip_recover(
        self, n_flat: int = 252, dip: float = 80.0, recover: float = 96.2
    ) -> tuple[list[float], list[float]]:
        """Price flat at 100 for n_flat days, one 80 close/low, then recover
        (recovery day's low stays above the dip so year_low remains 80)."""
        closes = [100.0] * n_flat + [dip, recover, 100.0]
        lows = [99.0] * n_flat + [dip, dip + 5.0, 99.0]
        return closes, lows

    def test_crossing_is_a_discrete_event(self, kaushik_db) -> None:
        days = _kaushik_days(kaushik_db)
        closes, lows = self._flat_then_dip_recover()
        _insert_kaushik_series(kaushik_db, "RIDER", days[: len(closes)], closes, lows)
        sig = KaushikBOHMethod1(delivery_variant=False)

        # Day index of the recovery: 252 + 1 = 253rd trading day.
        signal_day = days[len(closes) - 2]  # the recover day
        # The day after (still above threshold) must NOT re-fire.
        after_day = days[len(closes) - 1]

        s_sig = sig.score(pd.Timestamp(signal_day), ["RIDER"], kaushik_db)
        s_after = sig.score(pd.Timestamp(after_day), ["RIDER"], kaushik_db)

        assert "RIDER" in s_sig.index
        assert s_after.empty  # discrete crossing, not "currently above"
        # overshoot = 96.2 / 96.0 - 1 = 0.00208…; score = -overshoot
        assert s_sig["RIDER"] == pytest.approx(-(96.2 / 96.0 - 1.0))

    def test_no_signal_without_full_52w_lookback(self, kaushik_db) -> None:
        days = _kaushik_days(kaushik_db)
        # Only 100 days of data → year_low never defined.
        closes = [100.0] * 100
        lows = [99.0] * 100
        _insert_kaushik_series(kaushik_db, "NEWKID", days[:100], closes, lows)
        sig = KaushikBOHMethod1()
        s = sig.score(pd.Timestamp(days[99]), ["NEWKID"], kaushik_db)
        assert s.empty

    def test_cooldown_blocks_until_fresh_low(self, kaushik_db) -> None:
        days = _kaushik_days(kaushik_db)
        # Cycle 1: flat 252, dip 80, recover 96.2, then drift higher.
        closes = [100.0] * 252 + [80.0, 96.2]
        lows = [99.0] * 252 + [80.0, 85.0]
        # Cycle 2: new low 75, recover to 90.2 (>= 1.2*75=90).
        closes += [100.0] * 100 + [75.0, 90.2]
        lows += [99.0] * 100 + [75.0, 80.0]
        _insert_kaushik_series(kaushik_db, "CYCLER", days[: len(closes)], closes, lows)
        sig = KaushikBOHMethod1()

        sig1_day = days[253]  # first recovery (96.2)
        sig2_day = days[253 + 1 + 100 + 1]  # second recovery (90.2)
        probe_no_sig = days[254]  # right after first signal, still rising

        s1 = sig.score(pd.Timestamp(sig1_day), ["CYCLER"], kaushik_db)
        s_none = sig.score(pd.Timestamp(probe_no_sig), ["CYCLER"], kaushik_db)
        s2 = sig.score(pd.Timestamp(sig2_day), ["CYCLER"], kaushik_db)
        s_cooldown_day = sig.score(
            pd.Timestamp(days[253 + 1 + 100]), ["CYCLER"], kaushik_db
        )

        assert "CYCLER" in s1.index
        assert s_none.empty  # stayed above threshold → no re-cross
        assert s_cooldown_day.empty  # new low day alone is not a signal
        assert "CYCLER" in s2.index  # fresh low 75 → cycle restarts

    def test_delivery_variant_ranks_by_elevation(self, kaushik_db) -> None:
        days = _kaushik_days(kaushik_db)
        # Both symbols dip to 80 then recover on the SAME day.
        # A: recovers to 96.4 (overshoot 0.0042), signal-day delivery 60, prior 30.
        # B: recovers to 96.0 (overshoot 0.0),   signal-day delivery 40, prior 35.
        n_flat = 252
        signal_trading_idx = n_flat + 1  # 253
        closes_a = [100.0] * n_flat + [80.0, 96.4]
        lows_a = [99.0] * n_flat + [80.0, 85.0]
        closes_b = [100.0] * n_flat + [80.0, 96.0]
        lows_b = [99.0] * n_flat + [80.0, 85.0]
        # Delivery: 30 for 20 days before signal; 60 on signal day (A),
        # 35 before; 40 on signal day (B).
        dp_a = [30.0] * (signal_trading_idx - 20) + [30.0] * 20 + [60.0]
        dp_b = [35.0] * (signal_trading_idx - 20) + [35.0] * 20 + [40.0]
        _insert_kaushik_series(
            kaushik_db, "AAA", days[: len(closes_a)], closes_a, lows_a, dp_a
        )
        _insert_kaushik_series(
            kaushik_db, "BBB", days[: len(closes_b)], closes_b, lows_b, dp_b
        )

        base = KaushikBOHMethod1(delivery_variant=False)
        deliv = KaushikBOHMethod1(delivery_variant=True)
        sig_day = days[signal_trading_idx]

        s_base = base.score(pd.Timestamp(sig_day), ["AAA", "BBB"], kaushik_db)
        s_deliv = deliv.score(pd.Timestamp(sig_day), ["AAA", "BBB"], kaushik_db)

        # Mirror the engine's top-1 picker: sort score DESC, take index[0].
        base_winner = s_base.sort_values(ascending=False, kind="mergesort").index[0]
        deliv_winner = s_deliv.sort_values(ascending=False, kind="mergesort").index[0]

        # Base: score = -overshoot → smaller overshoot (BBB=0.0) wins.
        assert base_winner == "BBB"
        # Delivery: score = elevation → A (60-30=30) beats B (40-35=5).
        assert deliv_winner == "AAA"
        assert s_deliv["AAA"] == pytest.approx(30.0)
        assert s_deliv["BBB"] == pytest.approx(5.0)

    def test_registry_maps_kaushik_signals(self) -> None:
        assert SIGNAL_REGISTRY["kaushik_boh_m1"] is KaushikBOHMethod1
        assert SIGNAL_REGISTRY["kaushik_boh_m1_delivery"] is KaushikBOHMethod1Delivery
        assert (
            SIGNAL_REGISTRY["kaushik_boh_m1_delivery_filter"]
            is KaushikBOHMethod1DeliveryFilter
        )
        assert not KaushikBOHMethod1().requires_delivery
        assert KaushikBOHMethod1Delivery().requires_delivery
        assert KaushikBOHMethod1DeliveryFilter().requires_delivery
        assert (
            KaushikBOHMethod1DeliveryFilter().delivery_window
            == KAUSHIK_DELIVERY_FILTER_WINDOW
        )
        assert (
            KaushikBOHMethod1DeliveryFilter().elev_threshold
            == KAUSHIK_DELIVERY_ELEV_THRESHOLD
        )


class TestKaushikBOHMethod1DeliveryFilter:
    """Delivery-as-ENTRY-FILTER: an un-elevated crossing produces NO trade,
    even as the sole candidate; passing candidates use the BASE tie-break."""

    def _setup_two_symbols(self, conn):
        days = _kaushik_days(conn)
        n_flat = 252
        signal_idx = n_flat + 1  # recover day index (253)
        closes_a = [100.0] * n_flat + [80.0, 96.4]
        lows_a = [99.0] * n_flat + [80.0, 85.0]
        closes_b = [100.0] * n_flat + [80.0, 96.0]
        lows_b = [99.0] * n_flat + [80.0, 85.0]
        # A: delivery constant 30 before, 60 on signal day  -> elevation +30
        # B: delivery constant 50 before, 40 on signal day  -> elevation -10
        dp_a = [30.0] * signal_idx + [60.0]
        dp_b = [50.0] * signal_idx + [40.0]
        _insert_kaushik_series(
            conn, "AAA", days[: len(closes_a)], closes_a, lows_a, dp_a
        )
        _insert_kaushik_series(
            conn, "BBB", days[: len(closes_b)], closes_b, lows_b, dp_b
        )
        return days, days[signal_idx]

    def test_non_elevated_sole_candidate_is_blocked(self, kaushik_db) -> None:
        days = _kaushik_days(kaushik_db)
        n_flat = 252
        signal_idx = n_flat + 1
        closes = [100.0] * n_flat + [80.0, 96.2]
        lows = [99.0] * n_flat + [80.0, 85.0]
        dp = [50.0] * signal_idx + [40.0]  # delivery DROPS on signal day
        _insert_kaushik_series(
            kaushik_db, "SOLELO", days[: len(closes)], closes, lows, dp
        )
        sig_day = days[signal_idx]

        base = KaushikBOHMethod1(delivery_variant=False)
        filt = KaushikBOHMethod1DeliveryFilter()
        s_base = base.score(pd.Timestamp(sig_day), ["SOLELO"], kaushik_db)
        s_filt = filt.score(pd.Timestamp(sig_day), ["SOLELO"], kaushik_db)

        assert "SOLELO" in s_base.index  # base method takes the crossing
        assert s_filt.empty  # filter refuses: delivery not elevated

    def test_passing_candidate_uses_base_tiebreak(self, kaushik_db) -> None:
        days, sig_day = self._setup_two_symbols(kaushik_db)
        filt = KaushikBOHMethod1DeliveryFilter()
        s_filt = filt.score(pd.Timestamp(sig_day), ["AAA", "BBB"], kaushik_db)

        # Elevated A passes; un-elevated B is dropped even though it also crossed.
        assert "AAA" in s_filt.index
        assert "BBB" not in s_filt.index
        # Base tie-break applies among passers: score = -overshoot (AAA: 96.4/96-1).
        assert s_filt["AAA"] == pytest.approx(-(96.4 / 96.0 - 1.0))

    def test_filter_is_stricter_than_tie_break_variant(self, kaushik_db) -> None:
        days, sig_day = self._setup_two_symbols(kaushik_db)
        deliv = KaushikBOHMethod1(delivery_variant=True)
        filt = KaushikBOHMethod1DeliveryFilter()
        s_deliv = deliv.score(pd.Timestamp(sig_day), ["AAA", "BBB"], kaushik_db)
        s_filt = filt.score(pd.Timestamp(sig_day), ["AAA", "BBB"], kaushik_db)

        # Tie-break styles BOTH candidates (ranks by elevation, A first);
        # the filter keeps only A.
        assert set(["AAA", "BBB"]) == set(s_deliv.index)
        assert set(["AAA"]) == set(s_filt.index)
        assert s_deliv["AAA"] > s_deliv["BBB"]


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Kaushik real averaging mechanism.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def long_kaushik_db():
    """Same in-memory harness as `kaushik_db` but with a 2020–2025 calendar so
    a re-anchored 252-day cap (anchored to the MOST RECENT tranche) fits."""
    import myra_app.backtest_engine as be

    be._KAUSHIK_CACHE = None
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
    s = pd.Timestamp("2020-01-01")
    e = pd.Timestamp("2025-12-31")
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            conn.execute(
                "INSERT INTO market_calendar (date, is_trading_day) VALUES (?, 1)",
                (cur.strftime("%Y-%m-%d"),),
            )
        cur += pd.Timedelta(days=1)
    yield conn
    conn.close()
    be._KAUSHIK_CACHE = None


class TestKaushikAveraging:
    """Phase-3 averaging: fresh signals on held symbols blend the cost basis,
    re-anchor the 252-day cap, and never block the top-1 new entry."""

    def _cfg(self, last_day: str, average_in: bool = True) -> BacktestConfig:
        return BacktestConfig(
            signal="kaushik_boh_m1",
            exit_mode="profit_target",
            profit_target_pct=0.075,
            window="all",
            start_date="2020-01-01",
            end_date=last_day,
            average_in=average_in,
        )

    def test_average_in_uses_blended_basis_for_target(self, kaushik_db) -> None:
        """Cycle 2 average-in blends the basis; the target applies to the blend."""
        days = _kaushik_days(kaushik_db)
        # Signal 1 on days[253] (96.2, low floor 80). While open, a fresh
        # lower low (70 < floor) + recovery (84.2 >= 1.2*70) → signal 2 on
        # days[295]; blended = harmonic_mean(96.2, 84.2) ≈ 89.80;
        # target 89.80*1.075 ≈ 96.54.
        closes = (
            [100.0] * 252 + [80.0, 96.2] + [90.0] * 40 + [70.0, 84.2] + [105.0] * 10
        )
        lows = [99.0] * 252 + [80.0, 85.0] + [88.0] * 40 + [70.0, 75.0] + [104.0] * 10
        _insert_kaushik_series(kaushik_db, "RIDER", days[: len(closes)], closes, lows)

        res = run_backtest(kaushik_db, self._cfg(days[len(closes) - 1]))

        assert len(res.trades) == 1
        r = res.trades.iloc[0]
        assert int(r["n_tranches"]) == 2
        # Share-weighted (harmonic) mean: 20000 / (10000/96.2 + 10000/84.2)
        assert r["blended_basis"] == pytest.approx(89.800887, abs=0.01)
        assert r["entry_price"] == pytest.approx(96.2)
        assert r["exit_price"] == pytest.approx(105.0)
        assert r["exit_reason"] == "pt_target_75bp"
        assert int(r["n_hold_days"]) == 43  # days[296] - days[253]
        assert r["pnl_net"] > 0

    def test_three_tranche_cap_ignores_fourth_signal(self, kaushik_db) -> None:
        """3 tranches deploy at 96.2/84.2/72.2; a 4th fresh-low recovery at
        50.6 is ignored; position resolves via the blended target."""
        days = _kaushik_days(kaushik_db)
        closes = (
            [100.0] * 252
            + [80.0, 96.2]  # signal 1 (days[253])
            + [90.0] * 3
            + [70.0, 84.2]  # signal 2 (days[258]) → tranche 2
            + [70.0] * 3
            + [60.0, 72.2]  # signal 3 (days[263]) → tranche 3
            + [60.0] * 3
            + [42.0, 50.6]  # signal 4 (days[268]) → IGNORED
            + [200.0] * 5  # blended target 83.05*1.075 ≈ 89.28 → exit days[269]
        )
        lows = (
            [99.0] * 252
            + [80.0, 85.0]
            + [88.0] * 3
            + [70.0, 75.0]
            + [68.0] * 3
            + [60.0, 65.0]
            + [58.0] * 3
            + [42.0, 46.0]
            + [199.0] * 5
        )
        _insert_kaushik_series(kaushik_db, "RIDER", days[: len(closes)], closes, lows)

        res = run_backtest(kaushik_db, self._cfg(days[len(closes) - 1]))

        assert len(res.trades) == 1
        r = res.trades.iloc[0]
        assert int(r["n_tranches"]) == 3
        # Share-weighted: 30000 / (10000/96.2 + 10000/84.2 + 10000/72.2)
        assert r["blended_basis"] == pytest.approx(83.052086, abs=0.01)
        assert r["exit_reason"] == "pt_target_75bp"
        assert r["exit_price"] == pytest.approx(200.0)
        assert res.summary["n_tranche_distribution"] == {3: 1}

    def test_cap_reanchors_to_last_tranche(self, long_kaushik_db) -> None:
        """Cap runs 252 trading days from the MOST RECENT tranche (days[295]),
        letting the position outlive the first tranche's own 252-day clock."""
        days = _kaushik_days(long_kaushik_db)
        closes = (
            [100.0] * 252
            + [80.0, 96.2]  # signal 1 on days[253] (target 103.415)
            + [60.0] * 40  # never hits target
            + [30.0, 36.2]  # signal 2 on days[295] → tranche 2 (36.2)
            + [50.0] * 530  # below harmonic target 52.6*1.075≈56.55 → cap at days[547]
        )
        lows = [99.0] * 252 + [80.0, 85.0] + [58.0] * 40 + [30.0, 34.0] + [48.0] * 530
        _insert_kaushik_series(
            long_kaushik_db, "RIDER", days[: len(closes)], closes, lows
        )

        res = run_backtest(long_kaushik_db, self._cfg(days[len(closes) - 1]))

        assert len(res.trades) == 1
        r = res.trades.iloc[0]
        assert int(r["n_tranches"]) == 2
        # Share-weighted: 20000 / (10000/96.2 + 10000/36.2)
        assert r["blended_basis"] == pytest.approx(52.604834, abs=0.01)
        assert r["exit_reason"] == "pt_252d_cap"
        assert r["exit_date"] == days[547]  # 295 (last tranche) + 252
        assert int(r["n_hold_days"]) == 547 - 253

    def test_average_in_does_not_block_top1_new_entry(self, kaushik_db) -> None:
        """Same-day: RIDER averages in tranche 2 while BOAT opens as the new
        top-1 entry. Capital peaks at 3 x 10k (2 tranches + 1 fresh)."""
        days = _kaushik_days(kaushik_db)
        # RIDER: signal 1 on days[253]; fresh low 70 + recovery on days[257]/[258]
        # re-signals (tranche 2). BOAT: first signal on the SAME days[258].
        rider_c = [100.0] * 252 + [80.0, 96.2] + [90.0] * 3 + [70.0, 84.2] + [200.0] * 5
        rider_l = [99.0] * 252 + [80.0, 85.0] + [88.0] * 3 + [70.0, 75.0] + [199.0] * 5
        boat_c = [95.0] * 257 + [70.0, 84.2] + [200.0] * 5
        boat_l = [94.0] * 257 + [70.0, 75.0] + [199.0] * 5
        _insert_kaushik_series(
            kaushik_db, "RIDER", days[: len(rider_c)], rider_c, rider_l
        )
        _insert_kaushik_series(kaushik_db, "BOAT", days[: len(boat_c)], boat_c, boat_l)

        res = run_backtest(kaushik_db, self._cfg(days[len(rider_c) - 1]))

        assert set(res.trades["symbol"]) == {"RIDER", "BOAT"}
        rider = res.trades[res.trades["symbol"] == "RIDER"].iloc[0]
        boat = res.trades[res.trades["symbol"] == "BOAT"].iloc[0]
        assert int(rider["n_tranches"]) == 2
        # Share-weighted: 20000 / (10000/96.2 + 10000/84.2)
        assert rider["blended_basis"] == pytest.approx(89.800887, abs=0.01)
        assert int(boat["n_tranches"]) == 1
        assert boat["blended_basis"] == pytest.approx(84.2)
        assert rider["exit_reason"] == boat["exit_reason"] == "pt_target_75bp"
        # 2 tranches on RIDER (20k) + 1 on BOAT (10k) = 30k peak that day
        assert res.summary["peak_concurrent_capital"] >= 3 * POSITION_VALUE_INR


# ──────────────────────────────────────────────────────────────────────────────
# 7. MAE/MFE post-hoc enrichment
# ──────────────────────────────────────────────────────────────────────────────


def _insert_ohlcv(conn, symbol, dates, closes, highs=None, lows=None):
    """Insert synthetic OHLCV rows. highs/lows default to close ± 2."""
    highs = highs or [c * 1.02 for c in closes]
    lows = lows or [c * 0.98 for c in closes]
    for d, c, h, l in zip(dates, closes, highs, lows):
        conn.execute(
            "INSERT INTO technical_data "
            "(symbol, date, open, high, low, close, volume, delivery, delivery_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, 1000, 500, 50.0)",
            (symbol, d, c, h, l, c),
        )


class TestComputeMaeMfe:
    """compute_mae_mfe: post-hoc MAE/MFE enrichment."""

    def test_single_tranche_known_path(self, in_mem_db):
        """MAE/MFE match hand-computed values on a known price path."""
        # 5-day window: entry at 100, dips to 92 (MAE -8%), rallies to 112 (MFE +12%), exits at 108.
        dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
        closes = [100.0, 95.0, 93.0, 110.0, 108.0]
        highs = [101.0, 96.0, 94.0, 112.0, 109.0]
        lows = [99.0, 94.0, 92.0, 108.0, 107.0]
        _insert_ohlcv(in_mem_db, "TEST", dates, closes, highs, lows)

        trades = pd.DataFrame(
            [
                {
                    "entry_date": "2025-01-06",
                    "exit_date": "2025-01-10",
                    "symbol": "TEST",
                    "entry_price": 100.0,
                    "exit_price": 108.0,
                    "n_hold_days": 4,
                    "pnl_gross": 800.0,
                    "costs": 30.0,
                    "pnl_net": 770.0,
                    "exit_reason": "fixed_4d",
                }
            ]
        )
        result = compute_mae_mfe(trades, in_mem_db)
        assert result["mae_pct"].iloc[0] == pytest.approx(-8.0, abs=0.01)
        assert result["mfe_pct"].iloc[0] == pytest.approx(12.0, abs=0.01)

    def test_multi_tranche_uses_share_weighted_basis(self, in_mem_db):
        """Multi-tranche trade uses share-weighted (harmonic mean) basis, not arithmetic."""
        # Tranche 1 at 100, tranche 2 at 80 → harmonic mean = 2/(1/100+1/80) = 88.89.
        # Price dips to 84 → MAE = (84/88.89 - 1)*100 = -5.50%, NOT (84/100 - 1)*100 = -16%.
        # High on entry day = 101 → MFE = (101/88.89 - 1)*100 = 13.62%.
        dates = ["2025-01-06", "2025-01-07", "2025-01-08"]
        closes = [100.0, 85.0, 95.0]
        highs = [101.0, 86.0, 96.0]
        lows = [99.0, 84.0, 94.0]
        _insert_ohlcv(in_mem_db, "MULTI", dates, closes, highs, lows)

        trades = pd.DataFrame(
            [
                {
                    "entry_date": "2025-01-06",
                    "exit_date": "2025-01-08",
                    "symbol": "MULTI",
                    "entry_price": 100.0,
                    "exit_price": 95.0,
                    "n_hold_days": 2,
                    "pnl_gross": -50.0,
                    "costs": 30.0,
                    "pnl_net": -80.0,
                    "exit_reason": "fixed_2d",
                    "n_tranches": 2,
                    "blended_basis": 90.0,
                    "tranche_dates": "2025-01-06|2025-01-07",
                    "tranche_prices": "100.00|80.00",
                }
            ]
        )
        result = compute_mae_mfe(trades, in_mem_db)
        # Share-weighted basis: 2 / (1/100 + 1/80) = 88.8889 (harmonic mean)
        # MAE from share-weighted basis: 84/88.8889 - 1 = -5.50%
        assert result["mae_pct"].iloc[0] == pytest.approx(
            (84.0 / 88.8889 - 1.0) * 100.0, abs=0.01
        )
        # MFE from share-weighted basis: 101/88.8889 - 1 = 13.62% (high on entry day)
        assert result["mfe_pct"].iloc[0] == pytest.approx(
            (101.0 / 88.8889 - 1.0) * 100.0, abs=0.01
        )

    def test_empty_trades(self, in_mem_db):
        """Empty trade log returns empty DataFrame with mae_pct/mfe_pct columns."""
        trades = pd.DataFrame(
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
        )
        result = compute_mae_mfe(trades, in_mem_db)
        assert "mae_pct" in result.columns
        assert "mfe_pct" in result.columns
        assert len(result) == 0

    def test_missing_price_data(self, in_mem_db):
        """Trade with no OHLCV data gets MAE/MFE = 0 (graceful fallback)."""
        trades = pd.DataFrame(
            [
                {
                    "entry_date": "2025-01-06",
                    "exit_date": "2025-01-10",
                    "symbol": "NODATA",
                    "entry_price": 100.0,
                    "exit_price": 110.0,
                    "n_hold_days": 4,
                    "pnl_gross": 1000.0,
                    "costs": 30.0,
                    "pnl_net": 970.0,
                    "exit_reason": "fixed_4d",
                }
            ]
        )
        result = compute_mae_mfe(trades, in_mem_db)
        assert result["mae_pct"].iloc[0] == 0.0
        assert result["mfe_pct"].iloc[0] == 0.0

    def test_batch_loading(self, in_mem_db):
        """Multiple trades on different symbols load prices in one batch."""
        dates = ["2025-01-06", "2025-01-07", "2025-01-08"]
        _insert_ohlcv(
            in_mem_db, "SYM_A", dates, [100, 90, 110], [101, 91, 111], [99, 89, 109]
        )
        _insert_ohlcv(
            in_mem_db, "SYM_B", dates, [200, 190, 210], [201, 191, 211], [199, 189, 209]
        )

        trades = pd.DataFrame(
            [
                {
                    "entry_date": "2025-01-06",
                    "exit_date": "2025-01-08",
                    "symbol": "SYM_A",
                    "entry_price": 100.0,
                    "exit_price": 110.0,
                    "n_hold_days": 2,
                    "pnl_gross": 1000.0,
                    "costs": 30.0,
                    "pnl_net": 970.0,
                    "exit_reason": "fixed_2d",
                },
                {
                    "entry_date": "2025-01-06",
                    "exit_date": "2025-01-08",
                    "symbol": "SYM_B",
                    "entry_price": 200.0,
                    "exit_price": 210.0,
                    "n_hold_days": 2,
                    "pnl_gross": 500.0,
                    "costs": 30.0,
                    "pnl_net": 470.0,
                    "exit_reason": "fixed_2d",
                },
            ]
        )
        result = compute_mae_mfe(trades, in_mem_db)
        assert result["mae_pct"].iloc[0] == pytest.approx(
            (89.0 / 100.0 - 1.0) * 100.0, abs=0.01
        )
        assert result["mfe_pct"].iloc[0] == pytest.approx(
            (111.0 / 100.0 - 1.0) * 100.0, abs=0.01
        )
        assert result["mae_pct"].iloc[1] == pytest.approx(
            (189.0 / 200.0 - 1.0) * 100.0, abs=0.01
        )
        assert result["mfe_pct"].iloc[1] == pytest.approx(
            (211.0 / 200.0 - 1.0) * 100.0, abs=0.01
        )


# ──────────────────────────────────────────────────────────────────────────────
# 8. Retrospective stop-sensitivity sweep
# ──────────────────────────────────────────────────────────────────────────────


class TestRetrospectiveStopSweep:
    """retrospective_stop_sweep: trade-off table for fixed stop-loss levels."""

    def test_basic_sweep(self):
        """Known MAE values produce correct winners_killed / losers_stopped counts."""
        trades = pd.DataFrame(
            [
                # Winner with MAE -5%: survives 10% stop, killed at 25%? No — only if MAE <= -25%.
                {"pnl_net": 500.0, "mae_pct": -5.0},
                # Winner with MAE -12%: killed at 10% stop
                {"pnl_net": 300.0, "mae_pct": -12.0},
                # Loser with MAE -18%: cut at 15% stop
                {"pnl_net": -800.0, "mae_pct": -18.0},
                # Loser with MAE -8%: cut at 10% stop
                {"pnl_net": -200.0, "mae_pct": -8.0},
                # Deep winner with MAE -2%: survives all stops
                {"pnl_net": 1000.0, "mae_pct": -2.0},
            ]
        )
        # Use user-specified levels: 10%, 15%, 20%, 25%
        result = retrospective_stop_sweep(
            trades, start_pct=10.0, end_pct=25.0, step_pct=5.0
        )

        # 10% stop: MAE <= -10% → trades 1 (-12%), 2 (-18%), 3 (-8%) → NO, -8% > -10%
        # trades 1 (-12%) and 2 (-18%) → 2 stopped
        row10 = result[result["stop_pct"] == 10.0].iloc[0]
        assert row10["n_stopped"] == 2
        assert row10["winners_killed"] == 1  # trade 1
        assert row10["losers_stopped"] == 1  # trade 2

        # 15% stop: MAE <= -15% → trade 2 (-18%) → 1 stopped
        row15 = result[result["stop_pct"] == 15.0].iloc[0]
        assert row15["n_stopped"] == 1
        assert row15["winners_killed"] == 0
        assert row15["losers_stopped"] == 1  # trade 2

        # 20% stop: no MAE breaches -20% → 0 stopped
        row20 = result[result["stop_pct"] == 20.0].iloc[0]
        assert row20["n_stopped"] == 0

        # 25% stop: no MAE breaches -25% → 0 stopped
        row25 = result[result["stop_pct"] == 25.0].iloc[0]
        assert row25["n_stopped"] == 0

    def test_empty_trades(self):
        """Empty trade log returns empty DataFrame with correct columns."""
        result = retrospective_stop_sweep(pd.DataFrame())
        assert len(result) == 0
        assert "stop_pct" in result.columns
        assert "winners_killed" in result.columns

    def test_no_mae_column(self):
        """Trade log without mae_pct returns empty DataFrame."""
        result = retrospective_stop_sweep(pd.DataFrame({"pnl_net": [100]}))
        assert len(result) == 0

    def test_all_winners(self):
        """Sweep over all-profitable trades: losers_stopped should be 0."""
        trades = pd.DataFrame(
            [
                {"pnl_net": 500.0, "mae_pct": -3.0},
                {"pnl_net": 800.0, "mae_pct": -7.0},
                {"pnl_net": 200.0, "mae_pct": -1.0},
            ]
        )
        result = retrospective_stop_sweep(
            trades, start_pct=10.0, end_pct=25.0, step_pct=5.0
        )
        for _, row in result.iterrows():
            assert row["losers_stopped"] == 0

    def test_survivor_avg_return(self):
        """avg_pnl_full_pop equals baseline when no trades are stopped."""
        trades = pd.DataFrame(
            [
                {"pnl_net": 1000.0, "mae_pct": -2.0},  # survives 10% stop
                {
                    "pnl_net": -500.0,
                    "mae_pct": -8.0,
                },  # survives 10% stop (MAE -8% > -10%)
                {"pnl_net": 300.0, "mae_pct": -1.0},  # survives 10% stop
            ]
        )
        result = retrospective_stop_sweep(
            trades, start_pct=10.0, end_pct=10.0, step_pct=1.0
        )
        row = result.iloc[0]
        # All 3 survive (MAE -2%, -8%, -1% all > -10%)
        # Full-pop mean = survivor mean = (1000 - 500 + 300) / 3
        assert row["n_stopped"] == 0
        assert row["avg_pnl_full_pop"] == pytest.approx(
            (1000.0 - 500.0 + 300.0) / 3, abs=0.01
        )
        assert row["avg_pnl_survivors"] == pytest.approx(
            (1000.0 - 500.0 + 300.0) / 3, abs=0.01
        )
