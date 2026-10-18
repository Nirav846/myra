"""Phase 22 — the canonical adjusted-price read path (decision F1).

Phase 21 established, from local evidence alone, that ``technical_data`` carries
corporate-action-**adjusted**, back-adjusted prices (at each ex-date the parquet
OHLCV cache drops by exactly the action factor while the database does not), and
that ``LibrarianCore.get_ohlcv`` served the *cache* with a database delta
appended — one series with two price bases. It measured the consequence: a moving
average spanning an ex-date came out roughly doubled.

Phase 22 closes that seam for historical technical indicators only. These tests
pin the resulting contract:

A. a synthetic series crossing an adjustment boundary produces no artificial jump;
B. the canonical path is chosen, and a cache that deliberately disagrees with
   ``technical_data`` cannot leak into an indicator series;
C. missing symbols, missing dates, NULL OHLC and short history fail or read as
   unavailable rather than silently switching price basis;
D. volume and delivery keep their established semantics and are never rescaled;
E. backtest / rupee-level consumers are untouched by this change;
F. existing behaviour (TriggerScanner, cache-first consumers) is preserved.

Everything runs against temporary databases. No live market data, no network.
"""

from __future__ import annotations

import inspect
import os
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

import myra_app.constants as constants
from myra_app.constants import SOURCE_TECHNICAL_DATA
from myra_app.librarian_intelligence import LibrarianIntelligenceMixin

#: The synthetic corporate action. Prices before it are restated (adjusted
#: basis); the unadjusted series would instead show a 50% drop on the ex-date.
EX_INDEX = 120


# ─── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolate ``DATA_DIR`` and ``DB_DIR``; return a small builder API."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(constants, "DB_DIR", str(db_dir))
    return SimpleNamespace(
        root=tmp_path,
        data_dir=data_dir,
        cache_dir=data_dir / "ohlcv_cache",
        db_path=db_dir / "tech.db",
    )


def _adjusted_closes(rows: int = 260) -> list[float]:
    """A smooth series that stays continuous across the ex-date.

    The unadjusted twin of this series is :func:`_unadjusted_closes`, which
    doubles everything before the ex-date - exactly what a restated history
    removes.
    """
    return [100.0 + i * 0.5 for i in range(rows)]


def _unadjusted_closes(rows: int = 260) -> list[float]:
    """The same market before adjustment: pre-ex prices are twice as high."""
    return [
        c * 2.0 if i < EX_INDEX else c for i, c in enumerate(_adjusted_closes(rows))
    ]


def _write_technical(
    path,
    symbol: str,
    closes,
    *,
    volume=100000.0,
    delivery=45000.0,
    rows: int | None = None,
    null_close_at=None,
    skip_dates=(),
):
    """Create a minimal ``technical_data`` table holding one adjusted series."""
    n = rows if rows is not None else len(closes)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE technical_data (symbol TEXT, date TEXT, open REAL, high REAL,"
            " low REAL, close REAL, volume REAL, delivery REAL, trades REAL, vwap REAL,"
            " delivery_pct REAL, PRIMARY KEY(symbol, date))"
        )
        dates = pd.bdate_range("2024-01-01", periods=n)
        for i, d in enumerate(dates):
            iso = d.date().isoformat()
            if iso in skip_dates:
                continue
            close = None if null_close_at == i else closes[i]
            conn.execute(
                "INSERT OR REPLACE INTO technical_data VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    iso,
                    close,
                    (close + 2.0) if close is not None else None,
                    (close - 2.0) if close is not None else None,
                    close,
                    volume,
                    delivery,
                    1000.0,
                    close,
                    (delivery / volume * 100.0) if volume else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _write_cache(cache_dir, symbol: str, closes):
    """Write a parquet OHLCV cache that deliberately DISAGREES with the DB."""
    os.makedirs(cache_dir, exist_ok=True)
    index = pd.bdate_range("2024-01-01", periods=len(closes), name="date")
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 2.0 for c in closes],
            "Low": [c - 2.0 for c in closes],
            "Close": closes,
            "Volume": [100000.0] * len(closes),
            "delivery_qty": [45000.0] * len(closes),
        },
        index=index,
    )
    path = os.path.join(str(cache_dir), f"{symbol}.parquet")
    frame.to_parquet(path)
    return path


