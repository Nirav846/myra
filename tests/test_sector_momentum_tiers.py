"""Tests for ``compute_sector_momentum_tiers()`` in the Bottom Hunter module.

The function used to read the latest and 126th-most-recent close for every
symbol with one window-function query over the whole ``technical_data`` table,
which forced SQLite to sort ~2,400 rows per symbol to keep two. It now issues
two point lookups per symbol on the ``(symbol, date)`` primary key.

``_reference_tiers`` below is the *old* implementation, kept verbatim so the two
can be compared on the same fixture. Every parity assertion is exact dict
equality: both implementations perform the same additions in the same order, so
there is no floating-point tolerance to negotiate.

The fixtures are temporary SQLite files; ``bottom_hunter.DB_DIR`` is redirected
with ``monkeypatch`` so no production database is opened or written.
"""

from __future__ import annotations

import datetime
import os
import sqlite3

import pytest

from myra_app.librarian_core import LibrarianCore
from myra_app.strategies import bottom_hunter as bh

TECH_NAME = LibrarianCore.DB_MAP["technical"]
START = datetime.date(2025, 1, 1)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_db(db_dir, rows, *, unique_dates: bool = True) -> str:
    """Create a minimal ``technical_data`` table in ``db_dir`` and fill it.

    Rebuilding replaces the file, so a test may call the fixture twice to
    compare two datasets against the same ``DB_DIR``.
    """
    os.makedirs(str(db_dir), exist_ok=True)
    path = os.path.join(str(db_dir), TECH_NAME)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    try:
        ddl = "CREATE TABLE technical_data (symbol TEXT, date TEXT, close REAL"
        ddl += ", PRIMARY KEY (symbol, date))" if unique_dates else ")"
        conn.execute(ddl)
        conn.executemany(
            "INSERT INTO technical_data (symbol, date, close) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _series(symbol: str, n: int, base: float = 100.0, step: float = 1.0):
    """``n`` consecutive daily rows whose close rises by ``step`` each day."""
    return [
        (symbol, (START + datetime.timedelta(days=i)).isoformat(), base + i * step)
        for i in range(n)
    ]


def _flat_then_jump(symbol: str, n: int, base: float, roc: float):
    """``n`` rows that are flat at ``base`` except the last, which is ``base*(1+roc)``.

    With ``n >= 126`` the 126th-most-recent close is therefore exactly ``base``
    and the computed ROC is exactly ``roc``.
    """
    rows = _series(symbol, n, base, 0.0)
    last_symbol, last_date, _ = rows[-1]
    rows[-1] = (last_symbol, last_date, base * (1.0 + roc))
    return rows


# ---------------------------------------------------------------------------
# Reference implementation: the pre-optimisation algorithm, verbatim
# ---------------------------------------------------------------------------


def _reference_tiers(symbol_sector: dict[str, str], tech_db: str) -> dict[str, str]:
    sector_rocs: dict[str, list[float]] = {}
    symbols = list(symbol_sector.keys())
    try:
        with sqlite3.connect(tech_db) as conn:
            placeholders = ",".join("?" for _ in symbols)
            rows = conn.execute(
                f"""
                SELECT symbol, close, date, rn FROM (
                    SELECT symbol, close, date,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                    FROM technical_data
                    WHERE symbol IN ({placeholders})
                ) WHERE rn IN (1, 126)
                ORDER BY symbol, rn
                """,
                symbols,
            ).fetchall()

        sym_latest: dict[str, float] = {}
        sym_old: dict[str, float] = {}
        for sym, close, _dt, rn in rows:
            if rn == 1:
                sym_latest[sym] = float(close)
            elif rn == 126:
                sym_old[sym] = float(close)

        for sym in symbols:
            latest_close = sym_latest.get(sym, 0)
            old_close = sym_old.get(sym, 0)
            if old_close <= 0 or latest_close <= 0:
                continue
            roc = (latest_close - old_close) / old_close
            bucket = sector_rocs.setdefault(symbol_sector[sym], [])
            bucket.append(roc)  # noqa: PG-APPEND - mirrors production
    except Exception:  # noqa: BLE001 - identical to the production fallback
        return {}

    sector_avg_roc: dict[str, float] = {}
    for sec, rocs in sector_rocs.items():
        if rocs:
            sector_avg_roc[sec] = sum(rocs) / len(rocs)

    if len(sector_avg_roc) < 5:
        return {}

    ordered = sorted(sector_avg_roc.items(), key=lambda x: x[1], reverse=True)
    n = len(ordered)
    top_cutoff = max(1, int(n * 0.2))
    bottom_cutoff = max(1, int(n * 0.2))
    bottom_start = n - bottom_cutoff

    result: dict[str, str] = {}
    for i, (sec, _) in enumerate(ordered):
        if i < top_cutoff:
            result[sec] = "TOP"
        elif i >= bottom_start:
            result[sec] = "BOTTOM"
        else:
            result[sec] = "MID"
    return result


@pytest.fixture
def tier_env(tmp_path, monkeypatch):
    """Redirect ``bottom_hunter.DB_DIR`` at a temp dir for one test."""

    def build(rows, **kwargs):
        path = _make_db(tmp_path, rows, **kwargs)
        monkeypatch.setattr(bh, "DB_DIR", str(tmp_path))
        return path

    return build


# ---------------------------------------------------------------------------
# The 126-observation offset
# ---------------------------------------------------------------------------


def test_offset_is_exactly_126_rows_back(tier_env):
    """rn=1 / rn=126 must be the last and 126th-from-last rows, not 125/127.

    Five sectors, one symbol each, close = 100 + i, row counts 126/127/130/
    140/200. ROC is 125/(100 + n - 126), so every sector has a distinct value
    and the tiers pin the arithmetic exactly.
    """
    row_counts = {"S126": 126, "S127": 127, "S130": 130, "S140": 140, "S200": 200}
    rows = []
    symbol_sector = {}
    for sector, n in row_counts.items():
        symbol = sector.lower()
        symbol_sector[symbol] = sector
        rows.extend(_series(symbol, n))

    tier_env(rows)

    # ROC for a sector's single symbol, from the 126th-most-recent close.
    expected_roc = {
        sector: (100.0 + n - 1 - (100.0 + n - 126)) / (100.0 + n - 126)
        for sector, n in row_counts.items()
    }
    order = sorted(expected_roc, key=expected_roc.get, reverse=True)
    assert order == ["S126", "S127", "S130", "S140", "S200"]

    assert bh.compute_sector_momentum_tiers(symbol_sector) == {
        "S126": "TOP",
        "S127": "MID",
        "S130": "MID",
        "S140": "MID",
        "S200": "BOTTOM",
    }


def test_exactly_126_rows_is_usable_and_125_is_not(tier_env):
    """126 rows gives an rn=126 row; 125 rows does not exist, so it is skipped."""
    symbol_sector = {"a": "A"} | {f"s{i}": f"S{i}" for i in range(4)}
    rows = _flat_then_jump("a", 126, 100.0, 2.0)
    for i in range(4):
        rows.extend(_flat_then_jump(f"s{i}", 130, 100.0, 0.001 * (i + 1)))
    tier_env(rows)
    with_126 = bh.compute_sector_momentum_tiers(symbol_sector)
    assert with_126["A"] == "TOP"  # roc 2.0 dominates 0.001..0.004

    # Same layout, one row short: symbol "a" can no longer contribute, so its
    # sector never reaches the five-sector minimum and the result is empty.
    symbol_sector_125 = {"a": "A"} | {f"s{i}": f"S{i}" for i in range(4)}
    rows_125 = _flat_then_jump("a", 125, 100.0, 2.0)
    for i in range(4):
        rows_125.extend(_flat_then_jump(f"s{i}", 130, 100.0, 0.001 * (i + 1)))
    tier_env(rows_125)
    assert bh.compute_sector_momentum_tiers(symbol_sector_125) == {}


# ---------------------------------------------------------------------------
# Multi-symbol aggregation and tier cut-offs
# ---------------------------------------------------------------------------


def test_sector_average_is_equal_weight_across_symbols(tier_env):
    """Two symbols per sector: the sector value is the plain mean of their ROCs."""
    rocs = {
        "A": [("a1", 0.60), ("a2", 0.20)],  # mean 0.40 -> TOP
        "B": [("b1", 0.10), ("b2", 0.00)],  # mean 0.05
        "C": [("c1", -0.05), ("c2", -0.05)],  # mean -0.05
        "D": [("d1", -0.30), ("d2", -0.10)],  # mean -0.20 -> BOTTOM
        "E": [("e1", 0.20), ("e2", 0.20)],  # mean 0.20
    }
    symbol_sector, rows = {}, []
    for sector, members in rocs.items():
        for symbol, roc in members:
            symbol_sector[symbol] = sector
            rows.extend(_flat_then_jump(symbol, 130, 100.0, roc))
    tier_env(rows)

    assert bh.compute_sector_momentum_tiers(symbol_sector) == {
        "A": "TOP",
        "B": "MID",
        "E": "MID",
        "C": "MID",
        "D": "BOTTOM",
    }


def test_tier_cutoffs_scale_with_sector_count(tier_env):
    """10 sectors -> int(10*0.2)=2 TOP and 2 BOTTOM, the rest MID."""
    symbol_sector, rows = {}, []
    for i in range(10):
        symbol = f"x{i}"
        symbol_sector[symbol] = f"S{i}"
        rows.extend(_flat_then_jump(symbol, 130, 100.0, (i - 5) / 10.0))
    tier_env(rows)

    tiers = bh.compute_sector_momentum_tiers(symbol_sector)
    assert [k for k, v in tiers.items() if v == "TOP"] == ["S9", "S8"]
    assert [k for k, v in tiers.items() if v == "BOTTOM"] == ["S1", "S0"]
    assert sum(1 for v in tiers.values() if v == "MID") == 6


def test_fewer_than_five_sectors_returns_empty(tier_env):
    symbol_sector, rows = {}, []
    for i in range(4):
        symbol = f"y{i}"
        symbol_sector[symbol] = f"S{i}"
        rows.extend(_flat_then_jump(symbol, 130, 100.0, i / 10.0))
    tier_env(rows)
    assert bh.compute_sector_momentum_tiers(symbol_sector) == {}


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------


def test_symbol_absent_from_technical_data_is_skipped(tier_env):
    symbol_sector = {"present": "P", "ghost": "G"}
    rows = _flat_then_jump("present", 130, 100.0, 0.5)
    for i in range(4):
        rows.extend(_flat_then_jump(f"z{i}", 130, 100.0, i / 10.0))
        symbol_sector[f"z{i}"] = f"Z{i}"
    tier_env(rows)

    tiers = bh.compute_sector_momentum_tiers(symbol_sector)
    assert "G" not in tiers  # only a missing symbol mapped to it
    assert tiers["P"] == "TOP"


def test_null_close_returns_empty_dict(tier_env):
    """A NULL close is the fallback path: float(None) aborts the whole function."""
    symbol_sector = {"main": "A"} | {f"s{i}": f"S{i}" for i in range(4)}
    rows = _flat_then_jump("main", 130, 100.0, 0.5)
    rows[-1] = ("main", rows[-1][1], None)  # NULL close at the latest row
    for i in range(4):
        rows.extend(_flat_then_jump(f"s{i}", 130, 100.0, i / 10.0))
    tier_env(rows)

    assert bh.compute_sector_momentum_tiers(symbol_sector) == {}


def test_null_close_at_historical_row_returns_empty_dict(tier_env):
    symbol_sector = {"main": "A"} | {f"s{i}": f"S{i}" for i in range(4)}
    rows = _flat_then_jump("main", 130, 100.0, 0.5)
    rows[-126] = ("main", rows[-126][1], None)  # the rn=126 row
    for i in range(4):
        rows.extend(_flat_then_jump(f"s{i}", 130, 100.0, i / 10.0))
    tier_env(rows)

    assert bh.compute_sector_momentum_tiers(symbol_sector) == {}


def test_empty_sector_map_returns_empty_dict(tier_env):
    tier_env(_series("a", 130))
    assert bh.compute_sector_momentum_tiers({}) == {}


def test_missing_technical_db_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "DB_DIR", str(tmp_path / "does-not-exist"))
    symbol_sector = {f"s{i}": f"S{i}" for i in range(5)}
    assert bh.compute_sector_momentum_tiers(symbol_sector) == {}


