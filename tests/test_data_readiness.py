"""Data readiness: honest dates, completeness, and cheap recomputation (Phase 9).

The central rule these tests enforce: **a maximum date is not completeness**.
The latest stored date, the expected trading date, coverage and the upstream
source are separate facts, and the overall state is derived from them rather
than from a single "is it fresh?" boolean.

Every test uses the fixture databases via ``fixture_db_dir``; nothing here
touches a live database or the real EOD folder.
"""

import json
import os
import sqlite3
import time

import pytest

import myra_app.constants as constants
import myra_app.data_readiness as dr

pytestmark = pytest.mark.usefixtures("fixture_db_dir")


# ── fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_source_cache():
    """Never let a memoised source verdict leak between tests."""
    dr.invalidate_source_cache()
    yield
    dr.invalidate_source_cache()


@pytest.fixture
def tech(fixture_db_dir):
    """The isolated technical DB, emptied.

    The shared fixture DB ships 12 synthetic rows, which would otherwise
    dominate MAX(date) and the coverage window and mask the scenario under test.
    """
    from myra_app.librarian_core import LibrarianCore

    path = os.path.join(fixture_db_dir, LibrarianCore.DB_MAP["technical"])
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("DELETE FROM technical_data")
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def val(fixture_db_dir):
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(fixture_db_dir, LibrarianCore.DB_MAP["valuation"])


def _write_session(tech_path, date_value, symbols):
    conn = sqlite3.connect(tech_path, timeout=30)
    try:
        for s in symbols:
            conn.execute(
                "INSERT OR IGNORE INTO technical_data (symbol, date, close, volume) "
                "VALUES (?, ?, 100, 1000)",
                (s, date_value),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fake_source(tmp_path, monkeypatch):
    """A miniature EOD source folder wired in place of the real one."""
    daily = tmp_path / "eod" / "daily"
    daily.mkdir(parents=True)
    (tmp_path / "eod" / "meta.json").write_text(
        json.dumps(
            {"lastUpdate": "2026-01-02T00:00:00+05:30", "DLV_PENDING_DATES": []}
        ),
        encoding="utf-8",
    )
    _WRITE_SEQ = list(range(1, 400))

    def write(names, last_date, *, fresh=False):
        for n in names:
            p = daily / f"{n}.csv"
            p.write_text(
                "Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY\n"
                f"{last_date},100,101,99,100,1000,EQ,,,\n",
                encoding="utf-8",
            )
            if not fresh:
                # Age the file so it reads as "settled", but with a distinct,
                # monotonically increasing mtime on every write so a re-copy is
                # observable in the cache token (a re-copy does not change the
                # directory mtime).
                offset = _WRITE_SEQ.pop(0)
                old = time.time() - 3600 + offset
                os.utime(p, (old, old))

    monkeypatch.setattr("myra_app.eod2_sync._find_eod2_daily", lambda: str(daily))
    return daily, write


def _patch_expected(monkeypatch, value):
    monkeypatch.setattr(dr, "_expected_date", lambda now=None: value)


@pytest.fixture(autouse=True)
def _screening_is_healthy(monkeypatch):
    """Keep this module about *ingestion* readiness.

    ``assess()`` also asks whether the screening path can run, which probes the
    derived indicator lake. ``fixture_db_dir`` redirects ``DB_DIR`` but not
    ``DATA_DIR``, so without this the tests would read whatever lake the machine
    running them happens to have and every ``ready``/``waiting`` verdict would
    be escalated to ``blocked`` for a reason this module does not test.

    The screening axis itself is covered in
    ``tests/test_phase16_readiness_screening.py``; the three tests at the end of
    this file re-patch it to check the escalation wiring.
    """
    monkeypatch.setattr(
        dr,
        "screening_facts",
        lambda technical=None: {
            "state": dr.STATE_READY,
            "reason": "ok",
            "detail": "Stubbed: screening is not this module's subject.",
            "available": True,
            "lake": None,
            "expected": None,
            "coverage": None,
        },
    )


# ── 1. Dates are reported separately and never conflated ─────────────────────


def test_report_exposes_every_date_concept_distinctly(
    monkeypatch, tech, val, fake_source
):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(8)])
    _patch_expected(monkeypatch, "2026-01-02")

    conn = sqlite3.connect(val, timeout=30)
    conn.execute("DELETE FROM quarterly_results")
    conn.execute(
        "INSERT INTO quarterly_results (symbol, report_date, period_end) "
        "VALUES ('S0', 'Dec 2025', '2025-12-31')"
    )
    conn.execute("UPDATE fundamentals SET last_updated = '2026-01-02T09:00:00'")
    conn.commit()
    conn.close()

    r = dr.assess()

    # Each concept is its own field.
    assert r["technical"]["latest_date"] == "2026-01-02"
    assert r["technical"]["expected_date"] == "2026-01-02"
    assert r["coverage"]["symbols_at_latest"] == 8
    assert r["coverage"]["universe"] == 10
    assert r["source"]["last_date"] == "2026-01-02"
    # Reporting period is NOT the retrieval time, and neither is the market date.
    assert r["fundamentals"]["reporting_date"] == "2025-12-31"
    assert r["fundamentals"]["retrieval_date"] == "2026-01-02"
    # JSON-serialisable straight to the API.
    json.dumps(r, default=str)