class _StubLibrarian(LibrarianIntelligenceMixin):
    """Runs the real indicator maths against a stubbed data source.

    ``get_ohlcv`` records the ``source`` it was asked for, so a test can assert
    *which* basis the lake chose, not merely what came back.
    """

    read_only = False

    def __init__(self, frame=None, canonical_frame=None, reference_date="2026-12-31"):
        self.loader = SimpleNamespace(
            indicators=SimpleNamespace(save_indicators=lambda *a: None)
        )
        self._frame = frame
        self._canonical = canonical_frame
        self.requested_sources = []
        self.reference_date = reference_date

    def get_active_universe(self):
        return ["AAA"]

    def get_max_price_date(self):
        return self.reference_date

    def get_ohlcv(self, symbol, as_of_date=None, *, source=None):
        self.requested_sources.append(source)
        if source == SOURCE_TECHNICAL_DATA:
            return None if self._canonical is None else self._canonical.copy()
        return None if self._frame is None else self._frame.copy()


def _ohlcv_frame(closes):
    """The column shape the real ``get_ohlcv`` returns from technical_data."""
    index = pd.bdate_range("2024-01-01", periods=len(closes), name="date")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 2.0 for c in closes],
            "Low": [c - 2.0 for c in closes],
            "Close": closes,
            "Volume": [100000.0] * len(closes),
            "Delivery_qty": [45000.0] * len(closes),
            "delivery_pct": [45.0] * len(closes),
        },
        index=index,
    )


# ─── A. corporate-action discontinuity ──────────────────────────────────────


def test_the_synthetic_series_itself_is_continuous_across_the_ex_date():
    """Precondition for A: the adjusted series has no jump to begin with."""
    closes = _adjusted_closes()
    assert abs(closes[EX_INDEX] / closes[EX_INDEX - 1] - 1.0) < 0.01


def test_the_unadjusted_twin_reproduces_the_measured_inflation():
    """Precondition for A: the two bases really do differ by the factor."""
    adjusted = _adjusted_closes()
    unadjusted = _unadjusted_closes()
    # A 50-session mean spanning the ex-date is what Phase 21 measured as ~2x.
    straddle_adjusted = sum(adjusted[EX_INDEX - 49 : EX_INDEX + 1]) / 50
    straddle_unadjusted = sum(unadjusted[EX_INDEX - 49 : EX_INDEX + 1]) / 50
    assert straddle_unadjusted > straddle_adjusted * 1.9


def test_the_lake_does_not_reproduce_the_jump_from_the_unadjusted_cache(sandbox):
    """A: a straddling 50-session mean must follow the adjusted series only.

    The cache holds the unadjusted twin (2x before the ex-date). If the lake
    consulted it, ``sma50`` at the boundary would be the inflated value.
    """
    closes = _adjusted_closes()
    _write_technical(sandbox.db_path, "AAA", closes)
    _write_cache(sandbox.cache_dir, "AAA", _unadjusted_closes())

    lib = _StubLibrarian(
        frame=_ohlcv_frame(_unadjusted_closes()),  # the cache-first answer
        canonical_frame=_ohlcv_frame(closes),  # the canonical answer
    )
    out = lib._compute_symbol_indicators("AAA")

    expected = sum(closes[EX_INDEX - 49 : EX_INDEX + 1]) / 50
    got = out["sma50"].iloc[EX_INDEX]
    assert got == pytest.approx(expected, rel=1e-9)
    inflated = sum(_unadjusted_closes()[EX_INDEX - 49 : EX_INDEX + 1]) / 50
    assert abs(got - inflated) > 1.0, "the unadjusted cache leaked into the series"


# ─── B. source selection ────────────────────────────────────────────────────


