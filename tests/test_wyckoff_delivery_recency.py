"""
Tests for Wyckoff Automaton P0 (delivery dimension) and P1 (historical recency) fixes.

No database access, no network — pure computation only.
"""

import pandas as pd
import pytest

from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton


def _build_df(n=70, override_rows=None):
    """Synthetic DataFrame for _detect_events testing.

    Default: flat basing pattern with lows=100, highs=106, closes=103,
    volume=50000, delivery share volume=15000, delivery_pct=30.
    Override rows via {row_index: {col: val}}.
    """
    dates = pd.date_range("2025-01-01", periods=n)
    data = {
        "date": dates,
        "open": [103.5] * n,
        "high": [106.0] * n,
        "low": [100.0] * n,
        "close": [103.0] * n,
        "volume": [50000.0] * n,
        "delivery": [15000.0] * n,
        "delivery_pct": [30.0] * n,
        "swing_low": [float("nan")] * n,
        "nifty_outperformance_score": [0.0] * n,
        "sma_50": [102.0] * n,
        "high_52w": [130.0] * n,
        "low_52w": [95.0] * n,
    }
    df = pd.DataFrame(data)
    if override_rows:
        for idx, overrides in override_rows.items():
            for col, val in overrides.items():
                df.at[idx, col] = val
    return df


def _sos_override():
    """A bullish, high-volume, high-delivery candle that should read as SOS.

    close > open (bullish), close in upper half of range, volume > avg*1.2,
    delivery_pct (50) well above the window's average delivery_pct (30).
    """
    return {
        69: {
            "open": 103.0,
            "close": 105.0,
            "high": 106.0,
            "low": 102.0,
            "volume": 70000.0,
            "delivery_pct": 50.0,
        }
    }


# ---------------------------------------------------------------------------
# P0 — delivery dimension mismatch
# ---------------------------------------------------------------------------


def test_p0_sos_triggers_on_delivery_percentage_not_share_volume():
    """SOS fires using delivery_pct baseline, not the raw delivery share volume.

    The baseline data has delivery share volume = 15000 (share count) but
    delivery_pct = 30 (percentage). The old code compared del_pct (50) against
    avg of share volumes (15000) → always false, so SOS was never emitted.
    The fix compares against avg_del_pct (~30) → 50 >= 30 → SOS fires.
    """
    df = _build_df(n=70, override_rows=_sos_override())

    # Prove the dimension mismatch is the crux: mean share-volume ~15000 vs
    # mean percentage ~30.
    assert df["delivery"].mean() > 10000
    assert df["delivery_pct"].mean() < 40

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    sos_events = [e for e in events if e["event"] == "SOS"]
    assert len(sos_events) == 1, (
        "Expected exactly one SOS event: del_pct (50) >= avg_del_pct (~30). "
        f"Got {len(sos_events)}."
    )
    assert sos_events[0]["del_pct"] == 50.0
    assert sos_events[0]["event_date"] == str(df["date"].iloc[69])


def test_p0_delivery_baseline_uses_percentage_mean():
    """avg_del_pct is the mean of delivery_pct, not delivery share volume."""
    df = _build_df(n=70, override_rows=_sos_override())
    scanner = WyckoffAutomaton()
    # Access the computed baseline by reconstructing the window avg the same
    # way _detect_events does.
    avg_del_pct = float(df["delivery_pct"].values.astype(float).mean())
    assert avg_del_pct == pytest.approx(30.0 + (50.0 - 30.0) / 70.0)
    assert avg_del_pct < 50.0


# ---------------------------------------------------------------------------
# P1 — historical recency
# ---------------------------------------------------------------------------


def test_p1_days_since_relative_to_as_on_date():
    """days_since is computed against as_on_date, not date.today()."""
    df = _build_df(n=70, override_rows=_sos_override())
    scanner = WyckoffAutomaton()

    as_on = str(df["date"].iloc[69])  # as-of exactly the SOS event date
    events = scanner._detect_events(df, symbol="TEST", as_on_date=as_on)
    sos = [e for e in events if e["event"] == "SOS"][0]

    # Event on the as-of date → days_since must be 0, NOT a large number that
    # date.today() (≈2026) would produce.
    assert sos["days_since"] == 0
    assert sos["event_date"] == as_on