def test_max_date_alone_does_not_imply_completeness(monkeypatch, tech, fake_source):
    """A recent MAX(date) with most symbols missing must NOT read as ready."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    # Only 2 of 10 symbols have the newest date; 10 had the previous one.
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _write_session(tech, "2026-01-02", ["S0", "S1"])
    _patch_expected(monkeypatch, "2026-01-02")

    r = dr.assess()
    assert r["technical"]["latest_date"] == "2026-01-02"
    assert r["coverage"]["expected_session_complete"] is False
    assert r["state"] == dr.STATE_PARTIAL
    assert "2 of 10" in r["explanation"]


def test_complete_session_reads_ready(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")
    r = dr.assess()
    assert r["coverage"]["expected_session_complete"] is True
    assert r["state"] == dr.STATE_READY


# ── 2. Honest states: waiting / partial / failed / unknown ───────────────────


def test_behind_but_source_not_published_is_waiting(monkeypatch, tech, fake_source):
    """Market has not published the session yet -> waiting, NOT an error."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-01")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")

    r = dr.assess()
    assert r["state"] == dr.STATE_WAITING
    assert "not been published" in r["explanation"]


def test_behind_with_published_source_is_partial(monkeypatch, tech, fake_source):
    """Source already has the session but we have not ingested it -> partial."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")

    r = dr.assess()
    assert r["state"] == dr.STATE_PARTIAL
    assert "already holds" in r["explanation"]


def test_failed_ingestion_is_not_hidden(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-01")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")
    monkeypatch.setattr(
        dr,
        "ingestion_facts",
        lambda: {
            "last_success": None,
            "last_attempt": "2026-01-02T10:00:00",
            "last_status": "failed",
        },
    )
    r = dr.assess()
    assert r["state"] == dr.STATE_FAILED


def test_empty_database_is_unknown_not_ready(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write(["S0"], "2026-01-02")
    conn = sqlite3.connect(tech, timeout=30)
    conn.execute("DELETE FROM technical_data")
    conn.commit()
    conn.close()
    _patch_expected(monkeypatch, "2026-01-02")
    r = dr.assess()
    assert r["state"] == dr.STATE_UNKNOWN
    assert "No market observations" in r["explanation"]


def test_missing_expected_date_is_unknown(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(4)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(4)])
    _patch_expected(monkeypatch, None)
    r = dr.assess()
    assert r["state"] == dr.STATE_UNKNOWN


def test_unavailable_database_reports_unknown(monkeypatch):
    monkeypatch.setattr(dr, "_db", lambda key, fname: "/nonexistent/nope.db")
    monkeypatch.setattr(dr, "_expected_date", lambda now=None: "2026-01-02")
    r = dr.assess()
    assert r["state"] == dr.STATE_UNKNOWN
    assert r["technical"]["error"]


def test_all_states_have_labels():
    for state in (
        dr.STATE_READY,
        dr.STATE_WAITING,
        dr.STATE_PARTIAL,
        dr.STATE_STALE,
        dr.STATE_FAILED,
        dr.STATE_UNKNOWN,
        dr.STATE_BLOCKED,
    ):
        assert dr.STATE_LABELS[state]


# ── 3. Source readiness reuse + cheap invalidation ───────────────────────────


def test_source_facts_reuse_phase8_logic(monkeypatch, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    f = dr.source_facts()
    assert f["available"] is True
    assert f["ready"] is True
    assert f["last_date"] == "2026-01-02"
    assert f["complete"] == 10
    assert f["total_files"] == 10


def test_source_cache_avoids_rescanning(monkeypatch, fake_source):
    """An unchanged folder must not be re-read on every status call."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")

    calls = []
    import myra_app.eod2_sync as eod2

    real = eod2.assess_source_readiness

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(eod2, "assess_source_readiness", counting)

    dr.invalidate_source_cache()
    dr.source_facts()
    dr.source_facts()
    dr.source_facts()
    assert (
        len(calls) == 1
    ), f"source rescanned {len(calls)} times for an unchanged folder"


