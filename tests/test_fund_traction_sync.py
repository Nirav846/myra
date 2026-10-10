"""Tests for fund-traction sync failure reporting.

A2: a run that finds zero usable months, or that gets 404s for the months it
expected, must report success=False with a clear message (so the pipeline
writes last_status='failed' / error_message to sync_log) instead of silently
looking up-to-date. A normal complete run must still report success=True.
"""

import sqlite3

import pytest

from myra_app import fund_traction_sync as fts


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def val_db(tmp_path, monkeypatch):
    """Point the sync at a throwaway valuation DB."""
    monkeypatch.setattr(fts, "DB_DIR", str(tmp_path))
    return tmp_path / "myra_valuation.db"


def _stub_probe(monkeypatch, ok_months):
    """Stub HEAD: 200 for the given 'YYYY-MM' months, 404 for all others."""
    ok_names = set()
    for m in ok_months:
        ok_names.add(fts._MONTH_NAMES[int(m.split("-")[1]) - 1])

    def fake_head(url, timeout=5):
        name = url.rsplit("/", 1)[-1].replace("_traction.json", "")
        return _Resp(200 if name in ok_names else 404)

    monkeypatch.setattr(fts.requests, "head", fake_head)


def _seed_last_month(val_db, month):
    """Pre-seed the last-imported marker via the module's own setter."""
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    fts._set_last_imported_month(conn, month)
    conn.close()


def test_404_expected_month_is_a_visible_failure(val_db, monkeypatch):
    """The reported case: july is 200, every other expected month 404s.

    The old code discarded the 404s, filtered july out as already-imported,
    and returned success=True -- a phantom success on a dead upstream.
    """
    _stub_probe(monkeypatch, ok_months=["2026-07"])

    # Pre-seed so that july is already imported -> no new months.
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    conn.execute(
        "INSERT INTO fund_traction (symbol, month, month_end_close, "
        "traction_score) VALUES ('AAA', '2026-07', 100.0, 1.0)"
    )
    conn.commit()
    conn.close()
    _seed_last_month(val_db, "2026-07")

    result = fts.sync_fund_traction()

    assert result["success"] is False
    assert "unavailable upstream" in result["error"]
    assert "404" in result["error"]
    # The message must name the gap, not just say "no data".
    assert "expected month" in result["error"]


def test_no_months_at_all_is_a_failure(val_db, monkeypatch):
    _stub_probe(monkeypatch, ok_months=[])

    result = fts.sync_fund_traction()

    assert result["success"] is False
    assert "unusable" in result["error"]


def test_network_error_is_reported(val_db, monkeypatch):
    def boom(url, timeout=5):
        raise ConnectionError("dns failure")

    monkeypatch.setattr(fts.requests, "head", boom)

    result = fts.sync_fund_traction()

    assert result["success"] is False
    assert "errored" in result["error"]
    assert "dns failure" in result["error"]


def test_complete_run_is_still_success(val_db, monkeypatch):
    """Normal success must be unchanged: every expected month present."""
    expected = fts._expected_months()
    _stub_probe(monkeypatch, ok_months=expected)

    # Only the newest month is "new"; the rest are already imported.
    _seed_last_month(val_db, expected[-2] if len(expected) > 1 else "0000-00")

    def fake_download(url):
        return [{"nse": "AAA", "score": 12.5, "funds": 3}]

    monkeypatch.setattr(fts, "_download_and_parse", fake_download)

    result = fts.sync_fund_traction()

    assert result["success"] is True
    assert result["error"] is None
    assert result["months_synced"] == 1
    assert result["last_month"] == expected[-1]


def test_already_up_to_date_is_success(val_db, monkeypatch):
    """Genuinely current (no missing months, nothing new) stays a success."""
    expected = fts._expected_months()
    _stub_probe(monkeypatch, ok_months=expected)
    _seed_last_month(val_db, expected[-1])

    result = fts.sync_fund_traction()

    assert result["success"] is True
    assert result["last_month"] == expected[-1]


