"""Phase 44: Recovery Ladder columnar access.

Recovery Ladder (``BottomHunterM1Scanner``) reads four of the twelve loaded
columns — date, low, close and the last ``delivery_pct`` — and never builds a
frame; the tuple list was only transport. These tests pin both contracts:

* ``_ColumnarWindow.rows`` must equal ``rows_for_symbol`` cell for cell (the
  accessor contract every other caller still uses);
* ``_ColumnarWindow.bars`` must equal the arrays the detection loop used to
  build from those tuples;
* ``scan()`` must emit the same candidates, order and fingerprint as the
  pre-change ``rows_for_symbol`` path.

Expectations are derived from the fixture rows or from ``rows_for_symbol``,
never from the window's internals.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

import myra_app.strategies.bottom_hunter_m1_scanner as m1
from myra_app.db.bulk_loader import COLUMNS_12, rows_for_symbol
from myra_app.strategies.bottom_hunter_m1_scanner import (
    BottomHunterM1Scanner,
    _ColumnarWindow,
)
from myra_app.strategies.bottom_hunter_m1_state import connect_meta, load_cooldown

LOOKBACK = 252
AS_ON = "2025-09-11"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _rows(close_seq, low_seq, start=None, end=None, delivery=10.0):
    """Fixture rows in COLUMNS_12 order, ISO date strings.

    Daily bars ending on the scan date by default, so the "last bar is not
    stale" guard in ``scan()`` sees these fixtures as a live crossing.
    """
    periods = len(close_seq)
    if end is not None:
        dates = pd.date_range(end=end, periods=periods, freq="D")
    else:
        dates = pd.date_range(start or "2024-01-01", periods=periods, freq="D")
    out = []
    for day, close, low in zip(dates, close_seq, low_seq):
        out.append(
            (
                day.strftime("%Y-%m-%d"),
                close,
                close + 1.0,
                low,
                close,
                1_000,
                int(1_000 * delivery / 100),
                delivery,
                0.0,
                None,
                None,
                None,
            )
        )
    return out


def _crossing_rows(n=LOOKBACK):
    """Flat 100 then a dip to 80 and a recovery to 96.2 on the last bar."""
    return _rows([100.0] * n + [80.0, 96.2], [99.0] * n + [80.0, 85.0], end=AS_ON)


def _flat_rows(n=LOOKBACK + 260, start="2024-01-01"):
    return _rows([100.0] * n, [99.0] * n, start=start)


def _frame(rows, columns=COLUMNS_12):
    return pd.DataFrame(list(rows), columns=list(columns))


def _mixed_bulk() -> dict[str, pd.DataFrame]:
    """A slice shaped like ``load_ohlcv_for_universe`` output."""
    bulk = {
        "CROSS": _frame(_crossing_rows()),
        "FLAT": _frame(_flat_rows()),
        "SHORT": _frame(_crossing_rows(n=40)),
        "EMPTY": _frame([]),
    }
    for i in range(12):
        base = 100.0 + i
        close = [base] * LOOKBACK + [base * 0.8, base * 0.962]
        low = [base * 0.99] * LOOKBACK + [base * 0.8, base * 0.85]
        bulk[f"SYN{i:02d}"] = _frame(_rows(close, low, end=AS_ON, delivery=10.0 + i))
    return bulk


def _fingerprint(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "empty"
    keyed = frame.copy()
    keyed.columns = [str(c) for c in keyed.columns]
    keyed = keyed.astype(str).sort_values(list(keyed.columns)).reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(keyed, index=False).to_numpy().tobytes()
    ).hexdigest()[:16]


def _legacy_bars(bulk):
    """Reference accessor: the pre-change tuple path on the same frames."""

    def _impl(self, symbol, min_date, max_date=None):
        rows = rows_for_symbol(bulk, symbol, COLUMNS_12, min_date, max_date or AS_ON)
        if not rows:
            return (
                np.empty(0, dtype=float),
                np.empty(0, dtype=float),
                [],
                np.empty(0, dtype=object),
            )
        return (
            np.array([r[4] for r in rows], dtype=float),
            np.array([r[3] for r in rows], dtype=float),
            [r[0] for r in rows],
            np.array([r[7] for r in rows], dtype=object),
        )

    return _impl


# ---------------------------------------------------------------------------
# _ColumnarWindow: row and array parity
# ---------------------------------------------------------------------------


def test_rows_match_rows_for_symbol_for_every_symbol():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)

    compared = 0
    for symbol in bulk:
        expected = rows_for_symbol(bulk, symbol, COLUMNS_12, "1900-01-01", AS_ON)
        if symbol == "EMPTY":
            assert window.rows(symbol, "1900-01-01", AS_ON) == []
            continue
        actual = window.rows(symbol, "1900-01-01", AS_ON)
        assert actual == expected, symbol
        assert [type(v) for v in actual[-1]] == [type(v) for v in expected[-1]]
        compared += 1
    assert compared == len(bulk) - 1


def test_bars_match_the_arrays_the_loop_built_from_tuples():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)

    for symbol in bulk:
        rows = rows_for_symbol(bulk, symbol, COLUMNS_12, "1900-01-01", AS_ON)
        closes, lows, dates, delivery = window.bars(symbol, "1900-01-01", AS_ON)
        if not rows:
            assert len(closes) == len(lows) == len(dates) == 0
            continue
        assert closes.dtype == np.float64
        assert lows.dtype == np.float64
        assert list(closes) == [float(r[4]) for r in rows]
        assert list(lows) == [float(r[3]) for r in rows]
        assert dates == [r[0] for r in rows]
        assert list(delivery)[-1] == rows[-1][7]


def test_narrow_date_windows_clip_exactly_like_rows_for_symbol():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)
    windows = [
        ("2024-01-01", AS_ON),
        ("2024-06-01", "2024-12-31"),
        ("2024-11-01", "2024-11-30"),
        ("2025-01-01", "2025-01-01"),
        ("2026-01-01", AS_ON),
    ]

    for min_date, max_date in windows:
        expected = rows_for_symbol(bulk, "SYN03", COLUMNS_12, min_date, max_date)
        assert window.rows("SYN03", min_date, max_date) == expected, (
            min_date,
            max_date,
        )
        closes, _, dates, _ = window.bars("SYN03", min_date, max_date)
        assert len(closes) == len(expected)
        assert dates == [r[0] for r in expected]


def test_rows_preserve_chronological_order_and_index_free_tuples():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)
    dates = [r[0] for r in window.rows("CROSS", "1900-01-01", AS_ON)]

    assert dates == sorted(dates)
    assert isinstance(dates[0], str)
    assert len(set(dates)) == len(dates)


def test_a_column_absent_from_the_slice_is_none_padded_in_place():
    """Positional callers index by position, so a missing column must not shift."""
    bulk = _mixed_bulk()
    bulk["DRIFT"] = bulk["CROSS"].drop(columns=["delivery_pct"])
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)

    rows = window.rows("CROSS", "1900-01-01", AS_ON)
    assert len(rows[0]) == len(COLUMNS_12)
    assert rows[0][7] == 10.0  # this symbol has the column; no cross-symbol bleed
    assert rows[0][8] == 0.0  # nifty_outperformance_score keeps its index
    expected = rows_for_symbol(bulk, "CROSS", COLUMNS_12, "1900-01-01", AS_ON)
    assert rows == expected

    # The symbol whose frame lacks the column is padded, not shifted.
    drift = window.rows("DRIFT", "1900-01-01", AS_ON)
    assert len(drift[0]) == len(COLUMNS_12)
    assert drift[0][7] is None
    assert drift[0][8] == 0.0
    assert drift == rows_for_symbol(bulk, "DRIFT", COLUMNS_12, "1900-01-01", AS_ON)

    closes, _, _, delivery = window.bars("DRIFT", "1900-01-01", AS_ON)
    assert len(closes) == len(drift)
    assert list(delivery) == [None] * len(drift)


def test_an_all_na_column_keeps_its_position_and_dtype():
    bulk = _mixed_bulk()
    bulk["NADEL"] = bulk["FLAT"].assign(delivery_pct=np.nan)
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)

    rows = window.rows("NADEL", "1900-01-01", AS_ON)
    assert rows[-1][7] is None
    assert rows == rows_for_symbol(bulk, "NADEL", COLUMNS_12, "1900-01-01", AS_ON)
    _, _, _, delivery = window.bars("NADEL", "1900-01-01", AS_ON)
    assert all(pd.isna(value) for value in delivery)


def test_absent_empty_and_short_symbols():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_12)

    assert window.rows("GHOST", "1900-01-01", AS_ON) == []
    assert window.bars("GHOST", "1900-01-01", AS_ON)[2] == []
    assert window.rows("EMPTY", "1900-01-01", AS_ON) == []
    assert len(window.rows("SHORT", "1900-01-01", AS_ON)) == 42
    assert len(window.bars("SHORT", "1900-01-01", AS_ON)[0]) == 42


def test_from_frames_with_no_frames_serves_nothing():
    window = _ColumnarWindow.from_frames({}, COLUMNS_12)
    assert window.spans == {}
    assert window.rows("ANY", "1900-01-01", AS_ON) == []
    assert window.columns == tuple(COLUMNS_12)


def test_returned_bars_share_no_memory_and_are_safe_to_mutate():
    window = _ColumnarWindow.from_frames(_mixed_bulk(), COLUMNS_12)
    closes, lows, dates, delivery = window.bars("CROSS", "1900-01-01", AS_ON)
    baseline = closes.tolist()

    assert not np.shares_memory(closes, window.arrays["close"])
    assert not np.shares_memory(lows, window.arrays["low"])
    closes[:] = -1.0
    lows[:] = -1.0
    dates.clear()
    delivery[:] = -1.0

    again = window.bars("CROSS", "1900-01-01", AS_ON)
    assert again[0].tolist() == baseline
    assert len(again[2]) == len(baseline)
    # Sibling symbols were never exposed.
    assert window.bars("SYN00", "1900-01-01", AS_ON)[0].min() > 0


def test_rows_are_independent_of_bars_copies():
    window = _ColumnarWindow.from_frames(_mixed_bulk(), COLUMNS_12)
    rows = window.rows("CROSS", "1900-01-01", AS_ON)
    first_close = rows[-1][4]
    rows[-1] = ("1999-01-01",) + rows[-1][1:]

    assert window.rows("CROSS", "1900-01-01", AS_ON)[-1][4] == first_close


# ---------------------------------------------------------------------------
# Scanner: identical candidates, lifecycle, fallback
# ---------------------------------------------------------------------------


def _scan(monkeypatch, tmp_path, bulk, *, legacy=False, tag=""):
    scanner = BottomHunterM1Scanner(
        top_n=500, state_db=str(tmp_path / f"meta_{legacy}{tag}.db")
    )
    scanner._resolve_as_on_date = lambda arg=None: arg or AS_ON
    symbols = list(bulk)
    monkeypatch.setattr(
        scanner,
        "_get_universe",
        lambda: [(s, i, 100.0) for i, s in enumerate(symbols, start=1)],
    )
    monkeypatch.setattr(m1, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk))
    if legacy:
        monkeypatch.setattr(BottomHunterM1Scanner, "_get_tech_bars", _legacy_bars(bulk))
    return scanner.scan(as_on_date=AS_ON), scanner


def test_scan_matches_the_legacy_per_symbol_path(monkeypatch, tmp_path):
    bulk = _mixed_bulk()
    expected, _ = _scan(monkeypatch, tmp_path, bulk, legacy=True)
    actual, _ = _scan(monkeypatch, tmp_path, bulk)

    assert len(expected) >= 1
    pd.testing.assert_frame_equal(actual, expected, check_dtype=True)
    assert _fingerprint(actual) == _fingerprint(expected)


def test_scan_is_repeatable_and_releases_the_bulk_dictionary(monkeypatch, tmp_path):
    bulk = _mixed_bulk()
    first, scanner = _scan(monkeypatch, tmp_path, bulk, tag="_a")
    # A second run starts from cold cooldown state: the first run is live, so it
    # deliberately persists the cooldown it just replayed.
    second, _ = _scan(monkeypatch, tmp_path, bulk, tag="_b")

    pd.testing.assert_frame_equal(first, second, check_dtype=True)
    assert scanner._columnar is not None
    assert scanner._bulk_data is None
    assert scanner._columnar.spans


def test_scan_candidate_fields_are_unchanged(monkeypatch, tmp_path):
    bulk = _mixed_bulk()
    result, _ = _scan(monkeypatch, tmp_path, bulk)

    row = result[result["symbol"] == "CROSS"].iloc[0]
    assert row["signal_type"] == "NEW"
    assert row["signal_date"] == "2025-09-11"
    assert row["close"] == pytest.approx(96.2)
    assert row["year_low"] == pytest.approx(80.0)
    assert row["recovery_line"] == pytest.approx(96.0)
    assert row["overshoot_pct"] == pytest.approx(0.208, abs=0.001)
    assert row["delivery_pct"] == pytest.approx(10.0)
    assert row["mcap_rank"] == 1


def test_scan_skips_short_history_symbols(monkeypatch, tmp_path):
    result, _ = _scan(monkeypatch, tmp_path, {"SHORT": _frame(_crossing_rows(n=40))})

    assert result.empty


def test_scan_skips_a_symbol_absent_from_the_slice(monkeypatch, tmp_path):
    bulk = {"CROSS": _frame(_crossing_rows())}
    scanner = BottomHunterM1Scanner(top_n=500, state_db=str(tmp_path / "m.db"))
    scanner._resolve_as_on_date = lambda arg=None: arg or AS_ON
    monkeypatch.setattr(
        scanner,
        "_get_universe",
        lambda: [("CROSS", 1, 100.0), ("GHOST", 2, 100.0)],
    )
    monkeypatch.setattr(m1, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk))

    result = scanner.scan(as_on_date=AS_ON)
    assert result["symbol"].tolist() == ["CROSS"]


def test_scan_persists_cooldown_for_the_window_served_symbols(monkeypatch, tmp_path):
    bulk = _mixed_bulk()
    _, scanner = _scan(monkeypatch, tmp_path, bulk)

    conn = connect_meta(scanner.state_db)
    try:
        cooldown = load_cooldown(conn)
    finally:
        conn.close()
    assert "CROSS" in cooldown
    assert cooldown["CROSS"]["floor"] == pytest.approx(80.0)


def test_get_tech_bars_falls_back_without_a_window(tmp_path):
    """No window and no bulk dictionary -> the SQL/tuple path, unchanged."""
    scanner = BottomHunterM1Scanner(state_db=str(tmp_path / "m.db"))
    rows = _crossing_rows()
    scanner._get_tech_data = lambda symbol, min_date, max_date=None: rows

    closes, lows, dates, delivery = scanner._get_tech_bars("CROSS", "1900-01-01", AS_ON)

    assert list(closes) == [float(r[4]) for r in rows]
    assert list(lows) == [float(r[3]) for r in rows]
    assert dates == [r[0] for r in rows]
    assert len(delivery) == len(rows)


def test_get_tech_bars_with_nothing_available_is_empty(tmp_path):
    scanner = BottomHunterM1Scanner(state_db=str(tmp_path / "m.db"))
    scanner._get_tech_data = lambda symbol, min_date, max_date=None: []

    closes, lows, dates, delivery = scanner._get_tech_bars("NONE", "1900-01-01", AS_ON)

    assert len(closes) == len(lows) == len(dates) == len(delivery) == 0
    assert closes.dtype == np.float64


def test_get_tech_bars_signature_matches_the_progress_hook(tmp_path):
    """The web layer patches ``progress_attr`` and calls it positionally with
    ``(symbol, min_date, max_date)`` — that call shape must keep working."""
    scanner = BottomHunterM1Scanner(state_db=str(tmp_path / "m.db"))
    calls = []
    untouched = scanner._get_tech_bars

    def tracked(symbol, min_date=None, max_date=None):
        calls.append((symbol, min_date, max_date))
        return untouched(symbol, min_date, max_date)

    scanner._get_tech_bars = tracked
    scanner._get_tech_data = lambda symbol, min_date, max_date=None: _crossing_rows()

    bars = scanner._get_tech_bars("CROSS", "1900-01-01", AS_ON)
    assert calls == [("CROSS", "1900-01-01", AS_ON)]
    assert isinstance(bars[0], np.ndarray)
