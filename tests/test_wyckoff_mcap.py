"""
Tests for the price-adjusted historical market cap in the Wyckoff scanner.

Covers `_get_historical_mcap(df, symbol, as_on_date)` (the leak-free
current_mcap * (price_t / current_price) approximation), `_normalise_mcap`
(the 0-1 log-mcap normalisation against the scan universe), and their
integration into `_event_quality` for Spring events (via extra["norm_mcap"]).
No network access and no real-DB access — price frames are synthetic, and the
lazy `fundamentals` lookup (when used) runs on a temp DB.
"""

import math
import os
import sqlite3

import pandas as pd
import pytest

import myra_app.strategies.wyckoff_automaton as wy_mod
from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton


def _bulk_frame(closes, start="2025-01-01"):
    """Synthetic per-symbol df: `date` (datetime, ascending) + `close`.

    Mirrors what `_detect_events` receives in both the bulk and DB paths
    (scan() sorts ascending and converts `date` to datetime).
    """
    dates = pd.date_range(start, periods=len(closes))
    return pd.DataFrame({"date": dates, "close": [float(c) for c in closes]})


def _scanner_with(**kwargs):
    return WyckoffAutomaton(**kwargs)


# ---------------------------------------------------------------------------
# _get_historical_mcap — ratio math
# ---------------------------------------------------------------------------


def test_historical_mcap_ratio_math():
    """snapshot mcap 9e9, df closes ending at 100, event-date price 90 →
    9e9 * (90/100) = 8.1e9."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 9.0e9}
    df = _bulk_frame([90.0, 100.0])  # event on row 0
    hmcap = s._get_historical_mcap(df, "AAA", "2025-01-01")
    assert hmcap == pytest.approx(8.1e9)


def test_historical_mcap_uses_latest_close_as_current_price():
    """Current price is the LAST close in the ascending frame, not the max."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 9.0e9}
    df = _bulk_frame([120.0, 60.0])  # latest = 60
    hmcap = s._get_historical_mcap(df, "AAA", "2025-01-01")
    assert hmcap == pytest.approx(9.0e9 * (120.0 / 60.0))  # 18e9


def test_historical_mcap_missing_event_date_price_returns_none():
    """No row for the event date → None (no fallback price)."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 9.0e9}
    df = _bulk_frame([90.0, 100.0])
    assert s._get_historical_mcap(df, "AAA", "2025-02-01") is None


def test_historical_mcap_missing_current_mcap_returns_none():
    """Symbol absent from the snapshot map (and memoized-missing) → None."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 9.0e9}
    df = _bulk_frame([90.0, 100.0])
    assert s._get_historical_mcap(df, "BBB", "2025-01-01") is None
    assert s._current_mcap_map.get("BBB") is None


def test_historical_mcap_empty_df_returns_none():
    """Empty / absent price series → None."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 9.0e9}
    assert s._get_historical_mcap(None, "AAA", "2025-01-01") is None
    assert s._get_historical_mcap(pd.DataFrame(), "AAA", "2025-01-01") is None


def test_historical_mcap_zero_current_price_returns_none():
    """Latest close <= 0 (or NaN) → None (div-by-zero guard)."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 9.0e9}
    assert (
        s._get_historical_mcap(_bulk_frame([100.0, 0.0]), "AAA", "2025-01-01") is None
    )
    assert (
        s._get_historical_mcap(_bulk_frame([100.0, float("nan")]), "AAA", "2025-01-01")
        is None
    )


def test_historical_mcap_zero_snapshot_mcap_returns_none():
    """Current mcap <= 0 → None."""
    s = _scanner_with()
    s._current_mcap_map = {"AAA": 0.0}
    assert (
        s._get_historical_mcap(_bulk_frame([90.0, 100.0]), "AAA", "2025-01-01") is None
    )


def test_historical_mcap_lazy_fundamentals_fallback(tmp_path, monkeypatch):
    """When the symbol is absent from the seeded map, `_get_historical_mcap`
    lazily queries `fundamentals` (memoized into the map) — the direct-call /
    backtest / calibration-tool path."""
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    db = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE fundamentals (symbol TEXT, market_cap REAL, date TEXT)"
        )
        conn.execute("INSERT INTO fundamentals VALUES ('AAA', 9.0e9, '2026-08-24')")
        conn.commit()
    finally:
        conn.close()

    s = _scanner_with()
    df = _bulk_frame([90.0, 100.0])
    assert s._get_historical_mcap(df, "AAA", "2025-01-01") == pytest.approx(8.1e9)
    # Memoized — the map now has the value.
    assert s._current_mcap_map.get("AAA") == pytest.approx(9.0e9)