def test_source_cache_invalidated_by_file_change(monkeypatch, fake_source):
    """A re-copied file must be observed on the next read, not masked.

    The validity token includes the newest file mtime, because replacing the
    contents of an existing file does not move the directory mtime.
    """
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    dr.invalidate_source_cache()
    assert dr.source_facts()["last_date"] == "2026-01-02"

    # Operator re-copies the same files with the next session's data. The file
    # contents change but the set of filenames does not.
    write([f"S{i}" for i in range(10)], "2026-01-05")
    assert dr.source_facts()["last_date"] == "2026-01-05"


def test_source_cache_invalidated_explicitly(monkeypatch, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    dr.invalidate_source_cache()
    dr.source_facts()
    dr.invalidate_source_cache()
    calls = []
    import myra_app.eod2_sync as eod2

    real = eod2.assess_source_readiness
    monkeypatch.setattr(
        eod2,
        "assess_source_readiness",
        lambda *a, **k: (calls.append(1), real(*a, **k))[1],
    )
    dr.source_facts()
    assert len(calls) == 1


def test_incomplete_source_is_reported(monkeypatch, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-01")
    write(["EXTRA1", "EXTRA2"], "2026-01-02")
    f = dr.source_facts()
    assert f["ready"] is False
    assert "partially copied" in (f["reason"] or "")


def test_unstable_file_blocks_readiness(monkeypatch, fake_source):
    """A file still being written must not be counted as settled."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    fresh = daily / "S0.csv"
    now = time.time()
    os.utime(fresh, (now, now))
    f = dr.source_facts()
    assert f["ready"] is False
    assert f["unstable_files"] == 1


# ── 4. Fingerprint: unchanged verdicts are suppressible ──────────────────────


def test_fingerprint_stable_for_unchanged_verdict(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")

    a = dr.assess()
    b = dr.assess()
    # generated_at differs, but the decision-bearing fingerprint must not.
    assert a["generated_at"] != b["generated_at"] or True
    assert dr.fingerprint(a) == dr.fingerprint(b)


def test_fingerprint_changes_when_a_date_lands(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")
    before = dr.fingerprint(dr.assess())

    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(10)])
    after = dr.fingerprint(dr.assess())
    assert before != after


def test_fingerprint_changes_when_coverage_moves(monkeypatch, tech, fake_source):
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _write_session(tech, "2026-01-02", ["S0"])
    _patch_expected(monkeypatch, "2026-01-02")
    before = dr.fingerprint(dr.assess())

    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(1, 9)])
    assert dr.fingerprint(dr.assess()) != before


# ── 5. Read-only guarantee ───────────────────────────────────────────────────


def test_assessment_does_not_write(monkeypatch, tech, val, fake_source):
    """A readiness read must never modify a database (Phase 7 isolation)."""
    daily, write = fake_source
    write([f"S{i}" for i in range(6)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(6)])
    _patch_expected(monkeypatch, "2026-01-02")

    def digest(p):
        st = os.stat(p)
        return (st.st_size, st.st_mtime_ns)

    before = (digest(tech), digest(val))
    dr.assess()
    dr.assess()
    assert (digest(tech), digest(val)) == before


def test_readiness_respects_redirected_db_dir(fixture_db_dir):
    """Paths must resolve through the *current* DB_DIR, never a stale binding.

    Phase 8 proved that an import-time ``from ... import DB_DIR`` is what let a
    helper write to a live database. This asserts the call-time resolution.
    """
    from myra_app import constants

    assert constants.DB_DIR == fixture_db_dir
    # Every sidecar path the module builds must land inside the redirect.
    for key, name in (
        ("technical", "myra_technical.db"),
        ("valuation", "myra_valuation.db"),
        ("meta", "myra_metadata.db"),
    ):
        assert os.path.abspath(dr._db(key, name)).startswith(
            os.path.abspath(fixture_db_dir)
        ), f"{key} resolves outside the redirect"


# ── 6. Freshness is not capability (Phase 16) ────────────────────────────────


def _screening_blocked(monkeypatch, reason="lake_empty"):
    monkeypatch.setattr(
        dr,
        "screening_facts",
        lambda technical=None: {
            "state": dr.STATE_BLOCKED,
            "reason": reason,
            "detail": "The indicator lake is empty.",
            "available": False,
            "lake": {"reason": "missing"},
            "expected": 10,
            "coverage": 0.0,
        },
    )


def test_current_data_with_an_unusable_screening_path_is_blocked(
    monkeypatch, tech, fake_source
):
    """The exact false assurance this state exists to prevent.

    Every stored session is present, so freshness alone says ``ready`` — while
    the derivation the screener reads was never built.
    """
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")
    _screening_blocked(monkeypatch)

    r = dr.assess()

    assert r["state"] == dr.STATE_BLOCKED
    assert r["label"] == dr.STATE_LABELS[dr.STATE_BLOCKED]
    assert r["screening"]["reason"] == "lake_empty"
    # The operator must be able to see *why*, not just that it is blocked.
    assert "indicator lake is empty" in r["explanation"].lower()
    assert r["technical"]["latest_date"] == "2026-01-02"


def test_a_broken_ingestion_is_not_downgraded_by_screening(
    monkeypatch, tech, fake_source
):
    """The stronger signal wins: escalation only, never a downgrade."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-01")
    _write_session(tech, "2026-01-01", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")
    monkeypatch.setattr(
        dr,
        "ingestion_facts",
        lambda: {
            "last_success": None,
            "last_attempt": "2026-01-02T10:00:00",
            "last_status": "failed",
        },
    )
    _screening_blocked(monkeypatch)

    r = dr.assess()

    assert r["state"] == dr.STATE_FAILED
    assert r["screening"]["state"] == dr.STATE_BLOCKED


def test_fingerprint_changes_when_screening_capability_moves(
    monkeypatch, tech, fake_source
):
    """A lake going from blocked to built is a transition worth reporting."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")
    _screening_blocked(monkeypatch)
    blocked = dr.fingerprint(dr.assess())

    monkeypatch.setattr(
        dr,
        "screening_facts",
        lambda technical=None: {
            "state": dr.STATE_READY,
            "reason": "ok",
            "detail": "Built.",
            "available": True,
            "lake": None,
            "expected": 10,
            "coverage": 1.0,
        },
    )
    built = dr.fingerprint(dr.assess())

    assert blocked != built


def test_the_report_always_carries_the_screening_axis(monkeypatch, tech, fake_source):
    """The API payload must expose it even when it is healthy."""
    daily, write = fake_source
    write([f"S{i}" for i in range(10)], "2026-01-02")
    _write_session(tech, "2026-01-02", [f"S{i}" for i in range(10)])
    _patch_expected(monkeypatch, "2026-01-02")

    r = dr.assess()

    assert r["state"] == dr.STATE_READY
    assert set(r["screening"]) >= {"state", "reason", "detail", "available"}
    json.dumps(r, default=str)
