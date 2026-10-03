"""Stale-shares refresh: non-equity filter, SME dedupe, local base-twin join.

Covers the three pre-fetch steps in
``myra_app.fundamental_sync.FundamentalSync._refresh_stale_shares_outstanding``:

  * the non-equity predicate (``NON_EQUITY_SQL``) keeps index pseudo-symbols
    such as "NIFTY 50" / "NIFTYBEES" out of the fetch set;
  * the SME/base dedupe makes one Yahoo call per company, not per row;
  * the local join copies shares_outstanding from a base twin that already
    holds a valid Morningstar value, at zero network cost.

Every test runs against a per-test copy of ``tests/fixtures/valuation_fixture.db``
(the rows that make these shapes possible are seeded by
``tests/fixtures/build_fixture_db.py``); no production DB is ever touched.

The fixture is tuned so the stale set is exactly 28 rows / 25 distinct tickers
(3 SME/base collisions) -- test 10 depends on 25 being a multiple of the
progress-log interval.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import types

import pytest
import yfinance as yf

import myra_app.fundamental_sync as fs_module
from myra_app.fundamental_sync import FundamentalSync
from myra_app.symbols import (
    NON_EQUITY_SQL,
    dedupe_tickers,
    is_non_equity_symbol,
    is_sme_symbol,
    strip_sme,
)

# ── seeded shapes (see tests/fixtures/build_fixture_db.py) ───────────────────
_TESTSYM = tuple(f"TESTSYM{i:02d}" for i in range(1, 13))
_JUNK = (
    "FIX JUNK SPACE",
    "FIX JUNK SPACE2",
    "NIFTY JUNK SPACE",
    "NIFTYJUNK1",
    "NIFJUNK1",
)
_PROTECTED = ("ARTEMISMED", "DHANI", "GUJRAFFIA", "SINGERIND")
_SME_FILLERS = tuple(f"FIXSMEM{i:02d}_SME" for i in range(1, 7))
# Stale set as seeded: 28 rows / 25 distinct tickers (3 SME/base collisions).
# The local join runs BEFORE the stale query inside the method and fills 2 of
# those 28 (FIXJOIN1_SME, FIXDIVERGE_SME), so the method's returned `total` is
# 26 -- and those 26 rows still dedupe to the same 25 tickers, which is what
# the progress log must be denominated in.
_SEEDED_STALE_ROWS = 28
_SEEDED_DUPLICATE_ROWS = 3
_POST_JOIN_STALE_ROWS = 26
_DISTINCT_TICKERS = 25

# Mirror of the stale query, so a test can ask "what is still stale?" without
# running the fetch loop.  Test 23 keeps the NON_EQUITY_SQL half of this honest.
_STALE_SQL = f"""
SELECT symbol FROM fundamentals
WHERE ( shares_outstanding IS NULL
     OR shares_outstanding = 0
     OR last_fundamental_update IS NULL
     OR last_fundamental_update < date('now', '-90 days') )
  AND NOT {NON_EQUITY_SQL}
