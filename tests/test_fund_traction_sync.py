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