# ---------------------------------------------------------------------------
# Ordering / duplicates
# ---------------------------------------------------------------------------


def test_duplicate_dates_are_rejected_by_the_schema(tier_env):
    """Ties on ``date`` are impossible: (symbol, date) is the primary key.

    This is what makes ``LIMIT 1 OFFSET k`` equivalent to ``ROW_NUMBER()=k+1``
    - both need a total order, and the schema guarantees one.
    """
    path = tier_env(_series("a", 130))
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO technical_data (symbol, date, close) VALUES (?, ?, ?)",
                ("a", _series("a", 1)[0][1], 123.0),
            )


def test_null_date_sorts_identically_in_both_implementations(tier_env):
    """SQLite sorts NULL after every value in DESC; both forms share that order."""
    symbol_sector = {"a": "A"} | {f"s{i}": f"S{i}" for i in range(4)}
    rows = _series("a", 126)
    # Replace the oldest row's date with NULL. In DESC it sorts last, so it
    # becomes the rn=126 row and its close (100.0) is the historical value.
    rows[0] = ("a", None, 100.0)
    for i in range(4):
        rows.extend(_flat_then_jump(f"s{i}", 130, 100.0, (i + 1) / 100.0))
    path = tier_env(rows)

    assert bh.compute_sector_momentum_tiers(symbol_sector) == _reference_tiers(
        symbol_sector, path
    )