"""

_COLUMNS = (
    "shares_outstanding, last_fundamental_update, pe, sector, industry, market_cap"
)


@pytest.fixture
def writable_valuation_db(fixture_db_dir, tmp_path, monkeypatch):
    """Function-scoped copy of the valuation fixture DB.

    Depends on fixture_db_dir explicitly so a missing seed surfaces as the
    conftest pytest.skip rather than a bare FileNotFoundError from copy2.
    The DB_DIR binding in fundamental_sync is captured at import time, so the
    module attribute is what has to be patched.
    """
    src = os.path.join(fixture_db_dir, "myra_valuation.db")
    dst = tmp_path / "myra_valuation.db"
    shutil.copy2(src, dst)
    monkeypatch.setattr(fs_module, "DB_DIR", str(tmp_path))
    yield str(dst)


@pytest.fixture(autouse=True)
def _no_sleeps(monkeypatch):
    """Kill the 0.3s inter-symbol and 2s retry sleeps.

    Replaces the module's own `time` binding for the duration of each test --
    same idiom as the DB_DIR patch, never a global time.sleep patch.  Applied
    to every test here (not just the ones that hit the retry loop) because the
    loop sleeps 0.3s per fetched ticker and each run fetches 25.
    """
    monkeypatch.setattr(fs_module, "time", types.SimpleNamespace(sleep=lambda _s: None))


def _stale_symbols(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute(_STALE_SQL)}
    finally:
        conn.close()


def _row(db_path: str, symbol: str):
    """(shares_outstanding, last_fundamental_update, pe, sector, industry, mcap)"""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            f"SELECT {_COLUMNS} FROM fundamentals WHERE symbol = ?", (symbol,)
        ).fetchone()
    finally:
        conn.close()


def _symbols(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT symbol FROM fundamentals")]
    finally:
        conn.close()


def _today(db_path: str) -> str:
    """SQLite's date('now') -- what the writers actually stamp.

    Not datetime.date.today(): SQLite uses UTC, so the two disagree for the
    ~5.5 hours either side of IST midnight.
    """
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT date('now')").fetchone()[0]
    finally:
        conn.close()


def _run():
    return FundamentalSync()._refresh_stale_shares_outstanding()


def _stub_yfinance(monkeypatch, *, shares=None, raises=(), info=None):
    """Patch yfinance.Ticker (module attribute -- the method imports it locally).

    Returns the list of ".NS" tickers that were fetched, in call order.
    `shares` maps a ".NS" ticker to a share count; `raises` is a set of ".NS"
    tickers (or "*") whose .info raises; `info` is the payload every other
    ticker answers with.
    """
    fetched: list[str] = []
    shares_map = dict(shares or {})
    raise_set = set(raises)
    payload = dict(info or {})

    class _Ticker:
        def __init__(self, symbol):
            self._symbol = symbol

        @property
        def info(self):
            fetched.append(self._symbol)
            if "*" in raise_set or self._symbol in raise_set:
                raise RuntimeError(f"stub network failure for {self._symbol}")
            result = dict(payload)
            if self._symbol in shares_map:
                result["sharesOutstanding"] = shares_map[self._symbol]
            return result

    monkeypatch.setattr(yf, "Ticker", _Ticker)
    return fetched


# ── 1 ────────────────────────────────────────────────────────────────────────
def test_seeded_override_rows_present(writable_valuation_db):
    """The shapes these tests assert on must exist before anything asserts.

    Guards against a STALE fixture -- one that was built before the override
    rows landed, so it still resolves but no longer carries these seeded
    symbols/counts, and every later assertion would fail on missing data rather
    than on behaviour.  A MISSING fixture is not caught here: conftest.py:28
    calls pytest.skip() inside the session-scoped fixture_db_dir, which resolves
    before this body ever runs.
    """
    db = writable_valuation_db
    present = set(_symbols(db))
    assert set(_TESTSYM) <= present
    assert set(_JUNK) <= present
    assert set(_PROTECTED) <= present
    assert set(_SME_FILLERS) <= present
    for stem in ("FIXCASEA", "FIXJOIN1", "FIXJOINC", "FIXDIVERGE"):
        assert {stem, f"{stem}_SME"} <= present, stem
    assert {f"FIXBASEC{i}" for i in range(1, 6)} <= present
    assert {f"FIXBASEB{i}" for i in range(1, 6)} <= present

    # The join filters base.source_ms = 'MORNINGSTAR'; 'UPSTOX' on the SME side
    # and 10 _SME rows in total.
    conn = sqlite3.connect(db)
    try:
        sme = conn.execute(
            "SELECT symbol FROM fundamentals WHERE symbol LIKE '%\\_SME' ESCAPE '\\'"
        ).fetchall()
        bases = conn.execute(
            "SELECT symbol, source_ms FROM fundamentals "
            "WHERE symbol IN ('FIXJOIN1','FIXJOINC','FIXDIVERGE','FIXCASEA')"
        ).fetchall()
    finally:
        conn.close()
    assert len(sme) == 10, [s[0] for s in sme]
    assert all(src == "MORNINGSTAR" for _, src in bases), bases


# ── 2 ────────────────────────────────────────────────────────────────────────
def test_non_equity_symbols_excluded_from_stale_query(writable_valuation_db):
    """Index pseudo-symbols have no Yahoo ticker -- they must never be fetched."""
    stale = _stale_symbols(writable_valuation_db)
    assert set(_JUNK).isdisjoint(stale)
    # ...and they really are in the table, i.e. excluded rather than absent.
    present = set(_symbols(writable_valuation_db))
    assert set(_JUNK) <= present


# ── 3 ────────────────────────────────────────────────────────────────────────
def test_non_equity_predicate_covers_all_three_branches(writable_valuation_db):
    """space / NIFTY%-no-space / NIF%-no-space each need an isolated row.

    The NIF%-only rows have no live analogue in the production DB -- they
    exist so this branch is exercised rather than assumed.
    """
    conn = sqlite3.connect(writable_valuation_db)
    try:
        branches = conn.execute(
            "SELECT symbol, (symbol LIKE '% %'), (symbol LIKE 'NIFTY%'), "
            "(symbol LIKE 'NIF%') FROM fundamentals"
        ).fetchall()
        excluded = {
            sym
            for sym, flag in conn.execute(
                f"SELECT symbol, {NON_EQUITY_SQL} FROM fundamentals"
            )
            if flag
        }
    finally:
        conn.close()

    space = {sym for sym, sp, _, _ in branches if sp}
    nifty = {sym for sym, _, ny, _ in branches if ny}
    nif = {sym for sym, _, _, nf in branches if nf}
    # Each branch has at least one row that ONLY it matches -- otherwise a
    # branch could be dropped from the predicate with the fixture still green.
    assert {"FIX JUNK SPACE", "FIX JUNK SPACE2"} <= space - nifty - nif
    assert {"NIFTYJUNK1"} <= nifty - space
    assert {"NIFJUNK1"} <= nif - space
    assert space | nifty | nif <= excluded
    assert set(_JUNK) <= excluded


# ── 4 ────────────────────────────────────────────────────────────────────────
def test_normal_symbols_retained(writable_valuation_db):
    """Ordinary equities must survive the filter (it only drops non-equities)."""
    stale = _stale_symbols(writable_valuation_db)
    missing = set(_TESTSYM) - stale
    assert not missing, f"ordinary equities wrongly filtered out: {sorted(missing)}"


# ── 5 ────────────────────────────────────────────────────────────────────────
def test_protected_upstox_equities_retained(writable_valuation_db):
    """Real UPSTOX equities with a null share count are stale, not junk."""
    db = writable_valuation_db
    stale = _stale_symbols(db)
    for symbol in _PROTECTED:
        assert symbol in stale, symbol
        shares, lfu, pe, sector, _, _ = _row(db, symbol)
        assert shares is None, symbol
        assert pe is not None and sector is not None, symbol
        assert lfu is not None, symbol


# ── 6 ────────────────────────────────────────────────────────────────────────
def test_sme_and_base_row_strip_to_same_ticker():
    assert strip_sme("FIXPAIR1_SME") == "FIXPAIR1"
    assert strip_sme("FIXPAIR1") == "FIXPAIR1"


# ── 7 ────────────────────────────────────────────────────────────────────────
def test_dedupe_collapses_sme_base_pair_to_one_fetch(
    writable_valuation_db, monkeypatch
):
    """FIXJOINC + FIXJOINC_SME are both stale and are one company."""
    fetched = _stub_yfinance(monkeypatch, shares={"FIXJOINC.NS": 7_700_000})
    result = _run()
    assert result["total"] == _POST_JOIN_STALE_ROWS
    assert fetched.count("FIXJOINC.NS") == 1, fetched
    assert "FIXJOINC_SME.NS" not in fetched
    assert len(fetched) == _DISTINCT_TICKERS


# ── 8 ────────────────────────────────────────────────────────────────────────
def test_dedupe_preserves_raw_total():
    """`.total` is the raw input length, NOT len(.order).

    The input must contain a collision, otherwise this passes even when
    .total is computed as len(.order) and the field is silently wrong.
    """
    symbols = ["FIXPAIR1_SME", "FIXPAIR1", "FIXSOLO"]
    deduped = dedupe_tickers(symbols)
    assert deduped.total == len(symbols) == 3
    assert len(deduped.order) == 2
    assert deduped.total != len(deduped.order)


# ── 9 ────────────────────────────────────────────────────────────────────────
def test_dedupe_reports_duplicate_count(writable_valuation_db):
    symbols = ["AAA_SME", "AAA", "BBB_SME", "BBB", "CCC"]
    deduped = dedupe_tickers(symbols)
    assert deduped.duplicates == 2  # rows skipped
    assert len(deduped.order) == 3
    assert deduped.row_for == {"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"}

    # ...and the same accounting on the seeded stale set: 28 rows -> 25 tickers.
    seeded = dedupe_tickers(sorted(_stale_symbols(writable_valuation_db)))
    assert len(seeded.order) == _DISTINCT_TICKERS
    assert (
        seeded.duplicates
        == _SEEDED_DUPLICATE_ROWS
        == (_SEEDED_STALE_ROWS - _DISTINCT_TICKERS)
    )


# ── 10 ───────────────────────────────────────────────────────────────────────
def test_progress_log_uses_deduped_denominator(
    writable_valuation_db, monkeypatch, caplog
):
    """Progress must count fetches (25), not stale rows (28)."""
    _stub_yfinance(monkeypatch, shares={})
    caplog.set_level(logging.INFO, logger="myra.fundamental_sync")
    result = _run()
    assert result["total"] == _POST_JOIN_STALE_ROWS

    progress = [
        rec.getMessage().split("Progress: ")[1].split(" —")[0]
        for rec in caplog.records
        if "Progress: " in rec.getMessage()
    ]
    assert progress, "no progress line emitted"
    # Only the denominator is asserted, never the final line: the log fires every
    # 25 fetches, so it emits 25/25 for 25 tickers but only 25/26 if the fixture
    # ever grows to 26 -- a correct denominator with an unfired cadence, not a bug.
    # The per-line loop below is what pins the denominator.
    for entry in progress:
        den = int(entry.split("/")[1])
        assert den == _DISTINCT_TICKERS, f"truncated fraction in {progress!r}"


# ── 11 ───────────────────────────────────────────────────────────────────────
def test_local_join_fills_sme_shares_from_base_twin(writable_valuation_db, monkeypatch):
    db = writable_valuation_db
    assert _row(db, "FIXJOIN1_SME")[0] is None, "fixture must start with a null SME row"
    fetched = _stub_yfinance(monkeypatch, shares={})
    _run()
    base_shares = _row(db, "FIXJOIN1")[0]
    sme_shares = _row(db, "FIXJOIN1_SME")[0]
    assert sme_shares == base_shares == 9_500_000
    assert "FIXJOIN1_SME" not in fetched  # zero network cost for the fill


# ── 12 ───────────────────────────────────────────────────────────────────────
def test_local_join_sets_last_fundamental_update(writable_valuation_db, monkeypatch):
    _stub_yfinance(monkeypatch, shares={})
    _run()
    lfu = _row(writable_valuation_db, "FIXJOIN1_SME")[1]
    assert lfu == _today(writable_valuation_db)


# ── 13 ───────────────────────────────────────────────────────────────────────
def test_local_join_is_idempotent(writable_valuation_db, monkeypatch, caplog):
    """Second run must match zero rows -- no re-fill, no value churn."""
    _stub_yfinance(monkeypatch, shares={})
    _run()
    after_first = _row(writable_valuation_db, "FIXJOIN1_SME")
    assert after_first[0] == 9_500_000, "first run must actually fill the row"

    caplog.clear()
    caplog.set_level(logging.INFO, logger="myra.fundamental_sync")
    _run()
    after_second = _row(writable_valuation_db, "FIXJOIN1_SME")

    assert after_second == after_first
    assert not [
        rec.getMessage()
        for rec in caplog.records
        if "SME rows from their base" in rec.getMessage()
    ], "second run re-ran the local join"


# ── 14 ───────────────────────────────────────────────────────────────────────
def test_local_join_skips_base_twin_with_null_shares(
    writable_valuation_db, monkeypatch
):
    """FIXJOINC's base has no share count -- nothing to copy."""
    _stub_yfinance(monkeypatch, shares={})
    _run()
    assert _row(writable_valuation_db, "FIXJOINC")[0] is None
    assert _row(writable_valuation_db, "FIXJOINC_SME")[0] is None


