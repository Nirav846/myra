"""Liquidity Flip: DataFrame-accessor migration (Phase 41).

The scanner used to pull a list of 12-tuples out of the bulk window and rebuild
a DataFrame from it. It now asks the existing ``get_df_for_symbol`` accessor for
the frame directly.

These tests pin three things:

* the migrated path is used in the bulk branch (frame accessor, not tuples);
* it produces **the same candidates** as the pre-migration recipe, which is
  reproduced here as an independent oracle built on ``rows_for_symbol``;
* the two contract hazards from the Phase 40 audit stay resolved: absent
  optional columns behave exactly like all-NaN ones, and index labels never
  reach the scanner's arithmetic.

The golden test computes its expectations by hand from the synthetic fixture, so
at least one assertion does not compare two paths against each other.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import myra_app.strategies.liquidity_flip_detector as lf_module
from myra_app.db.bulk_loader import COLUMNS_12, rows_for_symbol
from myra_app.strategies.liquidity_flip_detector import LiquidityFlipDetector

AS_ON = "2026-10-16"
ROWS_PER_SYMBOL = 140
PRIOR_DAYS = 110  # rows priced at 20% delivery
RECENT_DAYS = 30  # rows priced at 65% delivery

# Columns the scanner reads. ``sma_50`` is carried but never read; the 52-week
# pair is read through a ``pd.notna`` guard.
NINE_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "nifty_outperformance_score",
]


def _dates(n_rows: int) -> list[str]:
    end = datetime.strptime(AS_ON, "%Y-%m-%d")
    return [
        (end - timedelta(days=n_rows - 1 - i)).strftime("%Y-%m-%d")
        for i in range(n_rows)
    ]


def _frame(
    symbol: str,
    n_rows: int = ROWS_PER_SYMBOL,
    *,
    drop_52w: bool = False,
    blank_52w: bool = False,
    steady: bool = False,
) -> pd.DataFrame:
    """One symbol's OHLCV frame in the shape ``_read_bulk`` produces."""
    dates = _dates(n_rows)
    close = [300.0] * n_rows
    delivery_pct = [20.0] * n_rows
    if not steady:
        delivery_pct = [20.0] * max(0, n_rows - RECENT_DAYS) + [65.0] * min(
            RECENT_DAYS, n_rows
        )
    data = {
        "symbol": [symbol] * n_rows,
        "date": dates,
        "open": [c - 5.0 for c in close],
        "high": [c + 10.0 for c in close],
        "low": [c - 10.0 for c in close],
        "close": close,
        "volume": [6_000_000] * n_rows,
        "delivery": [5_000_000] * n_rows,
        "delivery_pct": delivery_pct,
        "nifty_outperformance_score": [0.0] * n_rows,
        "sma_50": [c - 2.0 for c in close],
        "high_52w": [320.0] * n_rows,
        "low_52w": [250.0] * n_rows,
    }
    if drop_52w:
        data.pop("high_52w")
        data.pop("low_52w")
        data.pop("sma_50")
    elif blank_52w:
        data["high_52w"] = [np.nan] * n_rows
        data["low_52w"] = [np.nan] * n_rows
    frame = pd.DataFrame(data)
    # ``symbol`` is legitimately absent from a zero-row frame, so carry the
    # identity alongside it rather than reading it back out of the data.
    frame.attrs["symbol"] = symbol
    return frame


