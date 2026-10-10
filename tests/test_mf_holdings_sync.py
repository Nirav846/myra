"""Tests for MYRA-native RupeeVest fund-holdings ingestion."""

import os
import sqlite3

from myra_app import mf_holdings_sync as mhs
from myra_app.data_sources import rupeevest as rv


def _payload(name="Acme Small Cap Fund-Reg(G)"):
    return {
        "fund_info": [{"s_name": name, "classification": "Equity : Small Cap"}],
        "month_name": ["Aug-26", "Jul-26"],
        "MonthwiseAUM": [{"aum": "1000.5"}, {"aum": "950"}],
        "stock_mapping": {"1": "Alpha Ltd", "2": "Beta Ltd"},
        "stock_data": [
            [
                {"fincode": 1, "percent_aum": "2.5", "noshares": 100},
                {"fincode": 2, "percent_aum": "1.0", "noshares": 50},
            ],
            [{"fincode": 1, "percent_aum": "2.0", "noshares": 90}],
        ],
    }


class _Idx:
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, query):
        return self.mapping.get(query)


def _tables(db_path):
    conn = sqlite3.connect(db_path)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    return names


# ── Client / parser ─────────────────────────────────────────────────────────


def test_month_to_iso():
    assert rv.month_to_iso("Aug-26") == "2026-08"
    assert rv.month_to_iso("Jan-25") == "2025-01"
    assert rv.month_to_iso("garbage") is None


def test_fund_slug_matches_artifact_base():
    # Must equal the upstream artifact's fund_slug minus its trailing _MM_YY.
    assert rv.fund_slug("HDFC Small Cap Fund-Reg(G)") == "hdfc_small_cap"
    assert (
        rv.fund_slug("Nippon India Small Cap Fund(G)")
        == "nippon_india_small_cap_fund_g"
    )
    assert rv.fund_slug("Helios Flexi Cap Fund-Reg(G)") == "helios_flexi_cap"


def test_parse_tracker_normalises_months_aum_and_holdings():
    parsed = rv.parse_tracker(_payload(), schemecode="42")
    assert parsed["slug"] == "acme_small_cap"
    assert parsed["category"] == "Equity : Small Cap"
    assert parsed["schemecode"] == "42"
    assert parsed["months"] == ["2026-08", "2026-07"]
    assert parsed["aum_cr"] == {"2026-08": 1000.5, "2026-07": 950.0}
    assert len(parsed["holdings"]) == 3
    first = parsed["holdings"][0]
    assert first["company"] == "Alpha Ltd"
    assert first["weight_pct"] == 2.5
    assert first["month"] == "2026-08"


def test_index_resolve_exact_lower_and_fuzzy():
    idx = rv.RupeeVestIndex(
        {
            "HDFC Small Cap Fund-Reg(G)": "1",
            "Helios Flexi Cap Fund-Reg(G)": "2",
        }
    )
    assert idx.resolve("HDFC Small Cap Fund-Reg(G)") == (
        "HDFC Small Cap Fund-Reg(G)",
        "1",
    )
    assert idx.resolve("hdfc small cap fund-reg(g)") == (
        "HDFC Small Cap Fund-Reg(G)",
        "1",
    )
    assert idx.resolve("Helios Flexi Cap Fund-Reg(G)")[1] == "2"
    assert idx.resolve("No Such Fund-Reg(G)") is None


# ── Ingestor ────────────────────────────────────────────────────────────────


def test_load_fund_names_skips_comments(tmp_path):
    path = tmp_path / "funds.txt"
    path.write_text(
        "# comment\n\nAcme Small Cap Fund-Reg(G)\n  Beta Fund-Reg(G)  \n",
        encoding="utf-8",
    )
    assert mhs.load_fund_names(str(path)) == [
        "Acme Small Cap Fund-Reg(G)",
        "Beta Fund-Reg(G)",
    ]


def test_sync_dry_run_writes_no_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(mhs, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(mhs, "load_fund_names", lambda: ["Acme Small Cap Fund-Reg(G)"])
    idx = _Idx({"Acme Small Cap Fund-Reg(G)": ("Acme Small Cap Fund-Reg(G)", "42")})

    res = mhs.sync_mf_holdings(
        dry_run=True, index=idx, fetch=lambda c: _payload(), sleep=lambda *_: None
    )

    assert res["success"] is True
    assert res["funds_synced"] == 1
    assert res["holdings_rows"] == 3
    assert res["months"] == ["2026-08", "2026-07"]
    db = os.path.join(str(tmp_path), "myra_valuation.db")
    assert "mf_holding" not in _tables(db)


def test_sync_writes_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(mhs, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(mhs, "load_fund_names", lambda: ["Acme Small Cap Fund-Reg(G)"])
    idx = _Idx({"Acme Small Cap Fund-Reg(G)": ("Acme Small Cap Fund-Reg(G)", "42")})

    def _fetch(code):
        return _payload()

    first = mhs.sync_mf_holdings(
        dry_run=False, index=idx, fetch=_fetch, sleep=lambda *_: None
    )
    second = mhs.sync_mf_holdings(
        dry_run=False, index=idx, fetch=_fetch, sleep=lambda *_: None
    )

    assert first["success"] is True
    assert first["funds_synced"] == 1
    assert first["holdings_rows"] == 3
    assert first["aum_rows"] == 2
    assert second["holdings_rows"] == 3  # replaced, not appended

    db = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM mf_holding").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM mf_fund_aum").fetchone()[0] == 2
    fund = conn.execute(
        "SELECT display_name, schemecode, category FROM mf_fund WHERE fund_slug=?",
        ("acme_small_cap",),
    ).fetchone()
    conn.close()
    assert fund == ("Acme Small Cap Fund-Reg(G)", "42", "Equity : Small Cap")


def test_sync_records_unresolved_fund_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(mhs, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(mhs, "load_fund_names", lambda: ["Ghost Fund-Reg(G)"])

    res = mhs.sync_mf_holdings(
        dry_run=False,
        index=_Idx({}),
        fetch=lambda c: {},
        sleep=lambda *_: None,
        max_failures=0,
    )

    assert res["success"] is False
    assert res["failures"][0]["fund"] == "Ghost Fund-Reg(G)"
    assert "not found on RupeeVest" in res["failures"][0]["error"]
    assert res["error"] and "failed" in res["error"]