# ── 15 ───────────────────────────────────────────────────────────────────────
def test_local_join_does_not_copy_pe_sector_industry_mcap(
    writable_valuation_db, monkeypatch
):
    """Only shares_outstanding and last_fundamental_update may propagate."""
    _stub_yfinance(monkeypatch, shares={})
    _run()
    base = _row(writable_valuation_db, "FIXDIVERGE")
    sme = _row(writable_valuation_db, "FIXDIVERGE_SME")
    assert sme[0] == base[0] == 3_300_000  # shares copied
    for label, got, want in zip(
        ("pe", "sector", "industry", "market_cap"), sme[2:], base[2:]
    ):
        assert got != want, f"{label} leaked from the base twin"
    assert sme[2:] == (99.9, "Energy", "Renewables", 2.2e9)


# ── 16 ───────────────────────────────────────────────────────────────────────
def test_local_join_skipped_when_writers_disabled(writable_valuation_db, monkeypatch):
    """The join inherits the writer gate -- it must not run behind it."""
    monkeypatch.setattr(fs_module, "DISABLE_FUNDAMENTAL_WRITERS", True)
    fetched = _stub_yfinance(monkeypatch, shares={"FIXJOIN1.NS": 1.0})
    result = _run()
    assert result == {"updated": 0, "total": 0, "skipped": "flag_disabled"}
    assert fetched == []
    assert _row(writable_valuation_db, "FIXJOIN1_SME")[0] is None