def _bulk(*frames: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {frame.attrs["symbol"]: frame for frame in frames}


def _universe(*symbols: str, mcap: float = 5_000_000_000.0) -> list[tuple]:
    return [(symbol, mcap, 40.0) for symbol in symbols]


def legacy_tech_df(bulk, symbol, columns, min_date, max_date):
    """The pre-Phase-41 consumer recipe, as an independent oracle.

    Deliberately built on ``rows_for_symbol`` rather than on
    ``get_df_for_symbol`` so a defect shared by both accessors cannot make the
    parity test pass by construction.
    """
    rows = rows_for_symbol(bulk, symbol, columns, min_date, max_date)
    if not rows:
        return pd.DataFrame()
    if len(rows[0]) >= 12:
        frame = pd.DataFrame(rows, columns=list(COLUMNS_12))
    else:
        frame = pd.DataFrame(rows, columns=NINE_COLUMNS)
        frame["sma_50"] = None
        frame["high_52w"] = None
        frame["low_52w"] = None
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date").reset_index(drop=True)


def _scan(monkeypatch, bulk, universe, *, route="migrated", scanner=None):
    """Run one scan with a synthetic bulk window and universe."""
    scanner = scanner or LiquidityFlipDetector()

    def loader(start_date, end_date=None, symbols=None):
        wanted = list(symbols or bulk)
        return {s: bulk[s].copy() for s in wanted if s in bulk}

    # ``scan`` always reassigns the bulk window, so the window has to be
    # injected at the loader seam rather than pre-set on the instance.
    monkeypatch.setattr(lf_module, "load_ohlcv_for_universe", loader)
    monkeypatch.setattr(LiquidityFlipDetector, "_get_universe", lambda self: universe)
    # An in-memory DB has no tables, so the sector-map probe fails into its
    # own try/except and leaves the map empty, exactly like a failed read.
    monkeypatch.setattr(LiquidityFlipDetector, "_db_path", lambda self, key: ":memory:")
    if route == "legacy":
        monkeypatch.setattr(
            LiquidityFlipDetector,
            "_get_tech_df",
            lambda self, symbol, min_date, max_date=None: legacy_tech_df(
                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
            ),
        )
    return scanner.scan(as_on_date=AS_ON)


def _fingerprint(frame: pd.DataFrame) -> dict:
    if frame is None or frame.empty:
        return {
            "kind": "empty",
            "columns": list(frame.columns) if frame is not None else [],
        }
    ordered = frame.copy()
    ordered.columns = [str(c) for c in ordered.columns]
    ordered = (
        ordered.astype(str).sort_values(list(ordered.columns)).reset_index(drop=True)
    )
    return {
        "kind": "frame",
        "rows": int(len(ordered)),
        "columns": list(ordered.columns),
        "fingerprint": f"{int(pd.util.hash_pandas_object(ordered, index=False).sum()):x}",
    }


# ---------------------------------------------------------------------------
# The migration itself
# ---------------------------------------------------------------------------


def test_bulk_path_uses_the_frame_accessor(monkeypatch):
    """The bulk branch must read a frame, not a tuple list."""
    calls = {"frame": 0, "rows": 0}
    real_frame = lf_module.get_df_for_symbol
    real_rows = lf_module.rows_for_symbol

    def frame_accessor(*args, **kwargs):
        calls["frame"] += 1
        return real_frame(*args, **kwargs)

    def rows_accessor(*args, **kwargs):
        calls["rows"] += 1
        return real_rows(*args, **kwargs)

    monkeypatch.setattr(lf_module, "get_df_for_symbol", frame_accessor)
    monkeypatch.setattr(lf_module, "rows_for_symbol", rows_accessor)

    _scan(monkeypatch, _bulk(_frame("AAA")), _universe("AAA"))

    assert calls["frame"] == 1
    assert calls["rows"] == 0


def test_scan_still_uses_the_tuple_path_without_a_bulk_window(monkeypatch):
    """No bulk window => the unchanged per-symbol SQL fallback is used."""
    scanner = LiquidityFlipDetector()
    monkeypatch.setattr(scanner, "_get_universe", lambda: [])
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    assert scanner._bulk_data is None
    out = scanner.scan(as_on_date=AS_ON)
    assert out.empty


# ---------------------------------------------------------------------------
# Golden expectation (hand-computed from the fixture)
# ---------------------------------------------------------------------------


def test_golden_candidate_fields(monkeypatch):
    """Hand-computed expectation for one clean STRONG FLIP fixture.

    140 rows, 110 at 20% delivery then 30 at 65%, close 300, high 310, low 290,
    high_52w 320, low_52w 250, mcap 5e9, delivery 5e6.

    prior  = 20.0, recent = 65.0, jump = 45.0
    value  = 5e6 * 300 / 1e7 = 150.0 Cr
    wk52   = (300-250)/(320-250)*100 = 71.4286 -> 71.4, penalty 1.0
    sma200 = None (140 < 200) -> factor 1.0
    score  = 65*0.30 + 25 + 45*0.20 + 100*0.15 + (100-71.4286)*0.10
           = 19.5 + 25 + 9 + 15 + 2.857 = 71.357 -> 71.4 (grade A)
    """
    out = _scan(monkeypatch, _bulk(_frame("AAA")), _universe("AAA"))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["symbol"] == "AAA"
    assert row["flip_type"] == "STRONG FLIP"
    assert row["grade"] == "A"
    assert row["confidence"] == "High"
    assert row["prior_del_pct"] == 20.0
    assert row["current_del_pct"] == 65.0
    assert row["del_jump_pp"] == 45.0
    assert row["del50_days"] == 30
    assert row["avg_del_value_cr"] == 150.0
    assert row["flip_consistency"] == 100
    assert row["wk52_pos"] == 71.4
    assert row["close"] == 300.0
    assert row["market_cap_cr"] == 500.0
    assert row["sma_200"] is None
    assert row["sma_200_factor"] == 1.0
    assert row["flip_score"] == 71.4
    assert row["sector"] == "Unknown"


# ---------------------------------------------------------------------------
# Parity: migrated path vs the pre-migration recipe
# ---------------------------------------------------------------------------

PARITY_CASES = {
    "clean": _bulk(_frame("AAA")),
    "many": _bulk(_frame("AAA"), _frame("BBB"), _frame("CCC", steady=True)),
    "blank_52w": _bulk(_frame("AAA", blank_52w=True)),
    "absent_52w": _bulk(_frame("AAA", drop_52w=True)),
    "empty_frame": _bulk(_frame("AAA"), _frame("BTN", 0)),
    "short_history": _bulk(_frame("AAA"), _frame("SHRT", 40)),
}


@pytest.mark.parametrize("case", list(PARITY_CASES), ids=list(PARITY_CASES))
def test_legacy_and_migrated_paths_agree(case, monkeypatch):
    bulk = PARITY_CASES[case]
    universe = _universe(*bulk)
    if case == "many":
        universe = _universe(*bulk, "GONE")

    legacy = _scan(monkeypatch, bulk, universe, route="legacy")
    migrated = _scan(monkeypatch, bulk, universe, route="migrated")

    assert _fingerprint(migrated) == _fingerprint(legacy)
    if not legacy.empty:
        pd.testing.assert_frame_equal(migrated, legacy)


def test_candidate_ordering_is_score_descending(monkeypatch):
    out = _scan(
        monkeypatch,
        _bulk(_frame("AAA"), _frame("BBB"), _frame("CCC")),
        _universe("AAA", "BBB", "CCC"),
    )
    assert list(out["symbol"]) == sorted(
        out["symbol"], key=lambda s: -out.loc[out["symbol"] == s, "flip_score"].iloc[0]
    )


# ---------------------------------------------------------------------------
# Hazard A: missing columns
# ---------------------------------------------------------------------------


def test_absent_optional_columns_match_all_nan_columns(monkeypatch):
    """`rows_for_symbol` pads absent columns with None; the frame accessor drops
    them. Both must mean "no 52-week level" and fall back to high/low.

    The populated frame is deliberately *not* equal: it uses the real 52-week
    band (71.4) while both degraded shapes use the in-window high/low (50.0).
    """
    present = _scan(monkeypatch, _bulk(_frame("AAA")), _universe("AAA"))
    blank = _scan(monkeypatch, _bulk(_frame("AAA", blank_52w=True)), _universe("AAA"))
    absent = _scan(monkeypatch, _bulk(_frame("AAA", drop_52w=True)), _universe("AAA"))

    pd.testing.assert_frame_equal(absent, blank)
    assert present.iloc[0]["wk52_pos"] == 71.4
    assert absent.iloc[0]["wk52_pos"] == 50.0


def test_absent_optional_columns_do_not_raise(monkeypatch):
    """A drifted schema must not turn into a logged exception per symbol."""
    out = _scan(monkeypatch, _bulk(_frame("AAA", drop_52w=True)), _universe("AAA"))
    assert len(out) == 1
    # high.max()=310, low.min()=290 => (300-290)/(310-290)*100 = 50.0
    assert out.iloc[0]["wk52_pos"] == 50.0


# ---------------------------------------------------------------------------
# Hazard B: index labels, dtypes, independence
# ---------------------------------------------------------------------------


def test_frames_are_date_sorted_with_a_zero_based_index(monkeypatch):
    scanner = LiquidityFlipDetector()
    scanner._bulk_data = _bulk(_frame("AAA"))
    min_date = "2025-10-31"
    frame = scanner._get_tech_df("AAA", min_date, AS_ON)

    assert list(frame.index) == list(range(len(frame)))
    assert frame["date"].is_monotonic_increasing
    assert pd.api.types.is_datetime64_any_dtype(frame["date"])
    assert list(frame.columns) == list(COLUMNS_12)


def test_index_labels_do_not_reach_the_output(monkeypatch):
    """A non-zero, non-contiguous index must not change any candidate field."""
    bulk = _bulk(_frame("AAA"))
    # Re-label the snapshot frame the way a narrower slice would.
    bulk["AAA"].index = np.arange(500, 500 + len(bulk["AAA"]))

    relabelled = _scan(monkeypatch, bulk, _universe("AAA"))
    plain = _scan(monkeypatch, _bulk(_frame("AAA")), _universe("AAA"))

    pd.testing.assert_frame_equal(relabelled, plain)


def test_absent_symbol_and_empty_frame_yield_no_candidate(monkeypatch):
    out = _scan(
        monkeypatch,
        _bulk(_frame("AAA"), _frame("BTN", 0)),
        _universe("AAA", "BTN", "GONE"),
    )
    assert list(out["symbol"]) == ["AAA"]


def test_short_history_is_skipped(monkeypatch):
    out = _scan(
        monkeypatch,
        _bulk(_frame("AAA"), _frame("SHRT", 40)),
        _universe("AAA", "SHRT"),
    )
    assert list(out["symbol"]) == ["AAA"]


def test_whitespace_padded_universe_symbol_is_found(monkeypatch):
    out = _scan(monkeypatch, _bulk(_frame("AAA")), _universe(" AAA "))
    assert list(out["symbol"]) == ["AAA"]


def test_returned_frame_is_an_independent_copy(monkeypatch):
    scanner = LiquidityFlipDetector()
    bulk = _bulk(_frame("AAA"))
    scanner._bulk_data = bulk
    frame = scanner._get_tech_df("AAA", "2025-10-31", AS_ON)
    frame.loc[frame.index[0], "close"] = -1.0

    assert bulk["AAA"]["close"].iloc[0] == 300.0
    again = scanner._get_tech_df("AAA", "2025-10-31", AS_ON)
    assert again["close"].iloc[0] == 300.0


def test_every_universe_symbol_is_visited_once(monkeypatch):
    seen: list[str] = []
    real = LiquidityFlipDetector._get_tech_df

    def recording(self, symbol, *args, **kwargs):
        seen.append(symbol)
        return real(self, symbol, *args, **kwargs)

    monkeypatch.setattr(LiquidityFlipDetector, "_get_tech_df", recording)
    _scan(
        monkeypatch,
        _bulk(_frame("AAA"), _frame("BBB"), _frame("CCC")),
        _universe("AAA", "BBB", "CCC"),
    )

    assert seen == ["AAA", "BBB", "CCC"]


def test_empty_columns_are_still_named_like_the_tuple_path(monkeypatch):
    """The frame handed to the scan loop keeps the 12-column contract."""
    scanner = LiquidityFlipDetector()
    scanner._bulk_data = _bulk(_frame("AAA", drop_52w=True))
    frame = scanner._get_tech_df("AAA", "2025-10-31", AS_ON)
    assert list(frame.columns) == list(COLUMNS_12)