def test_rows_outside_the_sector_map_are_ignored(tier_env):
    symbol_sector = {f"s{i}": f"S{i}" for i in range(5)}
    rows = _flat_then_jump("s0", 130, 100.0, 1.0)
    for i in range(1, 5):
        rows.extend(_flat_then_jump(f"s{i}", 130, 100.0, i / 10.0))
    rows.extend(_flat_then_jump("stranger", 130, 100.0, 99.0))
    tier_env(rows)

    tiers = bh.compute_sector_momentum_tiers(symbol_sector)
    assert tiers["S0"] == "TOP"


# ---------------------------------------------------------------------------
# Parity with the pre-optimisation algorithm
# ---------------------------------------------------------------------------


def test_parity_with_reference_on_varied_universe(tier_env):
    """Mixed row counts, missing symbols and repeated closes, one fixture."""
    symbol_sector, rows = {}, []
    counts = [125, 126, 127, 129, 130, 150, 200, 260, 400, 130, 130, 131]
    for i, n in enumerate(counts):
        sector = f"SECTOR{i % 8}"
        symbol = f"SYM{i:02d}"
        symbol_sector[symbol] = sector
        rows.extend(_flat_then_jump(symbol, n, 50.0 + i, (i - 5) / 20.0))
    # A couple of symbols with no rows at all, plus a flat series whose closes
    # repeat (distinct dates, identical prices).
    symbol_sector["EMPTY1"] = "SECTOR0"
    symbol_sector["EMPTY2"] = "SECTOR3"
    rows.extend(_series("FLAT", 140, 100.0, 0.0))
    symbol_sector["FLAT"] = "SECTOR5"

    path = tier_env(rows)

    expected = _reference_tiers(symbol_sector, path)
    actual = bh.compute_sector_momentum_tiers(symbol_sector)
    assert actual == expected
    assert actual  # the fixture is rich enough to produce tiers


def test_parity_holds_for_every_individual_sector_pair(tier_env):
    """Exact equality of the tier dict, not an approximation, across many shapes."""
    symbol_sector, rows = {}, []
    for i in range(30):
        symbol = f"T{i:02d}"
        symbol_sector[symbol] = f"SEC{i % 11}"
        rows.extend(
            _flat_then_jump(symbol, 120 + (i * 7) % 40, 10.0 + i, (i % 17 - 8) / 13.0)
        )
    path = tier_env(rows)

    assert bh.compute_sector_momentum_tiers(symbol_sector) == _reference_tiers(
        symbol_sector, path
    )


def test_does_not_write_to_the_database(tier_env):
    """The function is read-only: the fixture file must be untouched."""
    symbol_sector = {f"s{i}": f"S{i}" for i in range(5)}
    rows = []
    for i in range(5):
        rows.extend(_flat_then_jump(f"s{i}", 130, 100.0, i / 10.0))
    path = tier_env(rows)

    before = os.stat(path).st_mtime_ns
    first = bh.compute_sector_momentum_tiers(symbol_sector)
    second = bh.compute_sector_momentum_tiers(symbol_sector)
    after = os.stat(path).st_mtime_ns

    assert first == second
    assert before == after