def test_historical_mcap_lazy_fallback_db_missing_returns_none(tmp_path, monkeypatch):
    """No valuation DB at all → None (no crash)."""
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    s = _scanner_with()
    df = _bulk_frame([90.0, 100.0])
    assert s._get_historical_mcap(df, "AAA", "2025-01-01") is None


# ---------------------------------------------------------------------------
# _normalise_mcap — bounded 0-1 factor + edge cases
# ---------------------------------------------------------------------------


def test_normalise_mcap_bounded_zero_to_one():
    """`_normalise_mcap` maps log-mcap into [0, 1] using the scan range."""
    s = _scanner_with()
    s._mcap_log_range = (math.log(1.0e8), math.log(1.0e10))
    assert s._normalise_mcap(1.0e10) == pytest.approx(1.0)  # at max
    assert s._normalise_mcap(1.0e8) == pytest.approx(0.0)  # at min
    assert s._normalise_mcap(1.0e9) == pytest.approx(0.5)  # mid-range
    # Out-of-range values clamp to the [0, 1] bounds.
    assert s._normalise_mcap(1.0e12) == pytest.approx(1.0)
    assert s._normalise_mcap(1.0e6) == pytest.approx(0.0)


def test_normalise_mcap_degenerate_range_returns_zero():
    """All-mcaps-identical range (hi == lo) → 0.0, so the factor does nothing
    but is NOT None (robust to the degenerate edge case)."""
    s = _scanner_with()
    s._mcap_log_range = (math.log(5.0e9), math.log(5.0e9))
    assert s._normalise_mcap(5.0e9) == 0.0


def test_normalise_mcap_missing_inputs_return_none():
    """No scan range (direct caller) / missing or non-positive mcap → None,
    which the score treats as plain base."""
    s = _scanner_with()
    assert s._mcap_log_range is None
    assert s._normalise_mcap(1.0e9) is None  # no range
    s._mcap_log_range = (math.log(1.0e8), math.log(1.0e10))
    assert s._normalise_mcap(None) is None
    assert s._normalise_mcap(0.0) is None
    assert s._normalise_mcap(-5.0) is None


# ---------------------------------------------------------------------------
# _event_quality integration — Spring mcap factor (via extra["norm_mcap"])
# ---------------------------------------------------------------------------


def _spring_quality(s, norm_mcap=1.0):
    """Spring `_event_quality` with del_pct=75 (→50) and recovery_pct=5 (→50),
    plus the given normalised (0-1) mcap factor."""
    return s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=75.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "norm_mcap": norm_mcap},
    )


def test_spring_quality_includes_norm_mcap_factor():
    """base (del/75*50 + rec/5*50) + mcap_weight * norm_mcap. With del=30
    (→20) and rec=5 (→50): base 70; norm 1.0 → 90, norm 0.5 → 80."""
    s = _scanner_with()
    q = s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "norm_mcap": 1.0},
    )
    assert q == pytest.approx(90.0)
    q_half = s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "norm_mcap": 0.5},
    )
    assert q_half == pytest.approx(80.0)
    # Full base (del 75 → 50, rec 5 → 50) + 20*1 = 100 → still clamps at 100.
    assert _spring_quality(s, norm_mcap=1.0) == pytest.approx(100.0)


def test_spring_quality_mcap_weight_zero_matches_base():
    """`mcap_weight=0` disables the factor → quality equals the plain base."""
    s = _scanner_with(mcap_weight=0)
    q = s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "norm_mcap": 1.0},
    )
    assert q == pytest.approx(70.0)  # base only, no factor


def test_spring_quality_mcap_missing_falls_back_to_base():
    """mcap factor unresolvable (no norm_mcap in extra) → base score only."""
    s = _scanner_with()
    q = s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0},
    )
    assert q == pytest.approx(70.0)


def test_sc_sos_quality_ignores_norm_mcap_extra():
    """SC/SOS quality ignores the norm_mcap key — the factor is Spring-only
    (regression guard for the scoring-scope boundary)."""
    s = _scanner_with()
    sc_plain = s._event_quality("SC", vol_ratio=2.0, del_pct=40.0, avg_del_pct=30.0)
    sc_extra = s._event_quality(
        "SC",
        vol_ratio=2.0,
        del_pct=40.0,
        avg_del_pct=30.0,
        extra={"norm_mcap": 1.0},
    )
    assert sc_extra == sc_plain
    sos_plain = s._event_quality(
        "SOS",
        vol_ratio=2.0,
        del_pct=50.0,
        avg_del_pct=30.0,
        extra={"close_position": 0.8},
    )
    sos_extra = s._event_quality(
        "SOS",
        vol_ratio=2.0,
        del_pct=50.0,
        avg_del_pct=30.0,
        extra={"close_position": 0.8, "norm_mcap": 1.0},
    )
    assert sos_extra == sos_plain