def test_the_lake_requests_the_canonical_source(sandbox):
    """B: the read path is chosen explicitly, not left to the default."""
    lib = _StubLibrarian(canonical_frame=_ohlcv_frame(_adjusted_closes()))
    lib._compute_symbol_indicators("AAA")
    assert lib.requested_sources == [SOURCE_TECHNICAL_DATA]


def test_a_disagreeing_cache_cannot_leak_into_the_indicator_series(sandbox):
    """B: even a wildly wrong cache leaves the indicators untouched."""
    closes = _adjusted_closes()
    lib = _StubLibrarian(
        frame=_ohlcv_frame([c * 7.0 for c in closes]),
        canonical_frame=_ohlcv_frame(closes),
    )
    out = lib._compute_symbol_indicators("AAA")
    assert out["close"].iloc[-1] == pytest.approx(closes[-1])
    assert out["sma50"].iloc[-1] == pytest.approx(sum(closes[-50:]) / 50, rel=1e-9)


def test_the_default_read_path_is_unchanged_for_other_consumers(sandbox):
    """B/F: the cache-first default still exists for non-indicator callers."""
    from myra_app.librarian import Librarian

    sig = inspect.signature(Librarian.get_ohlcv)
    assert sig.parameters["source"].default is None
    assert sig.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY


# ─── C. missing data ────────────────────────────────────────────────────────


def test_an_unknown_symbol_is_unavailable_not_fabricated(sandbox):
    """C: no canonical rows -> no indicator frame (never a cache fallback)."""
    lib = _StubLibrarian(
        frame=_ohlcv_frame(_unadjusted_closes()),  # cache has data...
        canonical_frame=None,  # ...the database does not
    )
    assert lib._compute_symbol_indicators("AAA") is None


def test_short_history_yields_no_frame(sandbox):
    """C: below the 30-row floor the symbol is skipped, not zero-filled."""
    lib = _StubLibrarian(canonical_frame=_ohlcv_frame(_adjusted_closes(10)))
    assert lib._compute_symbol_indicators("AAA") is None


def test_a_null_close_does_not_silently_become_a_price(sandbox):
    """C: a NULL observation must not be repaired from another price basis."""
    closes = _adjusted_closes()
    lib = _StubLibrarian(
        frame=_ohlcv_frame(_unadjusted_closes()),
        canonical_frame=_ohlcv_frame(closes),
    )
    out = lib._compute_symbol_indicators("AAA")
    # The canonical series itself is complete here; the guard is that a NULL in
    # the canonical source would surface as NaN rather than a cache value.
    canonical = _ohlcv_frame(closes)
    canonical.iloc[EX_INDEX, canonical.columns.get_loc("Close")] = float("nan")
    lib2 = _StubLibrarian(
        frame=_ohlcv_frame(_unadjusted_closes()), canonical_frame=canonical
    )
    out2 = lib2._compute_symbol_indicators("AAA")
    assert pd.isna(out2["close"].iloc[EX_INDEX])
    assert out["close"].iloc[EX_INDEX] == pytest.approx(closes[EX_INDEX])


def test_missing_dates_are_not_replaced_from_the_cache(sandbox):
    """C: a gap in the canonical series stays a gap."""
    closes = _adjusted_closes()
    gap = pd.bdate_range("2024-01-01", periods=260)[150].date().isoformat()
    lib = _StubLibrarian(
        frame=_ohlcv_frame(_unadjusted_closes()),  # cache is complete
        canonical_frame=_ohlcv_frame(closes).drop(index=gap),
    )
    out = lib._compute_symbol_indicators("AAA")
    assert gap not in {d.date().isoformat() for d in out.index}


# ─── D. volume and delivery semantics ───────────────────────────────────────


def test_volume_and_delivery_are_not_rescaled_by_the_adjustment(sandbox):
    """D: a price adjustment must not touch share counts or delivery."""
    lib = _StubLibrarian(canonical_frame=_ohlcv_frame(_adjusted_closes()))
    out = lib._compute_symbol_indicators("AAA")
    assert out["volume"].iloc[EX_INDEX - 1] == pytest.approx(100000.0)
    assert out["volume"].iloc[EX_INDEX] == pytest.approx(100000.0)
    assert out["delivery_qty"].iloc[EX_INDEX] == pytest.approx(45000.0)