def test_partial_404_imports_reachable_but_fails(val_db, monkeypatch):
    """Reachable months are still imported, but the run is not a success."""
    expected = fts._expected_months()
    newest = expected[-1]
    _stub_probe(monkeypatch, ok_months=[newest])
    _seed_last_month(val_db, expected[-2] if len(expected) > 1 else "0000-00")

    monkeypatch.setattr(
        fts,
        "_download_and_parse",
        lambda url: [{"nse": "AAA", "score": 12.5, "funds": 3}],
    )

    result = fts.sync_fund_traction()

    assert result["success"] is False
    assert result["months_synced"] == 1
    assert result["rows_inserted"] > 0
    assert "incomplete" in result["error"]


def test_min_month_value_unchanged():
    """A2 must not change the import floor."""
    assert fts.MIN_MONTH == "2026-04"


# ── Traction Board read API ─────────────────────────────────────────────────


def _seed_board_db(val_db):
    """Two months of traction + funds + insights, wired via the module's own
    parsers so the board reads exactly what the sync would have written."""
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)

    june = {
        "stock_key": "NAME:acme",
        "name": "Acme Ltd",
        "nse": "ACME",
        "sector": "IT",
        "direction": "increase",
        "score": 10.0,
        "fund_count": 2,
        "new_entry_count": 1,
        "breadth_exit": 0,
        "breadth_active": 2,
        "breadth_hold": 0,
        "funds": [
            {"fund_display_name": "Fund A", "activity": "add", "is_new": True},
            {"fund_display_name": "Fund B", "activity": "add", "is_new": False},
        ],
    }
    july = {
        "stock_key": "NAME:acme",
        "name": "Acme Ltd",
        "nse": "ACME",
        "sector": "IT",
        "direction": "increase",
        "score": 20.0,
        "fund_count": 3,
        "new_entry_count": 0,
        "breadth_exit": 0,
        "breadth_active": 3,
        "breadth_hold": 0,
        "funds": [
            {"fund_display_name": "Fund A", "activity": "add", "is_new": False},
            {"fund_display_name": "Fund B", "activity": "add", "is_new": False},
            {"fund_display_name": "Fund C", "activity": "add", "is_new": True},
        ],
    }
    beta_july = {
        "stock_key": "NAME:beta",
        "name": "Beta Ltd",
        "nse": "BETA",
        "sector": "Banking",
        "direction": "decrease",
        "score": 5.0,
        "fund_count": 2,
        "new_entry_count": 0,
        "breadth_exit": 2,
        "breadth_active": 0,
        "breadth_hold": 0,
        "funds": [
            {"fund_display_name": "Fund A", "activity": "reduce", "is_new": False},
            {"fund_display_name": "Fund C", "activity": "exit", "is_new": False},
        ],
    }
    fts._insert_rows(conn, [june], "2026-06")
    fts._insert_rows(conn, [july, beta_july], "2026-07")

    fts._insert_insights_doc(
        conn,
        {
            "monthId": "2026-07",
            "source": "unit-test",
            "topTraction": [
                {"stockKey": "NAME:acme", "name": "Acme Ltd", "fundCount": 3}
            ],
            "insights": [
                {
                    "id": "ins_01",
                    "headline": "Acme still adding",
                    "action": "monitor",
                    "stockKeys": ["NAME:acme"],
                }
            ],
        },
        "2026-07",
    )
    conn.close()


def test_board_defaults_to_newest_month(val_db):
    _seed_board_db(val_db)
    res = fts.get_traction_board()
    assert res["success"] is True
    assert res["month"] == "2026-07"
    assert res["months"] == ["2026-07", "2026-06"]
    assert res["prior_month"] == "2026-06"
    assert res["count"] == 2  # acme + beta


def test_board_persistence_and_breakdown(val_db):
    _seed_board_db(val_db)
    res = fts.get_traction_board(month="2026-07", include_funds=True)
    acme = next(r for r in res["rows"] if r["symbol"] == "ACME")
    # june was all-adds -> july still-adding
    assert acme["persistence"]["status"] == "still_adding"
    assert len(acme["funds"]) == 3
    assert acme["new_entry_count"] == 1
    assert any(f["fund_name"] == "Fund C" for f in acme["adds"])