# ── 17 ───────────────────────────────────────────────────────────────────────
def test_guard_raises_when_every_ticker_errors(writable_valuation_db, monkeypatch):
    """The guard is retained: yfinance unreachable must still be loud."""
    _stub_yfinance(monkeypatch, raises="*")
    with pytest.raises(RuntimeError, match="25 tickers"):
        _run()


# ── 18 ───────────────────────────────────────────────────────────────────────
def test_guard_silent_when_yfinance_answers_without_shares(
    writable_valuation_db, monkeypatch
):
    """REGRESSION GUARD FOR THE WHOLE CHANGE -- do not weaken or skip this.

    A row that yfinance answers for but that carries no sharesOutstanding must
    NOT count as an error.  Observed on three sampled symbols in the live
    cohort: quoteType comes back 'NONE' with populated .info and no
    sharesOutstanding (an n=3 inference, not a measurement across the ~252
    fetched tickers).  The loop `break`s on that success, so counting errors
    after the fact from `shares is None` would classify every one of them as a
    failure and raise on every quiet run.  fetch_errored must be incremented
    only in the exception branch, so "yfinance answered, nobody had shares"
    passes silently (updated=0, no raise).
    """
    _stub_yfinance(monkeypatch, info={"quoteType": "NONE", "shortName": "Stub Corp"})
    result = _run()
    assert result == {"updated": 0, "total": _POST_JOIN_STALE_ROWS}


