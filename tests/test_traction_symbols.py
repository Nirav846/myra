"""Tests for Phase 2.7 automated fund_traction symbol resolution.

All in-memory: no live DB is opened (see ``tests/conftest.py`` ``live_db_guard``).
The network probe is always injected as a fake so tests never touch yfinance.
"""

import sqlite3

from myra_app import traction_symbols as ts


def _index(master=(), aliases=(), csv_rows=()):
    return ts._build_index(master, aliases, csv_rows)


class TestCompact:
    def test_alnum_upper(self):
        assert ts._compact("P B Fintech-Ltd.") == "PBFINTECHLTD"
        assert ts._compact(None) == ""


class TestResolveLocal:
    def test_master_name_match(self):
        idx = _index(master=[("ZYDUSLIFE", "Zydus Lifesciences Limited")])
        res = ts.resolve_local("Zydus Lifesciences Limited", "ZYDUSLIFESCIENCES", idx)
        assert res == ts.Resolution("ZYDUSLIFE", "master_name")

    def test_canonical_raw_key_not_in_master_returns_none(self):
        idx = _index(master=[("ZYDUSLIFE", "Zydus Lifesciences Limited")])
        # TCS is already canonical but absent from master -> no local guess.
        assert ts.resolve_local("Tata Consultancy Services", "TCS", idx) is None

    def test_persisted_alias_wins(self):
        idx = _index(aliases=[("ONE97COMMUNICATIONS", "PAYTM")])
        res = ts.resolve_local("One97 Communications", "ONE97COMMUNICATIONS", idx)
        assert res == ts.Resolution("PAYTM", "alias")

    def test_csv_fallback(self):
        idx = _index(csv_rows=[("PB Fintech Limited", "POLICYBZR")])
        res = ts.resolve_local("PB Fintech Limited", "PBFINTECH", idx)
        assert res == ts.Resolution("POLICYBZR", "name_to_nse")

    def test_ambiguous_master_dropped(self):
        idx = _index(master=[("AAA", "Foo Ltd"), ("BBB", "Foo Limited")])
        assert ts.resolve_local("Foo Ltd", "FOO", idx) is None

    def test_unknown_returns_none(self):
        assert ts.resolve_local("Mystery Co", "MYSTERYCO", _index()) is None


def _dbs(ft_rows, master=(), aliases=(), tech_symbols=()):
    """Build in-memory meta / valuation / technical DBs.

    ``ft_rows`` yields ``(symbol, name, nse)``.
    """
    meta = sqlite3.connect(":memory:")
    meta.execute("CREATE TABLE symbols_master (symbol TEXT, name TEXT)")
    meta.executemany("INSERT INTO symbols_master VALUES (?, ?)", master)
    if aliases:
        meta.execute(
            "CREATE TABLE symbol_alias (alias TEXT PRIMARY KEY, canonical TEXT)"
        )
        meta.executemany("INSERT INTO symbol_alias VALUES (?, ?)", aliases)

    val = sqlite3.connect(":memory:")
    val.execute(
        "CREATE TABLE fund_traction (symbol TEXT, month TEXT, name TEXT, nse TEXT)"
    )
    val.executemany("INSERT INTO fund_traction VALUES (?, '2026-09', ?, ?)", ft_rows)

    tech = sqlite3.connect(":memory:")
    tech.execute("CREATE TABLE technical_data (symbol TEXT)")
    tech.executemany(
        "INSERT INTO technical_data VALUES (?)", [(s,) for s in tech_symbols]
    )
    return meta, val, tech


