"""Tests for ``myra_app.traction_intelligence`` (MoM cohorts + DWAP).

All in-memory: nothing touches a live DB (see ``tests/conftest.py``).
"""

import sqlite3

from myra_app import traction_intelligence as ti


def _val():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE fund_traction (
            symbol TEXT, month TEXT, name TEXT, nse TEXT, sector TEXT,
            traction_score REAL, number_of_funds INTEGER, adds_new INTEGER,
            reduces_closes INTEGER, pct_vs_sma REAL)"""
    )
    conn.execute(
        """CREATE TABLE fund_traction_funds (
            symbol TEXT, month TEXT, fund_slug TEXT, fund_name TEXT,
            share_change_pct REAL)"""
    )
    return conn


def _tech():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE technical_data (
            symbol TEXT, date TEXT, high REAL, low REAL, close REAL,
            delivery REAL)"""
    )
    return conn


def _add_ft(conn, rows):
    conn.executemany(
        "INSERT INTO fund_traction VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )


def _add_fund(conn, rows):
    conn.executemany("INSERT INTO fund_traction_funds VALUES (?, ?, ?, ?, ?)", rows)


def _add_tech(conn, rows):
    conn.executemany("INSERT INTO technical_data VALUES (?, ?, ?, ?, ?, ?)", rows)


class TestMonthPriceContext:
    def test_dwap_and_range(self):
        t = _tech()
        _add_tech(
            t,
            [
                ("AAA", "2026-09-01", 110, 90, 100, 1000),
                ("AAA", "2026-09-15", 120, 100, 110, 3000),
            ],
        )
        ctx = ti._month_price_context(t, ["AAA"], "2026-09")["AAA"]
        # dwap = (1000*100 + 3000*110) / 4000 = 107.5
        assert ctx["dwap"] == 107.5
        assert ctx["month_high"] == 120
        assert ctx["month_low"] == 90
        assert ctx["cmp"] == 110  # latest close

    def test_missing_symbol_is_absent(self):
        t = _tech()
        assert ti._month_price_context(t, ["NOPE"], "2026-09") == {}


class TestCohorts:
    def test_new_exited_held_added_reduced(self):
        v = _val()
        _add_fund(
            v,
            [
                ("AAA", "2026-09", "A", "Fund A", 2.0),
                ("AAA", "2026-09", "B", "Fund B", -1.0),
                ("AAA", "2026-09", "C", "Fund C", 5.0),
                ("AAA", "2026-08", "A", "Fund A", 1.0),
                ("AAA", "2026-08", "B", "Fund B", 0.5),
                ("AAA", "2026-08", "D", "Fund D", 3.0),
            ],
        )
        by_symbol, board = ti._cohorts(v, "2026-09", "2026-08")
        coh = by_symbol["AAA"]
        assert coh["new_funds"] == 1
        assert coh["exited_funds"] == 1
        assert coh["held_funds"] == 2
        assert coh["expanding_funds"] == 1
        assert coh["trimming_funds"] == 1
        assert coh["cohort"] == "unchanged"  # 3 funds both months
        funds = {r["fund"]: r for r in board}
        assert funds["Fund C"]["symbols_added"] == 1
        assert funds["Fund D"]["symbols_added"] == 0
        assert funds["Fund D"]["symbols_exited"] == 1

    def test_no_prior_month_is_recording_only(self):
        v = _val()
        _add_fund(v, [("AAA", "2026-09", "A", "Fund A", 1.0)])
        by_symbol, _ = ti._cohorts(v, "2026-09", None)
        assert by_symbol["AAA"]["new_funds"] == 1
        assert by_symbol["AAA"]["cohort"] == "adding_funds"

    def test_key_map_absorbs_upstream_symbol_format_change(self):
        # Same company, ticker in Aug and long-name in Sep -> must be one holding.
        v = _val()
        _add_fund(
            v,
            [
                ("AARTIIND", "2026-08", "A", "Fund A", 1.0),
                ("AARTIINDUSTRIES", "2026-09", "A", "Fund A", 2.0),
            ],
        )
        key_map = {"AARTIIND": "AARTIIND", "AARTIINDUSTRIES": "AARTIIND"}
        by_symbol, _ = ti._cohorts(v, "2026-09", "2026-08", key_map)
        coh = by_symbol["AARTIIND"]
        assert coh["new_funds"] == 0
        assert coh["exited_funds"] == 0
        assert coh["held_funds"] == 1
        assert coh["expanding_funds"] == 1


class TestIntelligencePayload:
    def _seed(self):
        v = _val()
        _add_ft(
            v,
            [
                ("RAWSYM", "2026-08", "Real Co", "", "Tech", 10.0, 3, 1, 1, 2.0),
                ("RAWSYM", "2026-09", "Real Co", "REAL", "Tech", 20.0, 4, 2, 0, 5.0),
                ("NOTICK", "2026-09", "No Data Co", "", "Other", 5.0, 1, 1, 0, None),
            ],
        )
        _add_fund(
            v,
            [
                ("RAWSYM", "2026-09", "A", "Fund A", 2.0),
                ("RAWSYM", "2026-09", "C", "Fund C", 3.0),
                ("RAWSYM", "2026-08", "A", "Fund A", 1.0),
                ("NOTICK", "2026-09", "A", "Fund A", 1.0),
            ],
        )
        t = _tech()
        _add_tech(
            t,
            [
                ("REAL", "2026-09-01", 110, 90, 100, 1000),
                ("REAL", "2026-09-15", 120, 100, 110, 3000),
            ],
        )
        return v, t

    def test_payload_merges_price_and_cohorts(self):
        v, t = self._seed()
        out = ti.get_traction_intelligence("2026-09", val_conn=v, tech_conn=t)
        assert out["success"] is True
        assert out["month"] == "2026-09"
        assert out["prior_month"] == "2026-08"
        rows = {r["symbol"]: r for r in out["rows"]}
        real = rows["RAWSYM"]
        assert real["resolved"] == "REAL"
        assert real["has_market_data"] is True
        assert real["cmp"] == 110
        assert real["dwap"] == 107.5
        assert real["range_position"] == 66.7
        assert real["new_funds"] == 1  # Fund C entered
        assert real["fund_count_delta"] == 1
        assert real["cohort"] == "adding_funds"
        # A symbol with no technical rows is still listed, flagged.
        nod = rows["NOTICK"]
        assert nod["has_market_data"] is False
        assert nod["cmp"] is None
        assert out["stats"]["without_market_data"] == 1
        board = {r["fund"]: r for r in out["fund_leaderboard"]}
        assert board["Fund C"]["symbols_added"] == 1
        assert board["Fund C"]["net"] >= 1

    def test_defaults_to_latest_month(self):
        v, t = self._seed()
        out = ti.get_traction_intelligence(val_conn=v, tech_conn=t)
        assert out["month"] == "2026-09"

    def test_empty_db_reports_error(self):
        out = ti.get_traction_intelligence(val_conn=_val(), tech_conn=_tech())
        assert out["success"] is False
        assert "No fund traction data" in out["error"]
