"""Symbol-domain and parity tests for selective OHLCV materialization.

Five batch scanners used to ask ``load_ohlcv_for_universe`` for the whole
universe and then read only the symbols their loop visited. They now pass the
universe they iterate, so only those frames are materialized.

These tests pin the contract that makes that safe:

* the argument passed is exactly the stripped loop universe;
* the pre-change path (a full mapping) and the selective path hand every
  visited symbol identical rows, in the same order;
* symbols with no data, empty data, or a short history are still visited and
  still skipped, exactly as before;
* a symbol whose universe entry carries whitespace is still found (the loop
  strips before lookup, so the selector must strip too).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
import pytest

from myra_app.db.bulk_loader import TECH_COLUMNS
from myra_app.strategies.climax_accumulation import ClimaxAccumulationScanner
from myra_app.strategies.darvas_box_scanner import DarvasBoxScanner
from myra_app.strategies.float_exhaustion_scanner import FloatExhaustionScanner
from myra_app.strategies.liquidity_flip_detector import LiquidityFlipDetector
from myra_app.strategies.operator_fingerprint_scanner import (
    OperatorFingerprintScanner,
)

AS_ON = "2026-10-16"

# Universe as the scanners see it: "AAA" is fine, "SHORT" has too few bars,
# "GONE" has no rows at all in the source, "EMPTY" exists but carries no bars.
UNIVERSE = ["AAA", "SHORT", "GONE", "EMPTY"]
# The source itself is wider than the universe: "EXTRA" exists in the data but
# no scanner in this test iterates it, which is exactly what selective
# materialization is allowed to skip.
BULK_SYMBOLS = ["AAA", "SHORT", "EMPTY", "EXTRA"]


def _frame(symbol: str, n_rows: int) -> pd.DataFrame:
    end = datetime.strptime(AS_ON, "%Y-%m-%d")
    dates = [
        (end - timedelta(days=n_rows - 1 - i)).strftime("%Y-%m-%d")
        for i in range(n_rows)
    ]
    close = [100.0 + i for i in range(n_rows)]
    data = {
        "symbol": [symbol] * n_rows,
        "date": dates,
        "open": [c - 1.0 for c in close],
        "high": [c + 2.0 for c in close],
        "low": [c - 2.0 for c in close],
        "close": close,
        "volume": [1000 + i for i in range(n_rows)],
        "delivery": [500 + i for i in range(n_rows)],
        "delivery_pct": [40.0] * n_rows,
        "nifty_outperformance_score": [0.0] * n_rows,
        "sma_50": [c - 5.0 for c in close],
        "high_52w": [c + 10.0 for c in close],
        "low_52w": [c - 10.0 for c in close],
        "swing_low": [c - 15.0 for c in close],
    }
    ordered = ["symbol", *TECH_COLUMNS]
    return pd.DataFrame({c: data[c] for c in ordered})


FRAMES = {
    "AAA": _frame("AAA", 140),
    "SHORT": _frame("SHORT", 6),
    "EMPTY": _frame("EMPTY", 0),
    "EXTRA": _frame("EXTRA", 140),
}


class RecordingLoader:
    """Stands in for ``load_ohlcv_for_universe`` inside a scanner module.

    ``reference`` mode reproduces the pre-change call (no symbol list, so the
    whole universe is materialized); ``selective`` mode honours the list the
    scanner now passes.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.calls: list[list[str] | None] = []

    def __call__(self, start_date, end_date=None, symbols=None):
        self.calls.append(None if not symbols else list(symbols))
        wanted = (
            BULK_SYMBOLS if self.mode == "reference" else list(symbols or BULK_SYMBOLS)
        )
        return {s: FRAMES[s].copy() for s in wanted if s in FRAMES}

    @property
    def materialized(self) -> set[str]:
        if self.mode == "reference":
            return set(BULK_SYMBOLS) & set(FRAMES)
        return set(self.calls[-1] or ()) & set(FRAMES)


def _universe_rows(arity: int, universe=UNIVERSE) -> list[tuple]:
    rows = []
    for symbol in universe:
        if arity == 2:
            rows.append((symbol, 500.0))
        elif arity == 3:
            rows.append((symbol, 500.0, 40.0))
        elif arity == 4:
            rows.append((symbol, 500.0, 40.0, 200.0))
        elif arity == 6:
            rows.append((symbol, 500.0, 40.0, 30.0, 30.0, 40.0))
        else:  # pragma: no cover - guard against a silent shape drift
            raise AssertionError(f"unexpected universe arity {arity}")
    return rows


class ScannerCase:
    def __init__(self, name, module, factory, arity, run):
        self.name = name
        self.module = module
        self.factory = factory
        self.arity = arity
        self.run = run


def _run_scan(scanner):
    return scanner.scan(as_on_date=AS_ON)


CASES = [
    ScannerCase(
        "liquidity_flip",
        __import__("myra_app.strategies.liquidity_flip_detector", fromlist=["x"]),
        LiquidityFlipDetector,
        3,
        _run_scan,
    ),
    ScannerCase(
        "operator_fingerprint",
        __import__("myra_app.strategies.operator_fingerprint_scanner", fromlist=["x"]),
        OperatorFingerprintScanner,
        3,
        _run_scan,
    ),
    ScannerCase(
        "float_exhaustion",
        __import__("myra_app.strategies.float_exhaustion_scanner", fromlist=["x"]),
        FloatExhaustionScanner,
        6,
        _run_scan,
    ),
    ScannerCase(
        "climax_accumulation",
        __import__("myra_app.strategies.climax_accumulation", fromlist=["x"]),
        ClimaxAccumulationScanner,
        2,
        _run_scan,
    ),
    ScannerCase(
        "darvas_box",
        __import__("myra_app.strategies.darvas_box_scanner", fromlist=["x"]),
        DarvasBoxScanner,
        4,
        _run_scan,
    ),
]

