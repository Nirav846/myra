"""Phase 23 — F2 recovery rule and manifest, isolated from live data.

Everything here uses synthetic series and temporary SQLite files. No live
market data, no network, and the live-database guards are untouched.

The rule under test is the one the preflight *validated* rather than assumed:
session-count rolling windows over full history, ``min_periods=1``. See
``myra_app/recovery_f2.py`` and ``docs/PHASE23_F2_RECOVERY_PREFLIGHT.md``.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from myra_app import recovery_f2 as f2

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _history(symbol: str, closes, *, start=None, highs=None, lows=None):
    """One symbol's raw history. Only raw columns - never derived ones.

    The default start is chosen so the series *ends* on the last target session,
    which is what the manifest helpers need in order to cover all 12 dates.
    """
    n = len(closes)
    if start is None:
        end = pd.Timestamp(f2.TARGET_SESSIONS[-1])
        start = (end - pd.tseries.offsets.BDay(n)).date().isoformat()
    dates = pd.bdate_range(start, periods=n)
    highs = highs if highs is not None else [c + 2.0 for c in closes]
    lows = lows if lows is not None else [c - 2.0 for c in closes]
    return pd.DataFrame(
        {"symbol": symbol, "date": dates, "high": highs, "low": lows, "close": closes}
    )


def _rising(n=300, step=0.5, base=100.0):
    return [base + i * step for i in range(n)]


def _sawtooth(n=400, amp=40.0, base=100.0, period=20):
    """A repeating rise/fall.

    The 52-week extremes are therefore *not* the latest high/low, which is what
    makes ``high_52w == high`` a genuine defect rather than a correct value.
    """
    return [base + amp * abs(((i % (period * 2)) - period)) / period for i in range(n)]


def _schema(conn, *, guard=False):
    extra = ", CHECK (sma_200 >= 0)" if guard else ""
    conn.execute(
        "CREATE TABLE technical_data (symbol TEXT, date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, volume INTEGER, delivery INTEGER, delivery_pct REAL,"
        " sma_50 REAL, sma_200 REAL, high_52w REAL, low_52w REAL, delivery_ma_60 REAL"
        f"{extra}, PRIMARY KEY(symbol, date))"
    )


def _seed_degenerate(conn, symbols, sessions, closes):
    """Store the Phase 20 defect shape: derived columns hold the session's own
    raw fields, and ``sma_200`` is NULL."""
    for sym in symbols:
        hist = _history_ending_on(list(sessions), closes, sym)
        hist["iso"] = hist["date"].dt.strftime("%Y-%m-%d")
        for dte in sessions:
            row = hist[hist["iso"] == dte]
            if row.empty:
                continue
            c = float(row["close"].iloc[0])
            conn.execute(
                "INSERT OR REPLACE INTO technical_data VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sym,
                    dte,
                    c,
                    c + 2.0,
                    c - 2.0,
                    c,
                    100000,
                    45000,
                    45.0,
                    c,  # sma_50  == close   (degenerate)
                    None,  # sma_200 NULL       (degenerate)
                    c + 2.0,  # high_52w == high   (degenerate)
                    c - 2.0,  # low_52w  == low    (degenerate)
                    45.0,  # delivery_ma_60 == delivery_pct (degenerate)
                ),
            )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "tech.db")
    conn = sqlite3.connect(path)
    _schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def guarded_db(tmp_path):
    """A technical_data with a CHECK guard, to prove rollback on failure."""
    path = str(tmp_path / "guarded.db")
    conn = sqlite3.connect(path)
    _schema(conn, guard=True)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# the rule itself
# ---------------------------------------------------------------------------


def test_the_rule_equals_a_plain_trailing_window_mean():
    closes = _rising(300)
    out = f2.recompute_frame(_history("AAA", closes))
    assert out["sma_50"].iloc[-1] == pytest.approx(sum(closes[-50:]) / 50)
    assert out["sma_200"].iloc[-1] == pytest.approx(sum(closes[-200:]) / 200)


def test_the_rule_is_warm_up_correct_on_short_series():
    """A symbol with fewer sessions than the window still gets an honest mean."""
    closes = _rising(30)
    out = f2.recompute_frame(_history("AAA", closes))
    assert out["sma_50"].iloc[-1] == pytest.approx(sum(closes) / 30)
    assert out["sma_200"].iloc[-1] == pytest.approx(sum(closes) / 30)


def test_a_rolling_mean_never_equals_its_own_session_on_a_real_series():
    """The Phase 20 D1 shape cannot come back through this rule.

    Once the window is fully warmed (index >= 49), a rising series has a 50-day
    mean strictly below the latest close, so the two must never coincide.
    """
    closes = _rising(300)
    out = f2.recompute_frame(_history("AAA", closes))
    warmed = out.iloc[49:]  # window fully populated
    assert not (warmed["sma_50"] == warmed["close"]).any()


def test_52_week_columns_use_the_high_and_low_streams():
    closes = _rising(300)
    highs = [c + 10.0 for c in closes]
    lows = [c - 7.0 for c in closes]
    out = f2.recompute_frame(_history("AAA", closes, highs=highs, lows=lows))
    assert out["high_52w"].iloc[-1] == pytest.approx(max(highs[-252:]))
    assert out["low_52w"].iloc[-1] == pytest.approx(min(lows[-252:]))


def test_only_raw_columns_are_required():
    """Derived columns are never inputs, so a wrong one cannot self-propagate."""
    df = _history("AAA", _rising(60)).drop(columns=["high", "low"])
    with pytest.raises(ValueError, match="missing required columns"):
        f2.recompute_frame(df)


def test_missing_columns_are_refused_rather_than_defaulted():
    df = _history("AAA", _rising(60))
    df["close"] = None
    out = f2.recompute_frame(df)
    assert out["sma_50"].isna().all()


def test_duplicate_and_unsorted_dates_are_normalised():
    df = _history("AAA", _rising(60))
    df = pd.concat([df, df.tail(1)], ignore_index=True)
    df = df.sample(frac=1.0, random_state=0).reset_index(drop=True)
    out = f2.recompute_frame(df)
    assert len(out) == 60, "the duplicated session must collapse, not double-count"
    assert out["date"].is_monotonic_increasing


def test_an_unparseable_date_is_rejected():
    df = _history("AAA", _rising(60))
    df["date"] = df["date"].astype(object)
    df.loc[0, "date"] = "not-a-date"
    with pytest.raises(ValueError, match="unparseable date"):
        f2.recompute_frame(df)


def test_recomputation_is_deterministic():
    df = _history("AAA", _rising(400))
    a = f2.recompute_frame(df)
    b = f2.recompute_frame(df.sample(frac=1.0, random_state=7))
    pd.testing.assert_series_equal(a["sma_200"], b["sma_200"], check_names=False)


# ---------------------------------------------------------------------------
# the write scope is structurally constrained
# ---------------------------------------------------------------------------


def test_delivery_ma_60_is_excluded_by_construction():
    """No clean reference exists for it, so it must not be writable here."""
    assert "delivery_ma_60" in f2.EXCLUDED_COLUMNS
    assert "delivery_ma_60" not in f2.RECOVERABLE_COLUMNS
    out = f2.recompute_frame(_history("AAA", _rising(60)))
    assert "delivery_ma_60" not in out.columns


def test_only_the_four_validated_columns_are_in_scope():
    assert set(f2.RECOVERABLE_COLUMNS) == {"sma_50", "sma_200", "high_52w", "low_52w"}


def test_the_target_sessions_are_the_twelve_known_sessions():
    assert len(f2.TARGET_SESSIONS) == 12
    assert f2.TARGET_SESSIONS[0] == "2026-08-17"
    assert f2.TARGET_SESSIONS[-1] == "2026-10-13"
    assert list(f2.TARGET_SESSIONS) == sorted(f2.TARGET_SESSIONS), "must be ordered"


# ---------------------------------------------------------------------------
# manifest + plan
# ---------------------------------------------------------------------------


def _history_ending_on(sessions, closes, symbol="AAA"):
    """History whose final rows land exactly on the target session dates.

    The 12 target dates are real trading days but not consecutive, so a plain
    ``bdate_range`` cannot be relied on to contain them.
    """
    n = len(closes)
    leading = pd.bdate_range(
        end=pd.Timestamp(sessions[0]) - pd.Timedelta(days=1), periods=n - len(sessions)
    )
    dates = pd.DatetimeIndex(list(leading) + [pd.Timestamp(d) for d in sessions])
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "high": [c + 2.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
        }
    )


def _manifest_for(symbols=("AAA", "BBB"), sessions=f2.TARGET_SESSIONS, n=400):
    closes = _sawtooth(n)
    history, stored = {}, {}
    for sym in symbols:
        h = _history_ending_on(list(sessions), closes, sym)
        history[sym] = h
        for dte in sessions:
            row = h[h["date"] == pd.Timestamp(dte)]
            if row.empty:
                continue
            c = float(row["close"].iloc[0])
            stored[(sym, dte)] = {
                "sma_50": c,
                "sma_200": None,
                "high_52w": c + 2.0,
                "low_52w": c - 2.0,
            }
    return f2.build_manifest(history, stored, sessions=sessions), closes


def test_the_manifest_targets_exactly_the_degenerate_rows():
    manifest, _ = _manifest_for()
    assert manifest.summary()["total_rows"] == 2 * len(f2.TARGET_SESSIONS)


def test_the_manifest_hash_is_stable_and_order_independent():
    m1, _ = _manifest_for()
    m2, _ = _manifest_for(symbols=("BBB", "AAA"))
    assert m1.target_hash == m2.target_hash, "row order must not change the identity"


def test_the_hash_changes_when_the_target_set_changes():
    m1, _ = _manifest_for()
    m2, _ = _manifest_for(symbols=("AAA", "BBB", "CCC"))
    assert (
        m1.target_hash != m2.target_hash
    ), "a stale manifest must be detectable by hash"


def test_the_manifest_detects_every_degenerate_column():
    summary = _manifest_for()[0].summary()
    for col in f2.RECOVERABLE_COLUMNS:
        assert summary["would_change_per_column"][col] > 0
    assert summary["null_to_value_per_column"]["sma_200"] == summary["total_rows"]


def test_a_stale_manifest_is_rejected_by_the_plan_builder():
    """A row already holding the recomputed value must not be re-planned."""
    iso = f2.TARGET_SESSIONS[-1]
    closes = _sawtooth(400)
    h = _history_ending_on(list(f2.TARGET_SESSIONS), closes)
    good = f2.recompute_frame(h)
    row = good[good["date"] == pd.Timestamp(iso)].iloc[0]
    stored = {("AAA", iso): {c: row[c] for c in f2.RECOVERABLE_COLUMNS}}
    manifest = f2.build_manifest({"AAA": h}, stored, sessions=[iso])
    assert f2.merge_plan(manifest) == [], "an already-correct row must not be rewritten"


# ---------------------------------------------------------------------------
# applying the plan (temporary database only)
# ---------------------------------------------------------------------------


def test_apply_plan_repairs_the_degenerate_shape(db):
    sessions = list(f2.TARGET_SESSIONS)
    # A saw-tooth series, so the 252-session extremes are NOT the latest values:
    # on a monotonic series high_52w==high would be correct, not degenerate.
    closes = _sawtooth(400)
    _seed_degenerate(db, ["AAA"], sessions, closes)
    before = db.execute(
        "SELECT sma_50, sma_200, high_52w, low_52w FROM technical_data WHERE symbol='AAA'"
        " AND date=?",
        (sessions[-1],),
    ).fetchone()
    assert before[0] == before[2] - 2.0, "precondition: the degenerate shape is present"

    plan = _plan_for("AAA", closes, sessions)
    assert plan
    written = f2.apply_plan(db, plan)
    assert written == len(plan)

    row = db.execute(
        "SELECT sma_50, sma_200, high_52w, low_52w, close, high, low FROM technical_data "
        "WHERE symbol='AAA' AND date=?",
        (sessions[-1],),
    ).fetchone()
    assert row[1] is not None, "sma_200 must be populated"
    assert row[0] != row[4], "sma_50 must no longer equal close"
    assert row[2] != row[5], "high_52w must no longer equal the session high"
    assert row[3] != row[6], "low_52w must no longer equal the session low"


def test_apply_plan_is_idempotent(db):
    sessions = list(f2.TARGET_SESSIONS)
    closes = _rising(400)
    _seed_degenerate(db, ["AAA"], sessions, closes)
    f2.apply_plan(db, _plan_for("AAA", closes, sessions))

    snapshot = db.execute(
        "SELECT symbol, date, sma_50, sma_200, high_52w, low_52w FROM technical_data "
        "ORDER BY symbol, date"
    ).fetchall()
    stored2 = {
        (r[0], r[1]): {
            "sma_50": r[2],
            "sma_200": r[3],
            "high_52w": r[4],
            "low_52w": r[5],
        }
        for r in snapshot
    }
    m2 = f2.build_manifest(
        {"AAA": _history_ending_on(list(sessions), closes)}, stored2, sessions=sessions
    )
    assert f2.merge_plan(m2) == [], "a rerun must plan no work"


def _plan_for(sym, closes, sessions):
    h = _history_ending_on(list(sessions), closes, sym)
    stored = {
        (sym, r.date.date().isoformat()): {
            "sma_50": r.close,
            "sma_200": None,
            "high_52w": r.high,
            "low_52w": r.low,
        }
        for r in h.itertuples()
        if r.date.date().isoformat() in set(sessions)
    }
    return f2.merge_plan(f2.build_manifest({sym: h}, stored, sessions=sessions))


def test_a_failed_apply_rolls_back_completely(guarded_db):
    """An interrupted/failed operation must not leave a half-recovered session.

    One statement violates a CHECK constraint part-way through the batch; the
    whole transaction must roll back, leaving the pre-recovery state intact.
    """
    conn = guarded_db
    sessions = list(f2.TARGET_SESSIONS)
    closes = _rising(400)
    _seed_degenerate(conn, ["AAA"], sessions, closes)
    before = conn.execute(
        "SELECT COUNT(*), SUM(COALESCE(sma_200, -1)), SUM(sma_50) "
        "FROM technical_data"
    ).fetchone()

    plan = _plan_for("AAA", closes, sessions)
    assert plan
    # Corrupt one entry so the batch fails part-way through.
    plan = list(plan)
    plan[len(plan) // 2] = plan[len(plan) // 2][:3] + (-1.0,) + plan[len(plan) // 2][4:]

    with pytest.raises(sqlite3.IntegrityError):
        f2.apply_plan(conn, plan)

    after = conn.execute(
        "SELECT COUNT(*), SUM(COALESCE(sma_200, -1)), SUM(sma_50) "
        "FROM technical_data"
    ).fetchone()
    assert after == before, "the transaction must leave nothing behind"


def test_unrelated_rows_are_untouched(db):
    sessions = list(f2.TARGET_SESSIONS)
    closes = _rising(400)
    _seed_degenerate(db, ["AAA"], sessions, closes)
    # A row outside every target session, with its own values.
    db.execute(
        "INSERT OR REPLACE INTO technical_data VALUES "
        "('ZZZ','2026-06-01',1,2,0.5,1.5,10,5,50.0,111.0,222.0,333.0,444.0,55.0)"
    )
    db.commit()
    before = db.execute("SELECT * FROM technical_data WHERE symbol='ZZZ'").fetchone()

    h = _history("AAA", closes)
    stored = {
        ("AAA", r.date.date().isoformat()): {
            "sma_50": r.close,
            "sma_200": None,
            "high_52w": r.high,
            "low_52w": r.low,
        }
        for r in h.itertuples()
        if r.date.date().isoformat() in set(sessions)
    }
    plan = f2.merge_plan(f2.build_manifest({"AAA": h}, stored, sessions=sessions))
    f2.apply_plan(db, plan)
    after = db.execute("SELECT * FROM technical_data WHERE symbol='ZZZ'").fetchone()
    assert after == before


def test_an_empty_plan_writes_nothing(db):
    assert f2.apply_plan(db, []) == 0


# ---------------------------------------------------------------------------
# Phase 22 semantics must survive
# ---------------------------------------------------------------------------


def test_recovery_reads_raw_adjusted_columns_only():
    """The F2 rule must not reintroduce a second price basis.

    It reads exactly the raw columns; nothing pulls from the OHLCV cache, which
    Phase 22 established is unadjusted.
    """
    assert f2.SOURCE_COLUMNS == ("date", "high", "low", "close")
    for col in f2.SOURCE_COLUMNS:
        assert (
            col not in f2.RECOVERABLE_COLUMNS
        ), "a recoverable output must never be an input to its own recovery"
