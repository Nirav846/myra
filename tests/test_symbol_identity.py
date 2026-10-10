"""Tests for the Phase 2.5 symbol-identity bridge (``myra_app.symbol_identity``).

Pure/round-trip behaviour only: every test uses in-memory SQLite so nothing
touches a live database (see ``tests/conftest.py`` ``live_db_guard``).
"""

import sqlite3

from myra_app import symbol_identity as si


class TestNormalizeName:
    def test_strips_punctuation_case_and_suffix(self):
        assert si.normalize_name("Aavas Financiers Limited") == "AAVASFINANCIERS"
        assert (
            si.normalize_name("Tata Consultancy Services Ltd.")
            == "TATACONSULTANCYSERVICES"
        )

    def test_idempotent_on_already_normalised(self):
        assert si.normalize_name("AAVASFINANCIERS") == "AAVASFINANCIERS"

    def test_none_and_empty_are_blank(self):
        assert si.normalize_name(None) == ""
        assert si.normalize_name("") == ""
        assert si.normalize_name("   ") == ""

    def test_short_token_suffix_guard(self):
        assert si.normalize_name("CO") == "CO"


class TestResolveAliases:
    def test_matches_long_name_to_canonical(self):
        master = [
            ("AARTIIND", "Aarti Industries Limited"),
            ("AAVAS", "Aavas Financiers Ltd"),
        ]
        mapping, _stats = si.resolve_aliases(
            master, ["AARTIINDUSTRIES", "AAVASFINANCIERS"]
        )
        assert mapping == {"AARTIINDUSTRIES": "AARTIIND", "AAVASFINANCIERS": "AAVAS"}

    def test_skips_already_canonical_alias(self):
        master = [("TCS", "Tata Consultancy Services Ltd")]
        mapping, _stats = si.resolve_aliases(master, ["TCS"])
        assert mapping == {}

    def test_skips_ambiguous_by_never_guessing(self):
        master = [("AAA", "Foo Ltd"), ("BBB", "Foo Limited")]
        mapping, stats = si.resolve_aliases(master, ["FOO"])
        assert mapping == {}
        assert stats["ambiguous"] == 1

    def test_foreign_names_left_unmatched(self):
        master = [("TCS", "Tata Consultancy Services Ltd")]
        mapping, _stats = si.resolve_aliases(master, ["ALPHABETINC", "AMAZONCOMINC"])
        assert mapping == {}

    def test_blank_inputs_ignored(self):
        mapping, _stats = si.resolve_aliases(
            [("TCS", "Tata Consultancy Services")], ["", "  "]
        )
        assert mapping == {}


class TestWriteAliases:
    @staticmethod
    def _conn():
        return sqlite3.connect(":memory:")

    def test_idempotent_upsert(self):
        conn = self._conn()
        assert si.write_aliases(conn, {"X": "AAA"}) == 1
        assert si.write_aliases(conn, {"X": "AAA"}) == 1
        assert conn.execute("SELECT COUNT(*) FROM symbol_alias").fetchone()[0] == 1

    def test_lower_confidence_does_not_downgrade(self):
        conn = self._conn()
        si.write_aliases(conn, {"X": "STRONG"}, source="strong", confidence=1.0)
        si.write_aliases(conn, {"X": "WEAK"}, source="weak", confidence=0.4)
        row = conn.execute(
            "SELECT canonical, source FROM symbol_alias WHERE alias = 'X'"
        ).fetchone()
        assert row == ("STRONG", "strong")

    def test_equal_confidence_replaces(self):
        conn = self._conn()
        si.write_aliases(conn, {"X": "OLD"}, confidence=0.5)
        si.write_aliases(conn, {"X": "NEW"}, confidence=0.5)
        row = conn.execute(
            "SELECT canonical FROM symbol_alias WHERE alias = 'X'"
        ).fetchone()
        assert row[0] == "NEW"

    def test_self_mapping_skipped(self):
        conn = self._conn()
        assert si.write_aliases(conn, {"X": "X"}) == 0

    def test_empty_mapping_is_noop(self):
        conn = self._conn()
        assert si.write_aliases(conn, {}) == 0


class TestBackfill:
    @staticmethod
    def _dbs(ft_symbols, master_rows):
        meta = sqlite3.connect(":memory:")
        meta.execute("CREATE TABLE symbols_master (symbol TEXT, name TEXT)")
        meta.executemany("INSERT INTO symbols_master VALUES (?, ?)", master_rows)
        val = sqlite3.connect(":memory:")
        val.execute("CREATE TABLE fund_traction (symbol TEXT)")
        val.executemany(
            "INSERT INTO fund_traction VALUES (?)", [(s,) for s in ft_symbols]
        )
        return meta, val

    def test_writes_alias_when_not_dry_run(self):
        meta, val = self._dbs(
            ["AARTIINDUSTRIES"], [("AARTIIND", "Aarti Industries Limited")]
        )
        report = si.backfill_symbol_alias(dry_run=False, meta_conn=meta, val_conn=val)
        assert report["aliases"] == 1
        assert report["written"] == 1
        canonical = meta.execute(
            "SELECT canonical FROM symbol_alias WHERE alias = 'AARTIINDUSTRIES'"
        ).fetchone()[0]
        assert canonical == "AARTIIND"

    def test_dry_run_reports_without_writing(self):
        meta, val = self._dbs(["AAVASFINANCIERS"], [("AAVAS", "Aavas Financiers Ltd")])
        report = si.backfill_symbol_alias(dry_run=True, meta_conn=meta, val_conn=val)
        assert report["aliases"] == 1
        assert "written" not in report
        has_table = meta.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='symbol_alias'"
        ).fetchone()[0]
        assert has_table == 0