def test_board_filters(val_db):
    _seed_board_db(val_db)
    added = fts.get_traction_board(board_filter="added")
    assert {r["symbol"] for r in added["rows"]} == {"ACME"}
    reduced = fts.get_traction_board(board_filter="reduced")
    assert {r["symbol"] for r in reduced["rows"]} == {"BETA"}
    still = fts.get_traction_board(board_filter="still_adding")
    assert {r["symbol"] for r in still["rows"]} == {"ACME"}


def test_board_stats_stable_across_filter(val_db):
    _seed_board_db(val_db)
    all_res = fts.get_traction_board()
    filt = fts.get_traction_board(board_filter="reduced")
    # Stats are computed on the full month set regardless of active filter.
    assert filt["stats"]["total"] == all_res["stats"]["total"] == 2
    assert filt["stats"]["reducing"] == 1
    assert filt["count"] == 1


def test_board_search(val_db):
    _seed_board_db(val_db)
    res = fts.get_traction_board(search="beta")
    assert [r["symbol"] for r in res["rows"]] == ["BETA"]


def test_board_watchlist_roundtrip(val_db):
    _seed_board_db(val_db)
    assert fts.pin_watchlist("NAME:acme")["pinned"] is True
    res = fts.get_traction_board(board_filter="watchlist")
    assert [r["symbol"] for r in res["rows"]] == ["ACME"]
    # idempotent pin
    assert fts.pin_watchlist("NAME:acme")["pinned"] is True
    assert fts.unpin_watchlist("NAME:acme")["unpinned"] is True
    assert fts.get_traction_board(board_filter="watchlist")["count"] == 0


def test_board_empty_db_reports_error(val_db):
    res = fts.get_traction_board()
    assert res["success"] is False
    assert "fund traction sync" in res["error"]


def test_insights_and_top_traction(val_db):
    _seed_board_db(val_db)
    res = fts.get_traction_insights(month="2026-07")
    assert res["success"] is True
    assert res["source"] == "unit-test"
    assert len(res["insights"]) == 1
    assert res["insights"][0]["stock_keys"] == ["NAME:acme"]
    assert len(res["top_traction"]) == 1
    assert res["top_traction"][0]["fund_count"] == 3


# ── Smart gate (has_unsynced_month) ─────────────────────────────────────────


def _freeze_today(monkeypatch, year, month, day):
    """Freeze the sync module's date.today() so the two-month probe is stable."""
    from datetime import date as _date

    class _Frozen(_date):
        @classmethod
        def today(cls):
            return cls(year, month, day)

    monkeypatch.setattr(fts, "date", _Frozen)


def test_gate_reports_new_month(val_db, monkeypatch):
    """A month upstream that is newer than our watermark is 'unsynced'."""
    _freeze_today(monkeypatch, 2026, 10, 9)  # candidates: 2026-09, 2026-10
    _stub_probe(monkeypatch, ok_months=["2026-09"])
    _seed_last_month(val_db, "2026-07")

    assert fts.has_unsynced_month() == ["2026-09"]


def test_gate_empty_when_up_to_date(val_db, monkeypatch):
    """Everything available is already at/below the watermark -> nothing new."""
    _freeze_today(monkeypatch, 2026, 10, 9)
    _stub_probe(monkeypatch, ok_months=["2026-09"])
    _seed_last_month(val_db, "2026-09")

    assert fts.has_unsynced_month() == []


def test_gate_empty_when_upstream_has_nothing_new(val_db, monkeypatch):
    """Neither candidate month is published yet (404) -> nothing new, no error."""
    _freeze_today(monkeypatch, 2026, 10, 9)
    _stub_probe(monkeypatch, ok_months=[])  # both candidates 404
    _seed_last_month(val_db, "2026-07")

    assert fts.has_unsynced_month() == []