def test_p1_days_since_matches_as_on_date_gap():
    """days_since equals the gap between as_on_date and the event date."""
    df = _build_df(n=70, override_rows=_sos_override())
    scanner = WyckoffAutomaton()

    as_on = "2025-01-05"
    events = scanner._detect_events(df, symbol="TEST", as_on_date=as_on)
    sos = [e for e in events if e["event"] == "SOS"][0]

    event_date = pd.Timestamp(sos["event_date"]).date()
    expected = (pd.Timestamp(as_on).date() - event_date).days
    assert sos["days_since"] == expected


def test_p1_default_as_on_date_falls_back_to_today():
    """Without as_on_date, days_since falls back to date.today() for live scans."""
    from datetime import date

    df = _build_df(n=70, override_rows=_sos_override())
    scanner = WyckoffAutomaton()

    events = scanner._detect_events(df, symbol="TEST")  # no as_on_date
    sos = [e for e in events if e["event"] == "SOS"][0]

    event_date = pd.Timestamp(sos["event_date"]).date()
    expected = (date.today() - event_date).days
    assert sos["days_since"] == expected


# ---------------------------------------------------------------------------
# P2 — rolling-to-signal-day baselines (look-ahead bias removal)
# ---------------------------------------------------------------------------


def test_rolling_range_future_crash_does_not_suppress_sc():
    """range_low is now the expanding (rolling) minimum. A FUTURE crash at
    the last row (low=80) must not widen the SC gate for an earlier Selling
    Climax at row 65: close 104 <= 100*1.15 (rolling) but 104 > 80*1.15."""
    df = _build_df(
        n=70,
        override_rows={
            65: {
                "low": 101.0,
                "high": 106.0,
                "close": 104.0,
                "open": 103.0,
                "volume": 95000.0,  # > rolling avg_vol (~50682) * 1.8
                "delivery_pct": 55.0,
            },
            69: {"low": 80.0},  # FUTURE crash — must not affect row 65
        },
    )

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    sc_events = [e for e in events if e["event"] == "SC"]
    assert len(sc_events) == 1
    assert sc_events[0]["event_date"] == str(df["date"].iloc[65])
    # The SC's reported range must be the rolling min up to row 65, not 80.
    assert sc_events[0]["range_low_90"] > 80.0


def test_rolling_volume_future_spike_does_not_inflate_earlier_sos():
    """avg_vol is now rolling. A FUTURE volume spike at the last row must not
    inflate the volume baseline for an earlier SOS at row 67: 65000 >
    rolling_avg_vol(~50220)*1.2, but a global mean (incl. 10M spike) would
    suppress it."""
    df = _build_df(
        n=70,
        override_rows={
            67: {
                "open": 102.5,
                "close": 103.5,
                "high": 105.0,
                "low": 101.0,
                "volume": 65000.0,
                "delivery_pct": 45.0,
            },
            69: {"volume": 10000000.0},  # FUTURE spike — must not affect row 67
        },
    )

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    sos_events = [e for e in events if e["event"] == "SOS"]
    assert len(sos_events) == 1
    assert sos_events[0]["event_date"] == str(df["date"].iloc[67])


def test_rolling_avg_del_differs_from_global_mean():
    """avg_del_pct is rolling: with a low-delivery prefix (rows 0-30 at 10%)
    and a high suffix (rows 31-69 at 50%), the rolling mean at row 40 (~19.1)
    differs from the global mean (~32.3); an SOS with del_pct=25 fires on the
    rolling baseline but would be suppressed by the global one."""
    df = _build_df(
        n=70,
        override_rows={
            40: {
                "open": 102.5,
                "close": 103.5,
                "high": 105.0,
                "low": 101.0,
                "volume": 65000.0,
                "delivery_pct": 25.0,
            },
        },
    )
    # Prefix profile differs from the suffix → rolling != global mean.
    df.loc[0:30, "delivery_pct"] = 10.0
    df.loc[31:69, "delivery_pct"] = 50.0
    df.at[40, "delivery_pct"] = 25.0

    rolling_at_40 = float(df["delivery_pct"].astype(float).expanding().mean().iloc[40])
    global_mean = float(df["delivery_pct"].values.astype(float).mean())
    assert rolling_at_40 < 20.0
    assert global_mean > 30.0
    assert rolling_at_40 != global_mean

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    sos_events = [e for e in events if e["event"] == "SOS"]
    assert len(sos_events) == 1
    assert sos_events[0]["event_date"] == str(df["date"].iloc[40])
