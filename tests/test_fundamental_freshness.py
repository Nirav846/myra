"""Phase 11 Priority 2 — fundamental provenance and freshness.

These tests pin the properties the phase exists to guarantee:

* a missing timestamp is ``unknown``, never ``fresh``;
* a recent *retrieval* of an old *reporting period* is still old;
* fields with no producer are labelled unavailable rather than looking like a
  transient gap;
* a valid numeric zero is preserved and never confused with missing;
* nothing here invents data, converts missing to zero, or enables Phase 6's
  opt-in ranking inputs.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, timedelta

import pytest

from myra_app import fundamental_freshness as ff

TODAY = date(2026, 10, 15)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


# ── freshness classification ────────────────────────────────────────────────


def test_fresh_within_cadence():
    # daily cadence is 7 days, so 5 days old is comfortably fresh.
    out = ff.classify("2026-10-10", "2026-10-15", cadence="daily", today=TODAY)
    assert out["state"] == ff.FRESH
    assert out["age_days"] == 5


def test_aging_between_one_and_two_cadences():
    # 9 days is past the 7-day daily cadence but inside the 14-day grace.
    out = ff.classify("2026-10-06", "2026-10-15", cadence="daily", today=TODAY)
    assert out["state"] == ff.AGING


def test_stale_beyond_two_cadences():
    out = ff.classify("2026-06-01", "2026-10-15", cadence="daily", today=TODAY)
    assert out["state"] == ff.STALE


def test_quarterly_cadence_is_far_more_forgiving_than_daily():
    """A quarter-old financial statement is normal, not stale."""
    period = date(2026, 7, 15)
    assert ff.classify(period, today=TODAY, cadence="quarterly")["state"] == ff.FRESH
    assert ff.classify(period, today=TODAY, cadence="daily")["state"] == ff.STALE


def test_missing_reporting_period_is_unknown_never_fresh():
    for value in (None, "", "   ", "not-a-date"):
        out = ff.classify(value, "2026-10-15", cadence="daily", today=TODAY)
        assert out["state"] == ff.UNKNOWN, f"{value!r} must not read as fresh"


def test_missing_both_is_unknown():
    assert ff.classify(None, None, today=TODAY)["state"] == ff.UNKNOWN


def test_a_recent_retrieval_cannot_make_an_old_period_fresh():
    """The headline rule: fetch time is not information time."""
    old = date(2025, 1, 31)
    out = ff.classify(old, TODAY, cadence="daily", today=TODAY)
    assert out["state"] == ff.STALE
    assert out["age_days"] > 100


def test_a_future_reporting_period_is_unknown_not_fresh():
    future = date(2027, 1, 1)
    out = ff.classify(future, today=TODAY, cadence="quarterly")
    assert out["state"] == ff.UNKNOWN, "a future period is not evidence"


def test_boundary_ages_are_exact():
    cadence = ff.CADENCE_DAYS["daily"]  # 7 days

    def state_for(days: int) -> str:
        return ff.classify(TODAY - timedelta(days=days), cadence="daily", today=TODAY)[
            "state"
        ]

    # fresh up to and including one cadence
    assert state_for(cadence) == ff.FRESH
    assert state_for(cadence - 1) == ff.FRESH
    # aging past one cadence, up to and including two
    assert state_for(cadence + 1) == ff.AGING
    assert state_for(cadence * 2) == ff.AGING
    # stale beyond two cadences
    assert state_for(cadence * 2 + 1) == ff.STALE


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-09-30", date(2026, 9, 30)),
        ("2026-09-30T12:00:00", date(2026, 9, 30)),
        ("2026-09-30T12:00:00Z", date(2026, 9, 30)),
        ("2026-09-30 12:00:00", date(2026, 9, 30)),
    ],
)
def test_timestamp_shapes_parse(text, expected):
    assert ff.classify(text, today=TODAY)["state"] in ff.FRESHNESS_STATES
    assert ff._parse(text) == expected


def test_assess_row_keeps_period_and_retrieval_distinct():
    out = ff.assess_row("2026-09-30", "2026-10-13", cadence="quarterly", today=TODAY)
    assert out["reporting_period"] == "2026-09-30"
    assert out["retrieved_at"] == "2026-10-13"
    assert out["state"] == ff.FRESH


# ── provenance honesty ──────────────────────────────────────────────────────


def test_fields_without_a_producer_are_marked_unavailable():
    for field in (
        "profit_growth",
        "sales_growth",
        "debt_to_equity",
        "smart_money_score",
    ):
        meta = ff.provenance(field)
        assert (
            meta["status"] == "unavailable"
        ), f"{field} has no producer and must not claim otherwise"


def test_unavailable_fields_say_so_in_words():
    for field in ("profit_growth", "smart_money_score"):
        assert "NONE" in ff.provenance(field)["producer"]


def test_known_producers_are_marked_verified():
    for field in ("market_cap", "roe", "inst_holding"):
        assert ff.provenance(field)["status"] == "verified"


def test_unknown_field_is_unverified_not_verified():
    assert ff.provenance("some_column_nobody_checked")["status"] == "unverified"


def test_net_margin_unit_caveat_is_recorded():
    """Known defect: net_margin mixes fraction and percent scales."""
    meta = ff.provenance("net_margin")
    assert meta["status"] != "verified"
    assert "MIXED" in meta["units"]


def test_inst_holding_is_not_presented_as_smart_money():
    """Month-end holdings are a stock, not a flow - say so where it is used."""
    assert "cannot" in ff.provenance("inst_holding")["caveat"].lower()


# ── zero versus missing ─────────────────────────────────────────────────────


def test_a_valid_zero_is_preserved_as_zero_not_missing(fixture_db_dir):
    """Zero ROE (a breakeven company) is real data and must survive.

    `fixture_db_dir` hands out one directory for the whole session, so this test
    must restore whatever it changes - leaving rows behind made an unrelated
    module's row-count assertions fail later in the run.
    """
    from myra_app import constants
    from myra_app.librarian_core import LibrarianCore

    path = os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["valuation"])
    markers = ("ZZ_PHASE11_ZERO", "ZZ_PHASE11_NULL", "ZZ_PHASE11_REAL")
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fundamentals)")}
        if "roe" not in cols:  # pragma: no cover - fixture always has it
            pytest.skip("fixture valuation DB has no fundamentals.roe column")
        conn.execute(
            f"DELETE FROM fundamentals WHERE symbol IN ({','.join('?' * len(markers))})",
            markers,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO fundamentals (symbol, roe) VALUES (?, ?)",
            list(zip(markers, (0.0, None, 12.5))),
        )
        conn.commit()
        zeros = conn.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE symbol=? AND roe = 0",
            (markers[0],),
        ).fetchone()[0]
        nulls = conn.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE symbol=? AND roe IS NULL",
            (markers[1],),
        ).fetchone()[0]
    finally:
        conn.close()

    assert zeros == 1, "a real zero must remain distinguishable from missing"
    assert nulls == 1

    # Restore the shared fixture to its original state.
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            f"DELETE FROM fundamentals WHERE symbol IN ({','.join('?' * len(markers))})",
            markers,
        )
        conn.commit()
    finally:
        conn.close()


def test_freshness_model_never_converts_missing_to_zero():
    src = _read("myra_app/fundamental_freshness.py")
    assert "or 0" not in src, "missing fundamentals must not become zero"
    assert "COALESCE" not in src


# ── no scoring / ranking behaviour changes ───────────────────────────────────


def test_no_scoring_weights_or_defaults_were_touched():
    """Guardrail: this phase must not change investment decisions."""
    import subprocess

    diff = subprocess.run(
        [
            "git",
            "diff",
            "--stat",
            "HEAD",
            "--",
            "myra_app/score_components_v2.py",
            "myra_app/positional_engine.py",
            "myra_app/scoring.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert diff.stdout.strip() == "", f"scoring files were modified:\n{diff.stdout}"


def test_phase6_opt_in_flag_is_unchanged():
    """The opt-in must stay opt-in; nothing here enables it."""
    src = _read("myra_app/ranking_inputs.py")
    assert 'ENV_FLAG = "MYRA_V25_RANKING_INPUTS"' in src
    fresh = _read("myra_app/fundamental_freshness.py")
    assert (
        "MYRA_V25_RANKING_INPUTS" not in fresh
    ), "freshness reporting must not flip the ranking opt-in"


def test_freshness_module_has_no_ingestion_or_scheduler():
    """Reporting only: it must not start work or write anywhere."""
    src = _read("myra_app/fundamental_freshness.py")
    for banned in ("threading", "schedule", "INSERT", "UPDATE", "requests", "urllib"):
        assert banned not in src, f"{banned} must not appear in a read-only module"
    assert re.search(r"sqlite3", src) is None, "no database access at all"


# ── readiness integration ───────────────────────────────────────────────────


def test_readiness_exposes_fundamental_freshness(fixture_db_dir):
    from myra_app import data_readiness as dr

    block = dr.fundamental_facts()
    assert "freshness" in block
    assert block["freshness"]["financials"]["state"] in ff.FRESHNESS_STATES
    assert block["freshness"]["provenance"]["market_cap"]["status"] == "verified"


def test_readiness_reports_unknown_when_no_period_is_recorded(
    fixture_db_dir, monkeypatch
):
    """No reporting period must surface as unknown, never as ready."""
    from myra_app import data_readiness as dr

    monkeypatch.setattr(
        dr,
        "_fundamental_freshness_block",
        lambda p, r: {
            "financials": ff.assess_row(None, None, today=TODAY),
            "provenance": {},
        },
    )
    block = dr._fundamental_freshness_block(None, None)
    assert block["financials"]["state"] == ff.UNKNOWN


def test_readiness_contract_is_json_serialisable(fixture_db_dir):
    import json

    from myra_app import data_readiness as dr

    payload = dr.assess()
    assert json.loads(json.dumps(payload, default=str))
    assert payload["fundamentals"]["freshness"]["financials"]["state"] in (
        ff.FRESHNESS_STATES
    )