# ── 19 ───────────────────────────────────────────────────────────────────────
def test_dedupe_writes_to_base_row_when_both_stale(writable_valuation_db, monkeypatch):
    """Case C: both rows stale, base twin preferred as the write target."""
    _stub_yfinance(monkeypatch, shares={"FIXJOINC.NS": 7_700_000})
    _run()
    base = _row(writable_valuation_db, "FIXJOINC")
    assert base[0] == 7_700_000
    assert base[1] == _today(writable_valuation_db)


# ── 20 ───────────────────────────────────────────────────────────────────────
def test_dedupe_other_row_remains_stale(writable_valuation_db, monkeypatch):
    """The SME sibling is not written, so it stays stale for the next run."""
    db = writable_valuation_db
    _stub_yfinance(monkeypatch, shares={"FIXJOINC.NS": 7_700_000})
    _run()
    sme = _row(db, "FIXJOINC_SME")
    assert sme[0] is None
    assert sme[1] == "2020-01-01"
    assert "FIXJOINC_SME" in _stale_symbols(db)


# ── 21 ───────────────────────────────────────────────────────────────────────
def test_sme_filled_from_base_on_second_run_without_fetch(
    writable_valuation_db, monkeypatch
):
    """Prefer-non-SME makes Case C converge in ONE fetch instead of two.

    Run 1 writes the base row (its preferred target); run 2's local join sees
    the base now holds a valid Morningstar value, fills the SME row, and the
    pair never reaches yfinance again.
    """
    db = writable_valuation_db
    fetched = _stub_yfinance(monkeypatch, shares={"FIXJOINC.NS": 7_700_000})
    _run()
    assert fetched.count("FIXJOINC.NS") == 1
    assert _row(db, "FIXJOINC_SME")[0] is None  # run 1 could not fill it

    _run()
    assert fetched.count("FIXJOINC.NS") == 1, f"pair fetched twice: {fetched}"
    assert _row(db, "FIXJOINC_SME")[0] == 7_700_000
    stale = _stale_symbols(db)
    assert "FIXJOINC" not in stale
    assert "FIXJOINC_SME" not in stale