class TestBackfill:
    def test_local_resolution_written_fill_only(self):
        meta, val, tech = _dbs(
            [("ZYDUSLIFESCIENCES", "Zydus Lifesciences Limited", "")],
            master=[("ZYDUSLIFE", "Zydus Lifesciences Limited")],
            tech_symbols=["ZYDUSLIFE"],
        )
        report = ts.backfill_traction_nse(
            dry_run=False, meta_conn=meta, val_conn=val, tech_conn=tech
        )
        assert report["resolved"] == 1
        assert report["written"] == 1
        nse = val.execute(
            "SELECT nse FROM fund_traction WHERE symbol='ZYDUSLIFESCIENCES'"
        ).fetchone()[0]
        assert nse == "ZYDUSLIFE"
        alias = meta.execute(
            "SELECT canonical FROM symbol_alias WHERE alias='ZYDUSLIFESCIENCES'"
        ).fetchone()[0]
        assert alias == "ZYDUSLIFE"

    def test_existing_nse_never_clobbered(self):
        meta, val, tech = _dbs(
            [("SOMENAME", "Some Name Ltd", "REALTICKER")],
            tech_symbols=["REALTICKER"],
        )
        report = ts.backfill_traction_nse(
            dry_run=False, meta_conn=meta, val_conn=val, tech_conn=tech
        )
        assert report["already"] == 1
        assert report["written"] == 0
        assert (
            val.execute("SELECT nse FROM fund_traction").fetchone()[0] == "REALTICKER"
        )

    def test_resolved_but_no_local_data_is_flagged(self):
        meta, val, tech = _dbs(
            [("AAVASFINANCIERS", "Aavas Financiers Ltd", "")],
            master=[("AAVAS", "Aavas Financiers Ltd")],
            tech_symbols=[],  # nothing held locally
        )
        report = ts.backfill_traction_nse(
            dry_run=False, meta_conn=meta, val_conn=val, tech_conn=tech
        )
        assert report["resolved"] == 0
        assert report["resolved_no_data"] == 1
        nse = val.execute("SELECT nse FROM fund_traction").fetchone()[0]
        assert nse == "AAVAS"  # still filled so a later data arrival joins

    def test_unresolved_row_is_kept_not_hidden(self):
        meta, val, tech = _dbs([("MYSTERYCO", "Mystery Co", "")])
        report = ts.backfill_traction_nse(
            dry_run=False, meta_conn=meta, val_conn=val, tech_conn=tech
        )
        assert report["unresolved"] == 1
        assert report["written"] == 0
        count = val.execute("SELECT COUNT(*) FROM fund_traction").fetchone()[0]
        assert count == 1

    def test_dry_run_writes_nothing(self):
        meta, val, tech = _dbs(
            [("ZYDUSLIFESCIENCES", "Zydus Lifesciences Limited", "")],
            master=[("ZYDUSLIFE", "Zydus Lifesciences Limited")],
            tech_symbols=["ZYDUSLIFE"],
        )
        report = ts.backfill_traction_nse(
            dry_run=True, meta_conn=meta, val_conn=val, tech_conn=tech
        )
        assert report["resolved"] == 1
        assert report["written"] == 0
        nse = val.execute("SELECT nse FROM fund_traction").fetchone()[0]
        assert (nse or "") == ""
        # A dry run writes nothing at all -- not even the state table.
        has_state = meta.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='traction_symbol_state'"
        ).fetchone()[0]
        assert has_state == 0
        has_alias = meta.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='symbol_alias'"
        ).fetchone()[0]
        assert has_alias == 0

    def test_remote_probe_used_then_cached_in_alias(self):
        calls = []

        def probe(name, raw):
            calls.append(raw)
            return "NEWTICKER"

        meta, val, tech = _dbs([("NEWLISTCO", "New Listing Co", "")])
        report = ts.backfill_traction_nse(
            dry_run=False,
            meta_conn=meta,
            val_conn=val,
            tech_conn=tech,
            remote_probe=probe,
        )
        assert report["probed"] == 1
        assert calls == ["NEWLISTCO"]
        nse = val.execute("SELECT nse FROM fund_traction").fetchone()[0]
        assert nse == "NEWTICKER"
        assert report["resolved_no_data"] == 1  # not in our universe

    def test_retry_bounded_then_parked(self):
        meta, val, tech = _dbs([("UNKNOWNX", "Unknown Xyz", "")])
        calls = {"n": 0}

        def probe(_name, _raw):
            calls["n"] += 1
            return None

        for _ in range(2):
            ts.backfill_traction_nse(
                dry_run=False,
                max_retries=2,
                retry_cooldown_days=30,
                meta_conn=meta,
                val_conn=val,
                tech_conn=tech,
                remote_probe=probe,
            )
        assert calls["n"] == 2
        report = ts.backfill_traction_nse(
            dry_run=False,
            max_retries=2,
            retry_cooldown_days=30,
            meta_conn=meta,
            val_conn=val,
            tech_conn=tech,
            remote_probe=probe,
        )
        assert calls["n"] == 2  # parked: no further probe
        assert report["parked"] == 1

    def test_parked_key_reprobed_after_cooldown(self):
        meta, val, tech = _dbs([("UNKNOWNX", "Unknown Xyz", "")])
        calls = {"n": 0}

        def probe(_name, _raw):
            calls["n"] += 1
            return None

        ts.backfill_traction_nse(
            dry_run=False,
            max_retries=1,
            retry_cooldown_days=30,
            meta_conn=meta,
            val_conn=val,
            tech_conn=tech,
            remote_probe=probe,
        )
        assert calls["n"] == 1
        meta.execute(
            "UPDATE traction_symbol_state SET last_attempt='2000-01-01T00:00:00'"
        )
        ts.backfill_traction_nse(
            dry_run=False,
            max_retries=1,
            retry_cooldown_days=30,
            meta_conn=meta,
            val_conn=val,
            tech_conn=tech,
            remote_probe=probe,
        )
        assert calls["n"] == 2  # cooldown elapsed -> re-probed