def test_delivery_pct_matches_quantity_over_volume(sandbox):
    """D: the established derivation (quantity / volume) is preserved."""
    lib = _StubLibrarian(canonical_frame=_ohlcv_frame(_adjusted_closes()))
    out = lib._compute_symbol_indicators("AAA")
    got = out["delivery_pct"].iloc[-1]
    assert got == pytest.approx(45000.0 / 100000.0 * 100.0, rel=1e-6)


# ─── E. backtest / rupee-level safeguards ───────────────────────────────────


def test_the_backtest_does_not_read_through_the_changed_path():
    """E: backtesting reads technical_data directly, so it is unaffected."""
    from pathlib import Path

    src = Path("myra_app/backtest_engine.py").read_text(encoding="utf-8")
    assert "get_ohlcv" not in src, (
        "backtest_engine now routes through get_ohlcv; its execution prices and "
        "rupee sizing would inherit the adjusted basis and must be reviewed"
    )
    assert "FROM technical_data" in src


def test_the_only_indicator_consumer_now_asks_for_the_canonical_source():
    """E: exactly one consumer changed - the indicator path."""
    from pathlib import Path

    intel = Path("myra_app/librarian_intelligence.py").read_text(encoding="utf-8")
    assert "source=SOURCE_TECHNICAL_DATA" in intel
    results = Path("myra_app/results_manager.py").read_text(encoding="utf-8")
    assert "get_ohlcv(symbol)" in results, (
        "the chart consumer must keep the legacy default; its price semantics "
        "(raw levels vs adjusted) were left unchanged and documented"
    )


# ─── F. preserved behaviour ──────────────────────────────────────────────────


def test_indicators_still_compute_normally_on_valid_adjusted_data(sandbox):
    """F: a well-formed adjusted series produces the usual indicator set."""
    closes = _adjusted_closes()
    lib = _StubLibrarian(canonical_frame=_ohlcv_frame(closes))
    out = lib._compute_symbol_indicators("AAA")
    for col in ("sma20", "sma50", "sma200", "rsi", "atr20", "vol_sma50", "vwap"):
        assert col in out.columns, f"{col} disappeared from the indicator frame"
    assert out["sma50"].iloc[-1] == pytest.approx(sum(closes[-50:]) / 50, rel=1e-9)


def test_the_ingestion_source_is_still_the_adjusted_eod2_dataset():
    """F: the basis guarantee rests on this toggle staying on."""
    assert constants.USE_EOD2_DATA is True
    assert constants.SOURCE_TECHNICAL_DATA == "technical_data"


def test_a_rolling_mean_never_equals_the_sessions_own_close(sandbox):
    """F: the Phase 20 D1 shape must stay impossible through this path."""
    lib = _StubLibrarian(canonical_frame=_ohlcv_frame(_adjusted_closes()))
    out = lib._compute_symbol_indicators("AAA")
    warmed = out["sma50"].dropna()
    assert not (warmed == out.loc[warmed.index, "close"]).any()


# ── integration: the REAL Librarian.get_ohlcv, not a stub ───────────────────


def _real_librarian(sandbox):
    """A Librarian whose DB connection and parquet cache both live in tmp.

    ``__init__`` is bypassed deliberately: the point is to exercise the genuine
    ``get_ohlcv`` body against temporary storage without standing up the full
    eight-database sidecar stack.
    """
    from myra_app.data_loader import StockDataLoader
    from myra_app.librarian import Librarian

    lib = Librarian.__new__(Librarian)
    lib._tech_conn = sqlite3.connect(str(sandbox.db_path))
    lib._tech_cols_cache = None
    lib.read_only = False
    lib.loader = StockDataLoader()  # picks up the redirected DATA_DIR
    lib.loader.parquet_dir = str(sandbox.cache_dir)
    os.makedirs(sandbox.cache_dir, exist_ok=True)
    return lib


