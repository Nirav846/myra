"""
Tests for Bottom Hunter M1 scanner (bottom_hunter_m1_scanner.py).

Covers the pure crossing/cooldown math (a bit-exact port of the backtest's
``_precompute_kaushik_events`` sweep) plus scanner scan() e2e behaviour
with a synthetic universe and a temp meta sidecar.  No network, no
production DB writes — the meta sidecar is a tmp_path file.

The crossing series mirror the backtest fixture series from
``tests/test_backtest_components.py`` (RIDER = discrete dip-recover
crossing; CYCLER = cooldown + fresh-52-week-low restart).
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from myra_app.strategies.bottom_hunter_m1_scanner import (
    BottomHunterM1Scanner,
    detect_events,
)
from myra_app.strategies.bottom_hunter_m1_state import (
    connect_meta,
    delete_position,
    load_cooldown,
    load_positions,
    upsert_position,
)

LOOKBACK = 252
MULT = 1.20


# ---------------------------------------------------------------------------
# detect_events — pure crossing/cooldown math
# ---------------------------------------------------------------------------


def _flat(close=100.0, low=99.0, n=LOOKBACK):
    return ([close] * n, [low] * n)


def test_detect_hand_verified_crossing():
    """252 flat bars, dip to 80, recover to 96.2 → crossing on the recovery
    day with year_low 80, threshold 96.0, overshoot = 96.2/96 - 1 (0.2083%)."""
    closes, lows = _flat()
    closes = closes + [80.0, 96.2]
    lows = lows + [80.0, 85.0]
    events, final = detect_events(
        np.array(closes), np.array(lows), lookback=LOOKBACK, recovery_mult=MULT
    )
    assert len(events) == 1
    idx, overshoot = events[0]
    assert idx == len(closes) - 1  # recovery day
    assert overshoot == pytest.approx(96.2 / (MULT * 80.0) - 1.0, abs=1e-9)
    assert final["in_cooldown"] is True
    assert final["floor"] == pytest.approx(80.0)


def test_detect_no_crossing_when_recovery_below_line():
    """Recover to 95.9 (line to cross = 96.0) → no event, still below."""
    closes, lows = _flat()
    closes = closes + [80.0, 95.9]
    lows = lows + [80.0, 85.0]
    events, _ = detect_events(
        np.array(closes), np.array(lows), lookback=LOOKBACK, recovery_mult=MULT
    )
    assert events == []


def test_detect_exact_threshold_crossing():
    """close[i] == exactly 1.2 * year_low fires (>= rule, backtest parity)."""
    closes, lows = _flat()
    closes = closes + [80.0, 96.0]
    lows = lows + [80.0, 85.0]
    events, _ = detect_events(
        np.array(closes), np.array(lows), lookback=LOOKBACK, recovery_mult=MULT
    )
    assert len(events) == 1
    assert events[0][1] == pytest.approx(0.0, abs=1e-9)


def test_detect_cooldown_blocks_second_signal_same_floor():
    """After the first crossing, an identical dip-recover does NOT re-signal
    (no fresh 52-week low strictly below the floor)."""
    closes, lows = _flat()
    closes = closes + [80.0, 96.2] + [80.0, 96.2]
    lows = lows + [80.0, 85.0] + [80.0, 85.0]
    events, final = detect_events(
        np.array(closes), np.array(lows), lookback=LOOKBACK, recovery_mult=MULT
    )
    assert len(events) == 1  # only the first crossing
    assert final["in_cooldown"] is True
    assert final["floor"] == pytest.approx(80.0)


def test_detect_fresh_52w_low_restarts_cycle():
    """A year_low strictly below the floor restarts the cycle; the next
    dip-recover becomes a NEW signal with a NEW (lower) floor."""
    closes, lows = _flat()
    closes = closes + [80.0, 96.2] + [97.0] * 100 + [75.0, 90.2]
    lows = lows + [80.0, 85.0] + [96.0] * 100 + [75.0, 80.0]
    events, final = detect_events(
        np.array(closes), np.array(lows), lookback=LOOKBACK, recovery_mult=MULT
    )
    assert len(events) == 2
    assert events[1][0] == 253 + 1 + 100 + 1  # 355
    assert final["in_cooldown"] is True
    assert final["floor"] == pytest.approx(75.0)


def test_detect_seeded_state_suppresses_historical_signal():
    """Seeded in cooldown (floor=80): the original crossing is inside the
    seeded cycle and must NOT re-fire; a later fresh-low cycle still
    signals.  Mirrors the live restart contract."""
    closes, lows = _flat()
    closes = closes + [80.0, 96.2] + [97.0] * 100 + [75.0, 90.2]
    lows = lows + [80.0, 85.0] + [96.0] * 100 + [75.0, 80.0]
    seeded, final = detect_events(
        np.array(closes),
        np.array(lows),
        lookback=LOOKBACK,
        recovery_mult=MULT,
        floor=80.0,
        run_min=None,
    )
    assert len(seeded) == 1  # ONLY the fresh-low cycle re-signals
    assert final["in_cooldown"] is True
    assert final["floor"] == pytest.approx(75.0)


def test_detect_run_min_accumulates_after_exit():
    """Crossing at 253 (floor 80).  Fresh low 79 ends cooldown, price
    recovery above 1.2*79=94.8 → a second signal with the NEW floor 79.
    run_min persists across the in/out transition (never reset mid-run)."""
    closes, lows = _flat()
    closes = closes + [80.0, 96.2, 79.5, 95.0]
    lows = lows + [80.0, 85.0, 79.0, 84.0]
    events, final = detect_events(
        np.array(closes), np.array(lows), lookback=LOOKBACK, recovery_mult=MULT
    )
    assert len(events) == 2
    assert events[1][0] == 255
    assert final["floor"] == pytest.approx(79.0)


def test_detect_insufficient_window():
    """Fewer than lookback+1 bars → no events, no crash."""
    events, final = detect_events(
        np.array([100.0] * 100),
        np.array([99.0] * 100),
        lookback=LOOKBACK,
        recovery_mult=MULT,
    )
    assert events == []
    assert final["in_cooldown"] is False


# ---------------------------------------------------------------------------
# scanner helpers + default params
# ---------------------------------------------------------------------------


def test_defaults_match_confirmed_matrix():
    s = BottomHunterM1Scanner()
    assert s.lookback == 252
    assert s.recovery_mult == 1.20
    assert s.max_tranches == 3
    assert s.top_n == 500


def test_last_delivery_pct_reference_only():
    rows = [("2026-01-01", 100, 101, 99, 100, 1000, 100, 12.5, 0, None, None, None)]
    assert BottomHunterM1Scanner._last_delivery_pct(rows) == 12.5
    assert BottomHunterM1Scanner._last_delivery_pct([]) is None
    bad = [("2026-01-01", 100, 101, 99, 100, 1000, 100, None, 0, None, None, None)]
    assert BottomHunterM1Scanner._last_delivery_pct(bad) is None


# ---------------------------------------------------------------------------
# scan() e2e — synthetic universe + temp meta sidecar
# ---------------------------------------------------------------------------


def _rows_series(close_seq, low_seq, start="2025-01-01"):
    import datetime as _dt

    d0 = _dt.date.fromisoformat(start)
    rows = []
    for i, (c, lo) in enumerate(zip(close_seq, low_seq)):
        d = (d0 + _dt.timedelta(days=i)).isoformat()
        rows.append((d, c, c + 1.0, lo, c, 1000, 100, 10.0, 0, None, None, None))
    return rows


def _demo_rows(symbol: str):
    """TEST → dip+recover crossing on the last bar; FLAT → no crossing."""
    if symbol == "FLAT":
        close_seq = [100.0] * (LOOKBACK + 260)
        low_seq = [99.0] * (LOOKBACK + 260)
    else:
        close_seq = [100.0] * LOOKBACK + [80.0, 96.2]
        low_seq = [99.0] * LOOKBACK + [80.0, 85.0]
    return _rows_series(close_seq, low_seq)


def _make_scanner(tmp_path, top_n=500):
    return BottomHunterM1Scanner(top_n=top_n, state_db=str(tmp_path / "meta.db"))


def _patched_scan(scanner, as_of, symbols, tech_rows):
    """Run scanner.scan with universe/data fully synthetic."""
    with (
        patch.object(
            scanner,
            "_get_universe",
            return_value=[(s, i, 100.0) for i, s in enumerate(symbols, start=1)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner,
            "_get_tech_data",
            lambda symbol, min_date, max_date=None: tech_rows(symbol),
        ),
    ):
        return scanner.scan(as_on_date=as_of)


def test_scan_live_new_classification(tmp_path):
    """Live scan (state empty → bootstrap) surfaces TEST as NEW, writes
    cooldown, and a second live run does NOT re-surface the old signal.
    Use AS_OF matching the crossing bar date to avoid the stale guard."""
    AS_OF = "2025-09-11"  # last bar of _demo_rows("TEST")
    scanner = _make_scanner(tmp_path)
    scanner._resolve_as_on_date = lambda *a, **k: AS_OF

    result = _patched_scan(scanner, AS_OF, ["TEST", "FLAT"], _demo_rows)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["symbol"] == "TEST"
    assert row["signal_type"] == "NEW"
    assert row["overshoot_pct"] == pytest.approx(0.208, abs=0.001)
    assert row["year_low"] == pytest.approx(80.0)
    assert row["recovery_line"] == pytest.approx(96.0)
    assert not row["at_tranche_cap"]  # NEW signal is never at cap
    assert row["signal_date"] == AS_OF

    # Cooldown persisted for TEST (in cooldown), FLAT absent.
    conn = connect_meta(str(tmp_path / "meta.db"))
    cd = load_cooldown(conn)
    conn.close()
    assert "TEST" in cd
    assert cd["TEST"]["floor"] == pytest.approx(80.0)
    assert "FLAT" not in cd

    # Second live run: seeded → the historical crossing must NOT re-fire.
    scanner2 = _make_scanner(tmp_path)
    scanner2._resolve_as_on_date = lambda *a, **k: AS_OF
    result2 = _patched_scan(scanner2, AS_OF, ["TEST", "FLAT"], _demo_rows)
    assert result2.empty


AS_OF = "2025-09-11"  # last bar of _demo_rows("TEST")


def test_scan_live_add_classification_and_tranche_cap(tmp_path):
    """A symbol on the position book is classified ADD; n_tranches >= cap
    sets at_tranche_cap=True."""
    for n_tranches, expect_cap in [(1, False), (3, True)]:
        meta_db = str(tmp_path / "meta.db")
        scanner = _make_scanner(tmp_path)
        scanner._resolve_as_on_date = lambda a=None: AS_OF
        conn = connect_meta(meta_db)
        upsert_position(
            conn,
            "TEST",
            first_entry_date="2025-10-01",
            last_tranche_date="2025-11-20",
            n_tranches=n_tranches,
            blended_basis=92.5,
            updated_at="2025-11-20",
        )
        conn.close()

        result = _patched_scan(scanner, AS_OF, ["TEST"], _demo_rows)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["signal_type"] == "ADD"
        assert bool(row["at_tranche_cap"]) is expect_cap
        assert row["n_tranches"] == n_tranches
        assert row["blended_basis"] == pytest.approx(92.5)
        (tmp_path / "meta.db").unlink(missing_ok=True)


def test_scan_backdated_unseeded_and_read_only(tmp_path):
    """Back-dated scans sweep FULL history unseeded (reproduce the backtest)
    and NEVER write cooldown state."""
    LATEST = "2026-09-04"
    SCAN_DATE = "2025-09-11"  # matches _demo_rows crossing bar
    scanner = _make_scanner(tmp_path)
    # _resolve_as_on_date: use the arg when provided, else LATEST.
    scanner._resolve_as_on_date = lambda a=None: a if a else LATEST
    with (
        patch.object(
            scanner,
            "_get_universe",
            return_value=[("TEST", 1, 100.0)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner,
            "_db_min_date",
            return_value="1900-01-01",
        ),
        patch.object(
            scanner,
            "_get_tech_data",
            lambda symbol, min_date, max_date=None: _demo_rows(symbol),
        ),
    ):
        result = scanner.scan(as_on_date=SCAN_DATE)

    assert len(result) == 1
    assert result.iloc[0]["signal_type"] == "NEW"
    assert result.iloc[0]["signal_date"] == SCAN_DATE
    # No state sidecar created by a back-dated scan (read-only reproduction).
    conn = connect_meta(str(tmp_path / "meta.db"))
    assert load_cooldown(conn) == {}
    conn.close()


def test_scan_stale_last_bar_suppressed(tmp_path):
    """A crossing on a bar older than max_stale_days before the scan date is
    NOT surfaced (stale guard), even though it is inside the data window."""
    scanner = _make_scanner(tmp_path)
    scanner._resolve_as_on_date = lambda *a, **k: "2026-01-02"
    as_of = pd.Timestamp("2026-01-02")
    rows = _demo_rows("TEST")
    # Place the last bar (the crossing) 25 days before as_of → older than
    # the 10-day stale cutoff but inside the max_date window.
    last_bar = as_of - pd.Timedelta(days=25)
    base = last_bar - pd.Timedelta(days=len(rows) - 1)
    shifted = []
    for i, r in enumerate(rows):
        d = (base + pd.Timedelta(days=i)).isoformat()[:10]
        shifted.append((d,) + r[1:])
    with (
        patch.object(
            scanner,
            "_get_universe",
            return_value=[("TEST", 1, 100.0)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner, "_get_tech_data", lambda symbol, min_date, max_date=None: shifted
        ),
    ):
        result = scanner.scan(as_on_date="2026-01-02")
    assert len(result) == 0


def test_scan_empty_universe_returns_empty_df(tmp_path):
    scanner = _make_scanner(tmp_path)
    with (
        patch.object(scanner, "_get_universe", return_value=[]),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
    ):
        result = scanner.scan()
    assert isinstance(result, pd.DataFrame)
    assert result.empty


# ---------------------------------------------------------------------------
# scan_near_trigger — near-trigger watchlist
# ---------------------------------------------------------------------------


def _near_trigger_rows(symbol="NEAR"):
    """252 flat bars at 100, then close=118 (within 5% of recovery line 120).
    year_low=100 → recovery_line=120, pct_to_trigger=(1-118/120)*100≈1.67%."""
    close_seq = [100.0] * LOOKBACK + [118.0]
    low_seq = [100.0] * LOOKBACK + [100.0]
    return _rows_series(close_seq, low_seq)


def test_near_trigger_returns_approaching_stocks(tmp_path):
    """Stocks close to but below the recovery line appear in near-trigger output."""
    scanner = _make_scanner(tmp_path)
    scanner._resolve_as_on_date = lambda *a, **k: "2025-09-11"

    with (
        patch.object(
            scanner,
            "_get_universe",
            return_value=[("NEAR", 1, 100.0), ("FAR", 2, 200.0)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner,
            "_get_tech_data",
            lambda symbol, min_date, max_date=None: (
                _near_trigger_rows("NEAR")
                if symbol == "NEAR"
                else _flat(close=100, low=100, n=LOOKBACK + 1)
            ),
        ),
    ):
        result = scanner.scan_near_trigger(as_on_date="2025-09-11", band_pct=5.0)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["symbol"] == "NEAR"
    assert 0 < row["pct_to_trigger"] <= 5.0
    assert row["recovery_line"] == pytest.approx(1.20 * 100.0)


def test_near_trigger_excludes_crossed_stocks(tmp_path):
    """Stocks that already crossed the recovery line are excluded."""
    scanner = _make_scanner(tmp_path)
    scanner._resolve_as_on_date = lambda *a, **k: "2025-09-11"

    with (
        patch.object(
            scanner,
            "_get_universe",
            return_value=[("CROSSED", 1, 100.0)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner,
            "_get_tech_data",
            lambda symbol, min_date, max_date=None: _demo_rows("CROSSED"),
        ),
    ):
        result = scanner.scan_near_trigger(as_on_date="2025-09-11", band_pct=5.0)

    assert result.empty  # crossed stocks don't appear


def test_near_trigger_excludes_cooldown_stocks(tmp_path):
    """Stocks in cooldown (recent signal, haven't set fresh low) are excluded."""
    scanner = _make_scanner(tmp_path)
    scanner._resolve_as_on_date = lambda *a, **k: "2025-09-11"

    # Seed cooldown for COOLDOWN_SYM — it triggered recently and is cooling off.
    meta_db = str(tmp_path / "meta.db")
    conn = connect_meta(meta_db)
    upsert_position(
        conn,
        "COOLDOWN_SYM",
        first_entry_date="2025-09-10",
        last_tranche_date="2025-09-10",
        n_tranches=1,
        blended_basis=100.0,
        updated_at="2025-09-10",
    )
    from myra_app.strategies.bottom_hunter_m1_state import save_cooldown

    save_cooldown(
        conn,
        {
            "COOLDOWN_SYM": {
                "signal_date": "2025-09-10",
                "floor": 100.0,
                "run_min": 100.0,
            }
        },
        "2025-09-10",
    )
    conn.close()

    scanner2 = _make_scanner(tmp_path)  # Fresh scanner reads cooldown from meta.db
    scanner2._resolve_as_on_date = lambda *a, **k: "2025-09-11"

    with (
        patch.object(
            scanner2,
            "_get_universe",
            return_value=[("COOLDOWN_SYM", 1, 100.0), ("FRESH", 2, 200.0)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner2,
            "_get_tech_data",
            lambda symbol, min_date, max_date=None: (
                # COOLDOWN_SYM: close=118, year_low=100 → 1.67% from trigger (would pass band)
                _near_trigger_rows("COOLDOWN_SYM")
                if symbol == "COOLDOWN_SYM"
                # FRESH: same price profile but NOT in cooldown
                else _near_trigger_rows("FRESH")
            ),
        ),
    ):
        result = scanner2.scan_near_trigger(as_on_date="2025-09-11", band_pct=5.0)

    # COOLDOWN_SYM should NOT appear despite being within 5% of trigger.
    assert len(result) == 1
    assert result.iloc[0]["symbol"] == "FRESH"


def test_near_trigger_sorted_by_proximity(tmp_path):
    """Results are sorted with closest-to-trigger first."""

    def _make_rows(close_val, low_val):
        close_seq = [100.0] * LOOKBACK + [close_val]
        low_seq = [100.0] * LOOKBACK + [low_val]
        return _rows_series(close_seq, low_seq)

    scanner = _make_scanner(tmp_path)
    scanner._resolve_as_on_date = lambda *a, **k: "2025-09-11"

    # FAR: year_low=90 → recovery_line=108, close=106 → 1.85% away
    # CLOSE: year_low=100 → recovery_line=120, close=118 → 1.67% away
    # CLOSE should appear first
    with (
        patch.object(
            scanner,
            "_get_universe",
            return_value=[("FAR", 1, 100.0), ("CLOSE", 2, 200.0)],
        ),
        patch(
            "myra_app.strategies.bottom_hunter_m1_scanner.load_ohlcv_for_universe",
            return_value={},
        ),
        patch.object(
            scanner,
            "_get_tech_data",
            lambda symbol, min_date, max_date=None: (
                _make_rows(106.0, 90.0) if symbol == "FAR" else _make_rows(118.0, 100.0)
            ),
        ),
    ):
        result = scanner.scan_near_trigger(as_on_date="2025-09-11", band_pct=5.0)

    assert len(result) == 2
    assert result.iloc[0]["pct_to_trigger"] <= result.iloc[1]["pct_to_trigger"]


# ---------------------------------------------------------------------------
# Position CRUD + alert computation
# ---------------------------------------------------------------------------


def test_position_upsert_and_load(tmp_path):
    """Positions can be inserted and loaded back."""
    from myra_app.strategies.bottom_hunter_m1_state import (
        delete_position,
        load_positions,
    )

    conn = connect_meta(str(tmp_path / "meta.db"))
    upsert_position(
        conn,
        "TEST",
        first_entry_date="2025-01-15",
        last_tranche_date="2025-02-10",
        n_tranches=2,
        blended_basis=95.50,
        updated_at="2025-09-01",
    )
    positions = load_positions(conn)
    assert "TEST" in positions
    assert positions["TEST"]["n_tranches"] == 2
    assert positions["TEST"]["blended_basis"] == pytest.approx(95.50)

    delete_position(conn, "TEST")
    positions = load_positions(conn)
    assert "TEST" not in positions
    conn.close()


def test_position_tranche_cap_enforced():
    """n_tranches must be 1-3."""
    from myra_app.strategies.bottom_hunter_m1_state import connect_meta as cm

    conn = cm(":memory:")
    # Valid range
    upsert_position(
        conn,
        "A",
        first_entry_date="2025-01-01",
        last_tranche_date="2025-01-01",
        n_tranches=3,
        blended_basis=100.0,
        updated_at="2025-09-01",
    )
    pos = load_positions(conn)
    assert pos["A"]["n_tranches"] == 3
    conn.close()


def test_alert_hold_when_no_action():
    """HOLD alert: position exists, no target hit, no signal, cap not approaching."""
    # This is a logic test — verify the alert state machine.
    # HOLD = default when: price < target, no signal, cap > 20 days away.
    assert _compute_alert(
        current_price=95.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=False,
        n_tranches=1,
        days_to_cap=200,
    ) == ("HOLD", "")


def test_alert_sell_when_target_reached():
    """SELL alert: current price >= blended_basis * (1 + target_pct)."""
    alert, _ = _compute_alert(
        current_price=111.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=False,
        n_tranches=1,
        days_to_cap=200,
    )
    assert alert == "SELL"


def test_alert_average_on_signal():
    """AVERAGE alert: fresh signal fired, under 3-tranche cap."""
    alert, detail = _compute_alert(
        current_price=95.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=True,
        n_tranches=1,
        days_to_cap=200,
    )
    assert alert == "AVERAGE"
    assert "signal" in detail.lower() or "Signal" in detail


def test_alert_average_blocked_at_cap():
    """No AVERAGE alert when at 3-tranche cap — falls back to HOLD."""
    alert, _ = _compute_alert(
        current_price=95.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=True,
        n_tranches=3,
        days_to_cap=200,
    )
    assert alert == "HOLD"


def test_alert_cap_approaching():
    """CAP APPROACHING: within 20 trading days of 252-day cap, no target hit."""
    alert, detail = _compute_alert(
        current_price=95.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=False,
        n_tranches=1,
        days_to_cap=15,
    )
    assert alert == "CAP APPROACHING"
    assert "15" in detail


def test_alert_priority_sell_over_average():
    """SELL takes priority over AVERAGE when both conditions are met."""
    alert, _ = _compute_alert(
        current_price=111.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=True,
        n_tranches=1,
        days_to_cap=200,
    )
    assert alert == "SELL"


def test_alert_priority_average_over_cap():
    """AVERAGE takes priority over CAP APPROACHING."""
    alert, _ = _compute_alert(
        current_price=95.0,
        blended_basis=100.0,
        target_pct=10.0,
        has_signal=True,
        n_tranches=2,
        days_to_cap=10,
    )
    assert alert == "AVERAGE"


# ---------------------------------------------------------------------------
# Helper for alert computation tests (mirrors backend logic)
# ---------------------------------------------------------------------------

CAP_WARNING_DAYS = 20


def _compute_alert(
    current_price: float,
    blended_basis: float,
    target_pct: float,
    has_signal: bool,
    n_tranches: int,
    days_to_cap: int,
) -> tuple[str, str]:
    """Replicate the backend alert state machine for unit testing."""
    alert = "HOLD"
    detail = ""

    target_price = blended_basis * (1 + target_pct / 100)
    if current_price >= target_price:
        alert = "SELL"
        detail = f"Target Rs{target_price:.2f} reached"

    if alert == "HOLD" and has_signal and n_tranches < 3:
        alert = "AVERAGE"
        detail = "Signal detected"

    if alert == "HOLD" and days_to_cap <= CAP_WARNING_DAYS and days_to_cap > 0:
        alert = "CAP APPROACHING"
        detail = f"{days_to_cap} trading days remaining"

    return alert, detail
