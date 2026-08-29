"""
Tests for the price-adjusted historical market cap in the Wyckoff scanner.

Covers `_get_historical_mcap(df, symbol, as_on_date)` (the leak-free
current_mcap * (price_t / current_price) approximation) and its integration
into `_event_quality` for Spring events (via extra["historical_mcap"]).
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
# _event_quality integration — Spring mcap factor (via extra["historical_mcap"])
# ---------------------------------------------------------------------------


def _spring_quality(s, hist_mcap=8.1e9):
    """Spring `_event_quality` with del_pct=75 (→50) and recovery_pct=5 (→50),
    plus the given historical mcap factor."""
    return s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=75.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "historical_mcap": hist_mcap},
    )


def test_spring_quality_includes_historical_mcap_factor():
    """With `mcap_weight=20`: base (del/75*50 + rec/5*50 = 100) + 20*ln(1e9)
    — and the mcap-weight contribution is clamped at 100."""
    s = _scanner_with()
    # Base is already 100 → clamped.
    assert _spring_quality(s, hist_mcap=8.1e9) == pytest.approx(100.0)

    # Non-clamping base proves the factor is added: del=30 → 20, rec=5 → 50,
    # so base = 70 and then + 20*ln(1e9) → clamped to 100.
    s2 = _scanner_with()
    q2 = s2._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "historical_mcap": 1.0e9},
    )
    assert q2 == pytest.approx(min(70.0 + 20.0 * math.log(1.0e9), 100.0))


def test_spring_quality_mcap_weight_zero_matches_base():
    """`mcap_weight=0` disables the factor → quality equals the plain base."""
    s = _scanner_with(mcap_weight=0)
    q = _spring_quality(s, hist_mcap=8.1e9)
    assert q == pytest.approx(100.0)
    s2 = _scanner_with(mcap_weight=0)
    q2 = s2._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0, "historical_mcap": 1.0e9},
    )
    assert q2 == pytest.approx(70.0)  # base only, no factor


def test_spring_quality_mcap_missing_falls_back_to_base():
    """mcap unresolvable (no historical_mcap in extra) → base score only."""
    s = _scanner_with()
    q = s._event_quality(
        "Spring",
        vol_ratio=1.0,
        del_pct=30.0,
        avg_del_pct=30.0,
        extra={"recovery_pct": 5.0},
    )
    assert q == pytest.approx(70.0)


def test_sc_sos_quality_ignores_historical_mcap_extra():
    """SC/SOS quality ignores the historical_mcap key — the factor is Spring-
    only (regression guard for the scoring-scope boundary)."""
    s = _scanner_with()
    sc_plain = s._event_quality("SC", vol_ratio=2.0, del_pct=40.0, avg_del_pct=30.0)
    sc_extra = s._event_quality(
        "SC",
        vol_ratio=2.0,
        del_pct=40.0,
        avg_del_pct=30.0,
        extra={"historical_mcap": 1.0e9},
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
        extra={"close_position": 0.8, "historical_mcap": 1.0e9},
    )
    assert sos_extra == sos_plain