CASE_IDS = [c.name for c in CASES]


def _scan_with(case: ScannerCase, mode: str, monkeypatch, universe=UNIVERSE):
    """Run one scan, returning (loader, tech-call log, output)."""
    scanner = case.factory()
    loader = RecordingLoader(mode)
    monkeypatch.setattr(case.module, "load_ohlcv_for_universe", loader)
    monkeypatch.setattr(
        case.factory, "_get_universe", lambda self: _universe_rows(case.arity, universe)
    )
    # Keep the sector-map probe hermetic: an in-memory DB has no tables, so the
    # scanners' own try/except leaves the map empty, exactly as on a failed read.
    monkeypatch.setattr(case.factory, "_db_path", lambda self, key: ":memory:")

    seen: list[tuple] = []
    if hasattr(scanner, "_get_tech_df"):
        # Frame consumers (Phase 41 onwards) read through the accessor.
        real_df = scanner._get_tech_df

        def recording_df(symbol, min_date, max_date=None):
            df = real_df(symbol, min_date, max_date)
            seen.append((symbol, len(df), tuple(df.iloc[0]) if len(df) else None))
            return df

        monkeypatch.setattr(scanner, "_get_tech_df", recording_df)
    else:
        real = scanner._get_tech_data

        def recording(symbol, min_date, max_date=None):
            rows = real(symbol, min_date, max_date)
            seen.append((symbol, len(rows), tuple(rows[0]) if rows else None))
            return rows

        scanner._get_tech_data = recording
    return loader, seen, case.run(scanner)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_selector_receives_the_stripped_loop_universe(case, monkeypatch):
    loader, _, _ = _scan_with(case, "selective", monkeypatch)

    assert len(loader.calls) == 1, "a scan must load the universe exactly once"
    passed = loader.calls[0]
    assert passed is not None, "the scanner must now pass its universe"
    assert sorted(passed) == sorted({"AAA", "SHORT", "GONE", "EMPTY"})


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_only_requested_symbols_are_materialized(case, monkeypatch):
    loader, _, _ = _scan_with(case, "selective", monkeypatch)

    assert loader.materialized == {"AAA", "SHORT", "EMPTY"}
    # "GONE" is iterated but absent from the source: it is requested and yields
    # nothing, so an unknown symbol must not abort the load.
    assert "GONE" in loader.calls[-1]
    # "EXTRA" is in the source but outside the universe, so it is not asked for.
    assert "EXTRA" not in loader.calls[-1]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_selective_path_visits_the_same_symbols_with_the_same_rows(case, monkeypatch):
    ref_loader, ref_seen, ref_out = _scan_with(case, "reference", monkeypatch)
    sel_loader, sel_seen, sel_out = _scan_with(case, "selective", monkeypatch)

    # Same traversal: every symbol visited once, in the same order, with the
    # same row count and the same first row.
    assert ref_seen == sel_seen
    assert [s for s, _, _ in sel_seen] == ["AAA", "SHORT", "GONE", "EMPTY"]
    # Absent / empty symbols yield nothing; real and short histories are still
    # returned in full (the scanners' own row-count guards decide what to keep).
    by_symbol = {symbol: n for symbol, n, _ in sel_seen}
    assert by_symbol["AAA"] > 0
    assert by_symbol["SHORT"] == 6
    assert by_symbol["GONE"] == 0
    assert by_symbol["EMPTY"] == 0

    # And the scanner's own output is unchanged.
    if isinstance(ref_out, pd.DataFrame):
        pd.testing.assert_frame_equal(sel_out, ref_out)
    else:
        assert sel_out == ref_out


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_reference_path_is_the_legacy_full_load(case, monkeypatch):
    """Guard the guard: the reference run must really load everything.

    The reference loader ignores the selector and always returns the whole
    source, so it materializes the out-of-universe "EXTRA" that the selective
    path skips. If that stops being true the parity test above proves nothing.
    """
    ref_loader, _, _ = _scan_with(case, "reference", monkeypatch)

    assert ref_loader.materialized == {"AAA", "SHORT", "EMPTY", "EXTRA"}


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_whitespace_universe_entry_is_still_found(case, monkeypatch):
    """' AAA ' normalises to 'AAA' before lookup and before selection.

    A selector that forwarded the raw entry would ask for a symbol that does
    not exist in the snapshot and silently drop a real one, so this is the
    test that catches a missing ``.strip()``.
    """
    loader, seen, _ = _scan_with(case, "selective", monkeypatch, universe=[" AAA "])

    assert loader.calls[-1] == ["AAA"]
    assert [symbol for symbol, _, _ in seen] == ["AAA"]
    assert seen[0][1] > 0


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_empty_universe_returns_early_without_loading(case, monkeypatch):
    loader = RecordingLoader("selective")
    monkeypatch.setattr(case.module, "load_ohlcv_for_universe", loader)
    monkeypatch.setattr(case.factory, "_get_universe", lambda self: [])
    monkeypatch.setattr(case.factory, "_db_path", lambda self, key: ":memory:")

    out = case.run(case.factory())

    assert loader.calls == []
    if isinstance(out, pd.DataFrame):
        assert out.empty
    else:
        assert out == []