def test_gate_probes_only_two_candidate_months(val_db, monkeypatch):
    """The gate must stay cheap: only prev+current month are HEAD-probed."""
    _freeze_today(monkeypatch, 2026, 10, 9)
    probed = []

    def fake_head(url, timeout=5):
        probed.append(url.rsplit("/", 1)[-1])
        return _Resp(404)

    monkeypatch.setattr(fts.requests, "head", fake_head)
    _seed_last_month(val_db, "2026-01")

    fts.has_unsynced_month()

    assert sorted(probed) == ["october_traction.json", "september_traction.json"]


# ── Per-fund breakdown repair ───────────────────────────────────────────────


def _seed_traction_only(val_db, month, symbols=("AAA",)):
    """Traction rows with NO fund_traction_funds rows (the pre-feature state)."""
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    for sym in symbols:
        conn.execute(
            "INSERT OR REPLACE INTO fund_traction (symbol, month, traction_score) "
            "VALUES (?, ?, 1.0)",
            (sym, month),
        )
    conn.commit()
    conn.close()


def test_missing_breakdown_months_detected(val_db):
    _seed_traction_only(val_db, "2026-07", ["AAA", "BBB"])
    conn = sqlite3.connect(str(val_db))
    assert fts._months_missing_fund_breakdown(conn) == ["2026-07"]
    fts._insert_stock_funds(conn, "AAA", "2026-07", [{"fund_display_name": "Fund A"}])
    fts._insert_stock_funds(conn, "BBB", "2026-07", [{"fund_display_name": "Fund A"}])
    conn.commit()
    assert fts._months_missing_fund_breakdown(conn) == []
    conn.close()


def test_backfill_fills_missing_breakdown(val_db, monkeypatch):
    _seed_traction_only(val_db, "2026-07", ["AAA"])
    _stub_probe(monkeypatch, ok_months=["2026-07"])
    monkeypatch.setattr(
        fts,
        "_download_and_parse",
        lambda url: [
            {
                "stock_key": "NAME:a",
                "nse": "AAA",
                "score": 1.0,
                "funds": [
                    {
                        "fund_display_name": "Fund A",
                        "activity": "new",
                        "is_new": True,
                    }
                ],
            }
        ],
    )

    res = fts.backfill_fund_breakdown()

    assert res["success"] is True
    assert res["months_backfilled"] == ["2026-07"]
    assert res["fund_rows"] == 1
    conn = sqlite3.connect(str(val_db))
    n = conn.execute(
        "SELECT COUNT(*) FROM fund_traction_funds WHERE month = '2026-07'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_backfill_skips_month_no_longer_upstream(val_db, monkeypatch):
    """gh-pages history can be force-orphaned: a 404 month is skipped, not fatal."""
    _seed_traction_only(val_db, "2026-06", ["AAA"])
    _stub_probe(monkeypatch, ok_months=[])  # 2026-06 now 404s
    monkeypatch.setattr(
        fts,
        "_download_and_parse",
        lambda url: [{"nse": "AAA", "funds": []}],
    )

    res = fts.backfill_fund_breakdown()

    assert res["success"] is True
    assert res["months_backfilled"] == []
    assert res["fund_rows"] == 0


def test_backfill_is_noop_when_complete(val_db, monkeypatch):
    """A fully-populated DB must not trigger any network call."""
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    fts._insert_rows(
        conn,
        [
            {
                "stock_key": "NAME:a",
                "nse": "AAA",
                "score": 1.0,
                "funds": [{"fund_display_name": "Fund A"}],
            }
        ],
        "2026-07",
    )
    conn.close()

    def boom(*args, **kwargs):
        raise AssertionError("no network expected when nothing is missing")

    monkeypatch.setattr(fts, "_download_and_parse", boom)
    monkeypatch.setattr(fts, "_probe_months", boom)

    res = fts.backfill_fund_breakdown()

    assert res == {
        "success": True,
        "months_backfilled": [],
        "fund_rows": 0,
        "error": None,
    }


# ── Board market-data enrichment (price / prev% / mcap) ─────────────────────


def _seed_tech_db(tmp_path, closes_by_symbol):
    """Create a minimal technical_data sidecar under the monkeypatched DB_DIR.

    ``closes_by_symbol`` maps ticker -> list of (date, close).
    """
    conn = sqlite3.connect(str(tmp_path / "myra_technical.db"))
    conn.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT, close REAL)")
    for sym, series in closes_by_symbol.items():
        conn.executemany(
            "INSERT INTO technical_data (symbol, date, close) VALUES (?, ?, ?)",
            [(sym, d, c) for d, c in series],
        )
    conn.commit()
    conn.close()