# ── 22 ───────────────────────────────────────────────────────────────────────
def test_dedupe_falls_back_to_sme_when_base_not_stale(
    writable_valuation_db, monkeypatch
):
    """Case A: only the SME row is stale, so it must be the write target."""
    db = writable_valuation_db
    stale_before = _stale_symbols(db)
    assert "FIXCASEA_SME" in stale_before
    assert "FIXCASEA" not in stale_before, (
        "base twin must be fresh, otherwise this is a Case C pair and the "
        "fallback is never exercised"
    )

    _stub_yfinance(monkeypatch, shares={"FIXCASEA.NS": 12_345_678})
    _run()
    assert _row(db, "FIXCASEA_SME")[0] == 12_345_678
    assert _row(db, "FIXCASEA_SME")[1] == _today(db)
    base = _row(db, "FIXCASEA")
    assert base[0] == 12_000_000
    assert base[1] == "2099-12-31"  # untouched


# ── 23 ───────────────────────────────────────────────────────────────────────
def test_python_predicate_agrees_with_sql_predicate(writable_valuation_db):
    """DRIFT GUARD for myra_app.symbols.

    is_non_equity_symbol() and NON_EQUITY_SQL encode the same rule in two
    languages.  If either side gains a LIKE the other lacks, this fails here
    instead of the rule silently diverging.  Also pins strip_sme() against the
    replace('_SME','') the join SQL is forced to use: they agree only because
    every _SME suffix is terminal.
    """
    db = writable_valuation_db
    conn = sqlite3.connect(db)
    try:
        for (symbol,) in conn.execute("SELECT symbol FROM fundamentals"):
            sql_flag = conn.execute(
                f"SELECT {NON_EQUITY_SQL} FROM fundamentals WHERE symbol = ?",
                (symbol,),
            ).fetchone()[0]
            assert is_non_equity_symbol(symbol) == bool(sql_flag), symbol
            if is_sme_symbol(symbol):
                assert strip_sme(symbol) == symbol.replace("_SME", ""), symbol
    finally:
        conn.close()

    # strip_sme() uses an endswith() guard while the join SQL is forced to use
    # replace('_SME', ''); the two agree only because the suffix is always
    # terminal, so pin that against a symbol with an interior "_SME" -- the
    # SQL's terminal LIKE, the Python guard and strip_sme() must all treat it
    # as a plain symbol (a replace()-based strip_sme would mangle it).
    assert is_sme_symbol("FIX_SME_BANK") is False
    assert strip_sme("FIX_SME_BANK") == "FIX_SME_BANK"
    conn = sqlite3.connect(db)
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM fundamentals "
                "WHERE symbol = 'FIX_SME_BANK' AND symbol LIKE '%\\_SME' ESCAPE '\\'"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