def test_the_real_get_ohlcv_ignores_a_conflicting_cache_on_the_canonical_path(
    sandbox,
):
    """The method itself must not touch the cache when asked for technical_data.

    A cache holding 2x prices is planted; the canonical read must still return
    the adjusted series, and must not rewrite the cache on the way past.
    """
    closes = _adjusted_closes()
    _write_technical(sandbox.db_path, "AAA", closes)
    cache_path = _write_cache(sandbox.cache_dir, "AAA", _unadjusted_closes())
    before = os.path.getmtime(cache_path)

    lib = _real_librarian(sandbox)
    try:
        out = lib.get_ohlcv("AAA", source=SOURCE_TECHNICAL_DATA)
    finally:
        lib._tech_conn.close()

    assert out is not None
    assert out["Close"].iloc[EX_INDEX] == pytest.approx(closes[EX_INDEX])
    assert out["Close"].iloc[0] == pytest.approx(closes[0])
    assert (
        os.path.getmtime(cache_path) == before
    ), "the canonical read must not write to the parquet cache"


def test_the_real_get_ohlcv_still_serves_the_cache_by_default(sandbox):
    """The legacy default is unchanged for the consumers that rely on it."""
    closes = _adjusted_closes()
    _write_technical(sandbox.db_path, "AAA", closes)
    _write_cache(sandbox.cache_dir, "AAA", _unadjusted_closes())

    lib = _real_librarian(sandbox)
    try:
        out = lib.get_ohlcv("AAA")
    finally:
        lib._tech_conn.close()

    assert out is not None
    # Served from the unadjusted cache - proving the default path is intact.
    assert out["Close"].iloc[EX_INDEX - 1] == pytest.approx(
        _unadjusted_closes()[EX_INDEX - 1]
    )


def test_the_real_canonical_read_is_a_single_basis_across_the_ex_date(sandbox):
    """End to end through the genuine method: no jump at the boundary."""
    closes = _adjusted_closes()
    _write_technical(sandbox.db_path, "AAA", closes)
    _write_cache(sandbox.cache_dir, "AAA", _unadjusted_closes())

    lib = _real_librarian(sandbox)
    try:
        out = lib.get_ohlcv("AAA", source=SOURCE_TECHNICAL_DATA)
    finally:
        lib._tech_conn.close()

    assert out is not None
    assert len(out) == len(closes), "the canonical read must return the full history"
    step = out["Close"].iloc[EX_INDEX] / out["Close"].iloc[EX_INDEX - 1]
    assert abs(step - 1.0) < 0.02, f"unexpected {step:.4f} jump at the ex-date"


def test_the_real_canonical_read_returns_none_for_an_unknown_symbol(sandbox):
    """C, against the real method: absent data reads as unavailable."""
    _write_technical(sandbox.db_path, "AAA", _adjusted_closes())
    lib = _real_librarian(sandbox)
    try:
        assert lib.get_ohlcv("NOPE", source=SOURCE_TECHNICAL_DATA) is None
    finally:
        lib._tech_conn.close()


def test_the_real_canonical_read_orders_and_deduplicates(sandbox):
    """Requirement 4: chronological order and one row per session."""
    closes = _adjusted_closes()
    _write_technical(sandbox.db_path, "AAA", closes)
    lib = _real_librarian(sandbox)
    try:
        out = lib.get_ohlcv("AAA", source=SOURCE_TECHNICAL_DATA)
    finally:
        lib._tech_conn.close()

    assert out is not None
    assert out.index.is_monotonic_increasing
    assert out.index.is_unique


def test_the_real_canonical_read_honours_as_of_date(sandbox):
    """The pre-existing ``as_of_date`` contract still applies."""
    closes = _adjusted_closes()
    _write_technical(sandbox.db_path, "AAA", closes)
    cutoff = pd.bdate_range("2024-01-01", periods=len(closes))[100].date().isoformat()
    lib = _real_librarian(sandbox)
    try:
        out = lib.get_ohlcv("AAA", as_of_date=cutoff, source=SOURCE_TECHNICAL_DATA)
    finally:
        lib._tech_conn.close()

    assert out is not None
    assert out.index.max().date().isoformat() <= cutoff