def _seed_fundamentals(val_db, mcaps):
    """Add a fundamentals(symbol, market_cap) table to the valuation DB."""
    conn = sqlite3.connect(str(val_db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fundamentals (symbol TEXT PRIMARY KEY, "
        "market_cap REAL)"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO fundamentals (symbol, market_cap) VALUES (?, ?)",
        list(mcaps.items()),
    )
    conn.commit()
    conn.close()


def test_board_enriches_price_and_prev_from_tech_db(val_db, tmp_path):
    """Regression: price/prev_month_close/pct_vs_prev must populate from
    technical_data. The old windowed query referenced ``date``/``month`` in the
    outer WHERE without exposing them, raised ``no such column: date``, and
    silently blanked every price cell.
    """
    _seed_board_db(val_db)
    _seed_tech_db(
        tmp_path,
        {
            "ACME": [("2026-06-30", 100.0), ("2026-07-31", 110.0)],
            "BETA": [("2026-06-30", 50.0), ("2026-07-31", 40.0)],
        },
    )
    _seed_fundamentals(val_db, {"ACME": 2.5e11})

    res = fts.get_traction_board(month="2026-07")
    assert res["success"] is True
    assert res["prior_month"] == "2026-06"

    acme = next(r for r in res["rows"] if r["symbol"] == "ACME")
    assert acme["price"] == pytest.approx(110.0)
    assert acme["prev_month_close"] == pytest.approx(100.0)
    assert acme["pct_vs_prev"] == pytest.approx(10.0)
    assert acme["market_cap_cr"] == pytest.approx(25000.0)
    assert acme["mcap_bucket"] == "large"

    beta = next(r for r in res["rows"] if r["symbol"] == "BETA")
    assert beta["price"] == pytest.approx(40.0)
    assert beta["prev_month_close"] == pytest.approx(50.0)
    assert beta["pct_vs_prev"] == pytest.approx(-20.0)
    # No fundamentals row -> unknown bucket, no crash.
    assert beta["market_cap_cr"] is None
    assert beta["mcap_bucket"] == "unknown"


def test_board_price_blank_when_no_tech_db(val_db):
    """Missing technical sidecar is best-effort: price blanks, board still renders."""
    _seed_board_db(val_db)
    res = fts.get_traction_board(month="2026-07")
    assert res["success"] is True
    assert all(r["price"] is None for r in res["rows"])
    assert all(r["pct_vs_prev"] is None for r in res["rows"])


# ── SMA recompute uses the resolved ticker ──────────────────────────────────


def test_update_traction_sma_uses_resolved_ticker(val_db, tmp_path):
    """fund_traction.symbol is a name-derived key for unresolved rows; the SMA
    must be looked up under the resolved ticker (nse) and written back to the
    raw key. Before the fix only rows whose symbol happened to equal the ticker
    received a value.
    """
    raw = "LIFEINSURANCECORPORATIONOF"
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    conn.execute(
        "INSERT INTO fund_traction (symbol, month, nse) VALUES (?, '2026-09', ?)",
        (raw, "LICI"),
    )
    conn.commit()
    conn.close()

    # 30 closes for the *ticker* only; the raw key has no technical rows.
    _seed_tech_db(
        tmp_path,
        {"LICI": [(f"2026-07-{i:02d}", float(i)) for i in range(1, 31)]},
    )

    res = fts.update_traction_sma()
    assert res["success"] is True
    assert res["updated"] == 1
    assert res["skipped_no_sma"] == 0

    conn = sqlite3.connect(str(val_db))
    pct = conn.execute(
        "SELECT pct_vs_sma FROM fund_traction WHERE symbol = ? AND month = '2026-09'",
        (raw,),
    ).fetchone()[0]
    conn.close()
    # sma = mean(1..30) = 15.5, latest = 30 -> +93.5484%
    assert pct == pytest.approx(93.5484, abs=0.01)


def test_update_traction_sma_fans_out_one_ticker_to_many_raw_keys(val_db, tmp_path):
    """Several name-derived rows can resolve to one ticker; every one of them
    must receive the SMA. Without the fan-out the extra rows stay NULL forever.
    """
    raws = ["LIFEINSURANCECORPORATIONOF", "LIFEINSURANCE_CORP", "LIC"]
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    for raw in raws:
        conn.execute(
            "INSERT INTO fund_traction (symbol, month, nse) VALUES (?, '2026-09', ?)",
            (raw, "LICI"),
        )
    conn.commit()
    conn.close()

    _seed_tech_db(
        tmp_path,
        {"LICI": [(f"2026-07-{i:02d}", float(i)) for i in range(1, 31)]},
    )

    res = fts.update_traction_sma()

    assert res["updated"] == len(raws)
    assert res["skipped_no_sma"] == 0
    conn = sqlite3.connect(str(val_db))
    pcts = [
        conn.execute(
            "SELECT pct_vs_sma FROM fund_traction WHERE symbol = ? AND month = '2026-09'",
            (raw,),
        ).fetchone()[0]
        for raw in raws
    ]
    conn.close()
    assert all(p is not None for p in pcts)
    assert pcts == [pytest.approx(93.5484, abs=0.01)] * len(raws)


def test_update_traction_sma_empty_nse_falls_back_to_raw_symbol(val_db, tmp_path):
    """A genuinely unresolved row (nse = '') must still be looked up by its raw
    symbol, so pre-resolution behaviour is preserved rather than regressed.
    """
    raw = "ZYDUSLIFESCIENCES"
    conn = sqlite3.connect(str(val_db))
    fts._ensure_tables(conn)
    conn.execute(
        "INSERT INTO fund_traction (symbol, month, nse) VALUES (?, '2026-09', '')",
        (raw,),
    )
    conn.commit()
    conn.close()

    _seed_tech_db(
        tmp_path,
        {raw: [(f"2026-07-{i:02d}", float(i)) for i in range(1, 31)]},
    )

    res = fts.update_traction_sma()

    assert res["updated"] == 1
    conn = sqlite3.connect(str(val_db))
    pct = conn.execute(
        "SELECT pct_vs_sma FROM fund_traction WHERE symbol = ?", (raw,)
    ).fetchone()[0]
    conn.close()
    assert pct == pytest.approx(93.5484, abs=0.01)


def test_board_stale_symbol_gets_no_fabricated_zero_pct(val_db, tmp_path):
    """A symbol whose last close falls in the PRIOR month must not report a flat
    0.00% change: its latest row is also its prior-month row, so pct_vs_prev has
    to stay None rather than pretend the stock did not move.
    """
    _seed_board_db(val_db)
    # ACME: June close only (stale). BETA: a real July move.
    _seed_tech_db(
        tmp_path,
        {
            "ACME": [("2026-06-30", 100.0)],
            "BETA": [("2026-06-30", 50.0), ("2026-07-31", 40.0)],
        },
    )

    res = fts.get_traction_board(month="2026-07")
    acme = next(r for r in res["rows"] if r["symbol"] == "ACME")
    beta = next(r for r in res["rows"] if r["symbol"] == "BETA")

    # Stale: latest close still shown, but no invented prior-month comparison.
    assert acme["price"] == pytest.approx(100.0)
    assert acme["prev_month_close"] is None
    assert acme["pct_vs_prev"] is None
    # Healthy row is unaffected.
    assert beta["pct_vs_prev"] == pytest.approx(-20.0)
