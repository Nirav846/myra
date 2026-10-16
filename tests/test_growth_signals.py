"""Tests for the validated annual growth signal (Phase 14).

Every test here is pure computation or in-memory SQLite. No test opens a
production database — ``tests/conftest.py`` fails the whole session if one does.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys

import pytest

from myra_app import growth_signals as gs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import compare_v25_orderings as tool  # noqa: E402

FRESH = "2026-09-01T03:50:37.719715+00:00"

# Mirrors the deployed DDL (schema/valuation.sql) so the adapter is exercised
# against the real column set rather than a convenient subset.
DDL = """
CREATE TABLE upstox_income_statement (
    isin TEXT NOT NULL,
    symbol TEXT NOT NULL,
    period TEXT NOT NULL,
    report_type TEXT NOT NULL,
    time_period TEXT NOT NULL,
    units_in TEXT,
    revenue REAL,
    operating_profit REAL,
    net_profit REAL,
    revenue_change TEXT,
    operating_profit_change TEXT,
    net_profit_change TEXT,
    raw_json TEXT,
    fetched_at TEXT,
    PRIMARY KEY (isin, period, report_type, time_period)
)
"""


def _rec(
    symbol,
    period,
    net,
    revenue=None,
    fetched_at=FRESH,
    time_period="yearly",
    isin="INE000000001",
    change=None,
):
    return gs.AnnualRecord(
        symbol=symbol,
        period=period,
        net_profit=net,
        revenue=revenue,
        isin=isin,
        time_period=time_period,
        net_profit_change=change,
        fetched_at=fetched_at,
    )


class _NoCloseConnection(sqlite3.Connection):
    """Keeps an in-memory source inspectable after the tool closes it."""

    def close(self):  # pragma: no cover - intentionally a no-op
        pass


def _memory_conn(rows, keep_open=False):
    factory = _NoCloseConnection if keep_open else sqlite3.Connection
    conn = sqlite3.connect(":memory:", factory=factory)
    conn.execute(DDL)
    for index, row in enumerate(rows):
        conn.execute(
            "INSERT INTO upstox_income_statement (isin, symbol, period,"
            " report_type, time_period, units_in, revenue, net_profit,"
            " net_profit_change, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row.get("isin") or f"INE{index:09d}",
                row["symbol"],
                row["period"],
                "consolidated",
                row.get("time_period", "yearly"),
                "crore",
                row.get("revenue"),
                row.get("net_profit"),
                row.get("net_profit_change"),
                row.get("fetched_at", FRESH),
            ),
        )
    conn.commit()
    return conn


def _snapshot_row(symbol, **extra):
    row = {
        "Stock": symbol,
        "close": 100.0,
        "sma50": 95.0,
        "sma150": 90.0,
        "sma200": 85.0,
        "Market_Mood": "NEUTRAL",
    }
    row.update(extra)
    return row


def _write_snapshot(path, rows):
    fields = sorted({key for row in rows for key in row})
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def test_year_over_year_uses_the_absolute_prior_base():
    assert gs.year_over_year(110.0, 100.0) == pytest.approx(10.0)
    assert gs.year_over_year(90.0, 100.0) == pytest.approx(-10.0)
    # A recovery from a loss reads positive because the denominator is |prior|,
    # which is the provider's own convention.
    assert gs.year_over_year(50.0, -100.0) == pytest.approx(150.0)
    assert gs.year_over_year(-50.0, 100.0) == pytest.approx(-150.0)


def test_year_over_year_refuses_missing_inputs_and_zero_base():
    assert gs.year_over_year(None, 100.0) is None
    assert gs.year_over_year(100.0, None) is None
    assert gs.year_over_year(100.0, 0.0) is None
    assert gs.year_over_year(100.0, 0) is None


def test_valid_zero_growth_is_a_real_observation_not_a_missing_marker():
    records = [_rec("FLAT", "Mar 2025", 100.0), _rec("FLAT", "Mar 2026", 100.0)]
    observation = gs.derive_growth(records, reference_year=2026)["FLAT"]

    assert observation.eligible is True
    assert observation.profit_growth == pytest.approx(0.0)
    assert observation.profit_growth is not None
    assert gs.growth_inputs({"FLAT": observation}) == {"FLAT": {"ProfitGrowth": 0.0}}


def test_negative_growth_is_preserved():
    records = [_rec("DROP", "Mar 2025", 200.0), _rec("DROP", "Mar 2026", 150.0)]
    observation = gs.derive_growth(records, reference_year=2026)["DROP"]

    assert observation.eligible is True
    assert observation.profit_growth == pytest.approx(-25.0)


def test_revenue_growth_is_derived_but_is_not_a_scorer_input():
    """Revenue growth is carried for context; it cannot move a ranking."""
    from myra_app.ranking_inputs import METRIC_KEYS

    assert gs.REVENUE_METRIC_KEY not in METRIC_KEYS
    assert gs.GROWTH_METRIC_KEY in METRIC_KEYS

    records = [
        _rec("REV", "Mar 2025", 100.0, revenue=1000.0),
        _rec("REV", "Mar 2026", 120.0, revenue=1250.0),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["REV"]

    assert observation.profit_growth == pytest.approx(20.0)
    assert observation.sales_growth == pytest.approx(25.0)
    # Only the profit metric is offered to the scorer.
    assert gs.growth_inputs({"REV": observation}) == {
        "REV": {"ProfitGrowth": pytest.approx(20.0)}
    }


def test_revenue_growth_is_unavailable_when_revenue_is_absent():
    records = [
        _rec("NOREV", "Mar 2025", 100.0),
        _rec("NOREV", "Mar 2026", 120.0),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["NOREV"]

    assert observation.eligible is True
    assert observation.sales_growth is None


def test_revenue_growth_derives_for_eligible_symbol_and_stays_none_when_absent():
    records = [
        _rec("WITHREV", "Mar 2025", 100.0, revenue=1000.0),
        _rec("NOREV", "Mar 2025", 100.0),
        _rec("WITHREV", "Mar 2026", 150.0, revenue=1250.0),
        _rec("NOREV", "Mar 2026", 150.0),
    ]
    observations = gs.derive_growth(records, reference_year=2026)

    assert observations["WITHREV"].sales_growth == pytest.approx(25.0)
    assert observations["NOREV"].profit_growth == pytest.approx(50.0)
    assert observations["NOREV"].sales_growth is None


# ---------------------------------------------------------------------------
# Fiscal year ends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("month", ["Mar", "Jun", "Sep", "Dec", "Nov"])
def test_company_specific_fiscal_year_ends_are_respected(month):
    records = [
        _rec("ACME", f"{month} 2025", 100.0),
        _rec("ACME", f"{month} 2026", 125.0),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.eligible is True
    assert observation.profit_growth == pytest.approx(25.0)
    assert observation.latest_period == f"{month} 2026"
    assert observation.prior_period == f"{month} 2025"


def test_the_cohort_with_the_newest_period_wins():
    """A company with more than one annual series is read at its newest one."""
    records = [
        _rec("ACME", "Dec 2018", 50.0),
        _rec("ACME", "Dec 2019", 60.0),
        _rec("ACME", "Mar 2025", 100.0),
        _rec("ACME", "Mar 2026", 120.0),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.eligible is True
    assert observation.latest_period == "Mar 2026"
    assert observation.profit_growth == pytest.approx(20.0)


def test_a_single_fiscal_year_end_does_not_produce_a_pair():
    records = [_rec("ACME", "Mar 2025", 100.0), _rec("ACME", "Dec 2026", 120.0)]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_NO_PRIOR_PERIOD


# ---------------------------------------------------------------------------
# Eligibility gates
# ---------------------------------------------------------------------------


def test_missing_prior_period_is_unavailable():
    observation = gs.derive_growth(
        [_rec("ACME", "Mar 2026", 150.0)], reference_year=2026
    )["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_NO_PRIOR_PERIOD


def test_non_consecutive_annual_records_are_unavailable():
    records = [_rec("ACME", "Mar 2024", 100.0), _rec("ACME", "Mar 2026", 150.0)]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_NON_CONSECUTIVE


def test_missing_net_profit_is_unavailable():
    records = [_rec("ACME", "Mar 2025", None), _rec("ACME", "Mar 2026", 150.0)]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_MISSING_VALUES


def test_prior_base_below_one_crore_is_unavailable_but_the_floor_is_configurable():
    records = [_rec("SMALL", "Mar 2025", 0.5), _rec("SMALL", "Mar 2026", 1.0)]

    refused = gs.derive_growth(records, reference_year=2026)["SMALL"]
    assert refused.profit_growth is None
    assert refused.reason == gs.REASON_BASE_BELOW_FLOOR
    assert refused.prior_net_profit_cr == pytest.approx(0.5)

    allowed = gs.derive_growth(
        records,
        policy=gs.EligibilityPolicy(min_prior_base_cr=0.0),
        reference_year=2026,
    )["SMALL"]
    assert allowed.eligible is True
    assert allowed.profit_growth == pytest.approx(100.0)


def test_zero_prior_base_is_unavailable():
    records = [_rec("ZERO", "Mar 2025", 0.0), _rec("ZERO", "Mar 2026", 5.0)]
    observation = gs.derive_growth(records, reference_year=2026)["ZERO"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_ZERO_BASE


def test_stale_period_is_unavailable_while_the_fresh_symbol_is_kept():
    records = [
        _rec("NEW", "Mar 2025", 100.0),
        _rec("NEW", "Mar 2026", 120.0),
        _rec("OLD", "Mar 2023", 100.0),
        _rec("OLD", "Mar 2024", 120.0),
    ]
    observations = gs.derive_growth(records)

    assert observations["NEW"].eligible is True
    assert observations["OLD"].profit_growth is None
    assert observations["OLD"].reason == gs.REASON_STALE


def test_missing_freshness_metadata_is_unavailable_unless_waived():
    records = [
        _rec("ACME", "Mar 2025", 100.0, fetched_at=None),
        _rec("ACME", "Mar 2026", 120.0, fetched_at=None),
    ]

    refused = gs.derive_growth(records, reference_year=2026)["ACME"]
    assert refused.profit_growth is None
    assert refused.reason == gs.REASON_MISSING_FRESHNESS

    waived = gs.derive_growth(
        records,
        policy=gs.EligibilityPolicy(require_freshness_metadata=False),
        reference_year=2026,
    )["ACME"]
    assert waived.eligible is True


def test_policy_rejects_impossible_thresholds():
    with pytest.raises(ValueError):
        gs.EligibilityPolicy(min_prior_base_cr=-1.0)
    with pytest.raises(ValueError):
        gs.EligibilityPolicy(freshness_years=-1)


# ---------------------------------------------------------------------------
# Malformed and conflicting source records
# ---------------------------------------------------------------------------


def test_parse_period_accepts_only_well_formed_labels():
    parsed = gs.parse_period("Mar 2026")
    assert parsed is not None and (parsed.year, parsed.month) == (2026, 3)

    for bad in (None, "", "Mar", "2026", "Mar2026", "Q1 2026", "Mar 26", "Mar 3000"):
        assert gs.parse_period(bad) is None


def test_an_unparseable_period_cannot_be_used_as_the_prior():
    records = [_rec("ACME", "Mar 2026", 150.0), _rec("ACME", "bogus", 100.0)]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_NO_PRIOR_PERIOD


def test_duplicate_identical_records_collapse_to_one():
    records = [
        _rec("ACME", "Mar 2025", 100.0),
        _rec("ACME", "Mar 2026", 120.0),
        _rec("ACME", "Mar 2026", 120.0),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.eligible is True
    assert observation.profit_growth == pytest.approx(20.0)


def test_conflicting_records_for_one_period_are_unavailable():
    records = [
        _rec("ACME", "Mar 2025", 100.0),
        _rec("ACME", "Mar 2026", 120.0, isin="INE000000001"),
        _rec("ACME", "Mar 2026", 300.0, isin="INE000000002"),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_CONFLICTING


# ---------------------------------------------------------------------------
# Quarterly data can never produce an annual YoY figure
# ---------------------------------------------------------------------------


def test_a_symbol_with_only_quarterly_rows_has_no_annual_signal():
    records = [
        _rec("ACME", "Mar 2026", 90.0, time_period="quarterly"),
        _rec("ACME", "Jun 2026", 100.0, time_period="quarterly"),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_NO_ANNUAL_RECORDS


def test_quarterly_rows_cannot_supply_a_missing_prior_period():
    records = [
        _rec("ACME", "Mar 2026", 100.0),
        _rec("ACME", "Dec 2025", 10.0, time_period="quarterly"),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.profit_growth is None
    assert observation.reason == gs.REASON_NO_PRIOR_PERIOD


def test_quarterly_and_annual_rows_for_the_same_label_do_not_mix():
    """Only the annual row may be used, even when a quarterly row shares a label."""
    records = [
        _rec("ACME", "Mar 2025", 100.0),
        _rec("ACME", "Mar 2026", 120.0),
        # A quarterly row for the same label carrying a wildly different level:
        # if it leaked in, the derived growth would be wildly wrong.
        _rec("ACME", "Mar 2026", 9999.0, time_period="quarterly"),
    ]
    observation = gs.derive_growth(records, reference_year=2026)["ACME"]

    assert observation.eligible is True
    assert observation.profit_growth == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Unavailable stays unavailable
# ---------------------------------------------------------------------------


def test_unavailable_growth_is_never_converted_to_zero():
    records = [
        _rec("ONLY", "Mar 2026", 100.0),
        _rec("STALE", "Mar 2023", 100.0),
        _rec("STALE", "Mar 2024", 120.0),
    ]
    observations = gs.derive_growth(records)

    for observation in observations.values():
        assert observation.profit_growth is None
        assert not observation.eligible
    assert gs.growth_inputs(observations) == {}
    assert records  # the source records are untouched


def test_growth_inputs_include_only_eligible_symbols():
    records = [
        _rec("GOOD", "Mar 2025", 100.0),
        _rec("GOOD", "Mar 2026", 150.0),
        _rec("BAD", "Mar 2026", 150.0),
    ]
    observations = gs.derive_growth(records, reference_year=2026)
    inputs = gs.growth_inputs(observations)

    assert set(inputs) == {"GOOD"}
    assert inputs["GOOD"]["ProfitGrowth"] == pytest.approx(50.0)
    assert all(value is not None for row in inputs.values() for value in row.values())


def test_coverage_report_attributes_every_symbol_to_a_reason():
    records = [
        _rec("GOOD", "Mar 2025", 100.0),
        _rec("GOOD", "Mar 2026", 150.0),
        _rec("LONELY", "Mar 2026", 150.0),
    ]
    report = gs.coverage_report(gs.derive_growth(records, reference_year=2026))

    assert report["symbols"] == 2
    assert report["eligible"] == 1
    assert report["unavailable"] == 1
    assert sum(report["reasons"].values()) == 2
    assert report["reasons"][gs.REASON_NO_PRIOR_PERIOD] == 1


def test_parse_percent_reads_the_provider_change_format():
    assert gs.parse_percent("+10.18%") == pytest.approx(10.18)
    assert gs.parse_percent("-8.63%") == pytest.approx(-8.63)
    assert gs.parse_percent("0.00%") == pytest.approx(0.0)
    assert gs.parse_percent(None) is None
    assert gs.parse_percent("") is None
    assert gs.parse_percent("n/a") is None


def test_parse_fetched_at_accepts_iso_and_rejects_junk():
    assert gs.parse_fetched_at(FRESH) is not None
    assert gs.parse_fetched_at(None) is None
    assert gs.parse_fetched_at("") is None
    assert gs.parse_fetched_at("not a timestamp") is None


# ---------------------------------------------------------------------------
# Read-only source access
# ---------------------------------------------------------------------------


def test_read_annual_records_mirrors_the_table_and_filters_symbols():
    conn = _memory_conn(
        [
            {
                "symbol": "ACME",
                "period": "Mar 2026",
                "net_profit": 120.0,
                "revenue": 500.0,
                "net_profit_change": "+20.00%",
            },
            {
                "symbol": "ACME",
                "period": "Jun 2026",
                "time_period": "quarterly",
                "net_profit": 30.0,
            },
        ]
    )
    try:
        records = gs.read_annual_records(conn)
        assert len(records) == 2
        assert {record.time_period for record in records} == {"yearly", "quarterly"}

        yearly = gs.read_annual_records(conn, time_period=gs.ANNUAL_TIME_PERIOD)
        assert len(yearly) == 1
        assert yearly[0].net_profit == pytest.approx(120.0)
        assert yearly[0].net_profit_change == "+20.00%"

        assert gs.read_annual_records(conn, symbols=["acme"]) != []
        assert gs.read_annual_records(conn, symbols=["ABSENT"]) == []
    finally:
        conn.close()


def test_connect_readonly_builds_a_read_only_uri(monkeypatch):
    seen = {}
    real_connect = sqlite3.connect

    def fake_connect(database, *args, **kwargs):
        seen["uri"] = database
        seen["uri_kwarg"] = kwargs.get("uri")
        return real_connect(":memory:")

    monkeypatch.setattr(gs.sqlite3, "connect", fake_connect)
    conn = gs.connect_readonly()
    conn.close()

    assert seen["uri"].startswith("file:")
    assert seen["uri"].endswith("?mode=ro")
    assert seen["uri_kwarg"] is True


def test_derive_growth_is_pure_and_does_not_touch_the_source_records():
    records = [_rec("ACME", "Mar 2025", 100.0), _rec("ACME", "Mar 2026", 120.0)]
    before = list(records)
    gs.derive_growth(records, reference_year=2026)
    assert records == before


# ---------------------------------------------------------------------------
# Tool integration: default path unchanged, growth mode additive and read-only
# ---------------------------------------------------------------------------


def test_live_baseline_frame_does_not_mutate_its_input():
    original = _snapshot_row("ACME", roe=15.0, ProfitGrowth=0.0, MYRA_Score_v25=25.0)
    rows = [dict(original)]

    frame = tool._live_baseline_frame(rows)

    assert rows[0] == original
    assert "roe" not in frame[0]
    assert "ProfitGrowth" not in frame[0]
    assert "MYRA_Score_v25" not in frame[0]
    assert frame[0]["Stock"] == "ACME"


def test_populated_rows_skips_rows_without_a_technical_block():
    rows = [
        _snapshot_row("GOOD"),
        _snapshot_row("BLANK", close=None, sma50=None, sma150=None, sma200=None),
    ]
    assert [row["Stock"] for row in tool._populated_rows(rows)] == ["GOOD"]


def test_default_tool_mode_is_unchanged_and_never_fetches_growth(
    tmp_path, monkeypatch, capsys
):
    def boom(*args, **kwargs):
        raise AssertionError("growth signals must not be fetched by default")

    monkeypatch.setattr(gs, "read_annual_records", boom)
    monkeypatch.setattr(gs, "connect_readonly", boom)
    # No database access at all in this path.
    monkeypatch.setattr(tool, "_gather_inputs", lambda *a, **k: {})

    snapshot = _write_snapshot(
        tmp_path / "scan.csv",
        [_snapshot_row("AAA"), _snapshot_row("BBB"), _snapshot_row("CCC")],
    )
    assert tool.main(["--snapshot", snapshot, "--top-n", "3"]) == 0
    assert "identical ordering     : True" in capsys.readouterr().out


def test_growth_mode_reads_only_and_writes_nothing(tmp_path, monkeypatch):
    source = _memory_conn(
        [
            {
                "symbol": "ACME",
                "period": "Mar 2025",
                "net_profit": 100.0,
            },
            {
                "symbol": "ACME",
                "period": "Mar 2026",
                "net_profit": 150.0,
            },
        ],
        keep_open=True,
    )
    baseline_writes = source.total_changes

    def boom(*args, **kwargs):
        raise AssertionError("the production connection must not be used here")

    monkeypatch.setattr(gs, "connect_readonly", boom)
    monkeypatch.setattr(tool, "_ro_connect", lambda key: source)

    snapshot = _write_snapshot(
        tmp_path / "scan.csv",
        [_snapshot_row("ACME"), _snapshot_row("OTHER")],
    )
    assert (
        tool.main(
            [
                "--snapshot",
                snapshot,
                "--growth-source",
                "upstox-annual",
                "--top-n",
                "2",
            ]
        )
        == 0
    )
    # total_changes counts rows modified since the connection was opened: a
    # read-only run leaves it exactly where setup left it.
    assert source.total_changes == baseline_writes
    source.close()


def test_growth_shadow_separates_source_eligibility_from_frame_coverage(
    tmp_path, monkeypatch
):
    source = _memory_conn(
        [
            {"symbol": "ACME", "period": "Mar 2025", "net_profit": 100.0},
            {"symbol": "ACME", "period": "Mar 2026", "net_profit": 150.0},
            {"symbol": "ABSENT", "period": "Mar 2025", "net_profit": 100.0},
            {"symbol": "ABSENT", "period": "Mar 2026", "net_profit": 160.0},
        ],
        keep_open=True,
    )
    monkeypatch.setattr(tool, "_ro_connect", lambda key: source)

    snapshot = _write_snapshot(
        tmp_path / "scan.csv", [_snapshot_row("ACME"), _snapshot_row("OTHER")]
    )
    report = tool._growth_shadow(
        snapshot,
        tool._load_rows(snapshot),
        "NEUTRAL",
        2,
        gs.EligibilityPolicy(),
    )

    assert report["source_coverage"]["eligible"] == 2
    assert report["frame_symbols"] == 2
    assert report["frame_eligible"] == 1
    assert report["candidate"]["coverage"]["ProfitGrowth"] == 1
    assert report["baseline"]["coverage"]["ProfitGrowth"] == 0
    source.close()


def test_live_baseline_frame_strips_every_opt_in_input():
    row = _snapshot_row("ACME")
    for field in tool.LIVE_ABSENT_FIELDS:
        row[field] = 1.0
    frame = tool._live_baseline_frame([row])

    assert not set(tool.LIVE_ABSENT_FIELDS) & set(frame[0])


def test_regime_defaults_to_neutral_when_unusable():
    assert tool._regime_of([]) == "NEUTRAL"
    assert tool._regime_of([{"Stock": "A"}]) == "NEUTRAL"
    assert tool._regime_of([{"Market_Mood": ""}]) == "NEUTRAL"
    assert tool._regime_of([{"Market_Mood": "BULL"}]) == "BULL"