# ── 24 ───────────────────────────────────────────────────────────────────────
def test_guard_silent_on_partial_failure(writable_valuation_db, monkeypatch):
    """One flaky ticker must not fail the run -- the guard is `==`, not a
    "something went wrong" check.

    A partial outage is a normal transient state for Yahoo: the guard exists to
    catch a TOTAL one, so a run where 1 of 25 tickers raises on both attempts
    must return normally, keep going, and count only the writes that actually
    landed.  `fetch_errored == fetch_attempted` is what pins that -- a guard
    that fired on any error would turn one bad symbol into a failed refresh of
    all 26 rows.

    KNOWN GAP, not fixed here: _stub_yfinance is stateless across calls, so
    "raises on attempt 1, succeeds on retry" -- the exact shape a retry exists
    for -- is not expressible with the current stub.  Re-expressing it means
    restructuring the stub, which is out of scope for this pass.
    """
    db = writable_valuation_db
    fetched = _stub_yfinance(
        monkeypatch, shares={"FIXJOINC.NS": 7_700_000}, raises={"TESTSYM03.NS"}
    )
    result = _run()  # must NOT raise: 1 errored out of 25 is not "all errored"

    # The failing ticker was really fetched, on both attempts -- otherwise the
    # run above was a no-error run and this assertion proves nothing.
    assert "TESTSYM03.NS" in fetched, fetched
    assert fetched.count("TESTSYM03.NS") == 2, fetched
    # The healthy ticker in the same run still got its write: the error did not
    # abort the loop.
    assert _row(db, "FIXJOINC")[0] == 7_700_000
    assert result == {"updated": 1, "total": _POST_JOIN_STALE_ROWS}


# ── 25 ───────────────────────────────────────────────────────────────────────
def test_zero_shares_does_not_clobber_a_valid_value(writable_valuation_db, monkeypatch):
    """AGENTS.md invariant: never overwrite a valid metric with 0/null on a
    failed or partial fetch.

    Yahoo answers some symbols with sharesOutstanding == 0 instead of omitting
    the key, so "key present" is not evidence of a usable share count.  The
    `> 0` half of `if shares and shares > 0:` is the only thing stopping that 0
    reaching the UPDATE; without it the run would stamp a real 0 over a valid
    count AND mark the row fresh, destroying the value and hiding the row from
    every future stale sweep.

    Target is FIXCASEA_SME: stale by date (so it IS fetched, and is its own
    dedupe write target), yet already holding a valid share count that has to
    survive the run untouched.
    """
    db = writable_valuation_db
    before = _row(db, "FIXCASEA_SME")
    assert (
        before[0] is not None and before[0] > 0
    ), "fixture must start with a valid value, else there is nothing to protect"

    fetched = _stub_yfinance(monkeypatch, shares={"FIXCASEA.NS": 0})
    result = _run()

    # Proves the row was actually on the wire -- a non-fetch would make every
    # assertion below pass for free.
    assert "FIXCASEA.NS" in fetched, fetched
    after = _row(db, "FIXCASEA_SME")
    assert after[0] == before[0], "shares_outstanding was clobbered with 0"
    assert (
        after[1] == before[1]
    ), "row was stamped fresh despite the fetch yielding nothing usable"
    assert result == {"updated": 0, "total": _POST_JOIN_STALE_ROWS}
