"""Phase 16: a failed scan must never look like a quiet market.

``Engine.run_scan`` used to catch its own exceptions, print a line, and return
``([], {})`` — byte-identical to a strategy that legitimately matched nothing.
The operator could not tell "the market offered nothing today" from "MYRA could
not read its own indicator data", which is how an empty indicator lake survived
unnoticed. These tests pin the vocabulary that replaces that ambiguity and the
two places the engine now records it.
"""

import pytest

import myra_app.scan_outcome as so
from myra_app.engine import Engine, _classify_failure


class _StubLibrarian:
    """The only surface the engine touches before it produces an outcome."""

    _tech_conn = None

    def connect(self):
        self._tech_conn = True

    def get_active_universe(self):
        return ["S0"]


# ── 1. The vocabulary is closed and self-describing ─────────────────────────


def test_success_and_failure_partition_the_vocabulary():
    assert set(so.SUCCESS_STATUSES).isdisjoint(so.FAILURE_STATUSES)
    assert set(so.ALL_STATUSES) == set(so.SUCCESS_STATUSES) | set(so.FAILURE_STATUSES)


def test_every_status_has_a_plain_language_label():
    for status in so.ALL_STATUSES:
        assert so.STATUS_LABELS[status]


def test_an_unknown_status_is_rejected_loudly():
    with pytest.raises(ValueError):
        so.ScanOutcome(status="probably_fine", reason="trust_me")


def test_no_candidates_is_a_success_not_a_failure():
    """A zero-result scan is information, not an error."""
    outcome = so.ScanOutcome(status=so.SUCCESS_NO_CANDIDATES, reason="no_match")

    assert outcome.ok is True
    assert outcome.failed is False
    assert outcome.has_candidates is False


def test_only_the_candidates_status_reports_hits():
    with_hits = so.ScanOutcome(
        status=so.SUCCESS_WITH_CANDIDATES, reason="matched", candidates=3
    )

    assert with_hits.ok is True
    assert with_hits.has_candidates is True
    assert with_hits.as_dict()["candidates"] == 3


def test_every_failure_status_is_not_ok():
    for status in so.FAILURE_STATUSES:
        outcome = so.ScanOutcome(status=status, reason="r")
        assert outcome.ok is False
        assert outcome.failed is True
        assert outcome.has_candidates is False


def test_describe_keeps_the_code_and_the_prose_separate():
    outcome = so.ScanOutcome(
        status=so.FAILED_MISSING_REQUIRED_ARTIFACTS,
        reason="indicator_lake_empty",
        detail="no precomputed indicators exist to load",
    )

    described = outcome.describe()

    assert "indicator_lake_empty" in described
    assert "no precomputed indicators exist to load" in described
    assert described.startswith(so.STATUS_LABELS[outcome.status])


def test_describe_omits_an_empty_detail():
    outcome = so.ScanOutcome(status=so.SUCCESS_NO_CANDIDATES, reason="no_match")

    assert outcome.describe() == "Completed, nothing matched (no_match)"


# ── 2. Exceptions are classified by their remedy ────────────────────────────


@pytest.mark.parametrize(
    "exception,expected",
    [
        (FileNotFoundError("no such artifact"), so.FAILED_MISSING_REQUIRED_ARTIFACTS),
        (AssertionError("no symbol column"), so.FAILED_INVALID_INPUT_SCHEMA),
        (KeyError("close"), so.FAILED_INVALID_INPUT_SCHEMA),
        (IndexError("empty index"), so.FAILED_INVALID_INPUT_SCHEMA),
        (ValueError("bad dtype"), so.FAILED_INDICATOR_COMPUTATION),
        (TypeError("unsupported operand"), so.FAILED_INDICATOR_COMPUTATION),
        (ZeroDivisionError("division by zero"), so.FAILED_INDICATOR_COMPUTATION),
        (RuntimeError("boom"), so.FAILED_INTERNAL_ERROR),
    ],
)
def test_failures_are_classified_by_what_would_fix_them(exception, expected):
    status, reason = _classify_failure(exception)

    assert status == expected
    assert reason
    assert status in so.FAILURE_STATUSES


# ── 3. The engine's outcome channel ─────────────────────────────────────────


def test_outcome_channel_starts_unset():
    """``None`` means "no scan has run", which is not a failure."""
    engine = Engine(_StubLibrarian())

    assert engine.last_scan_outcome is None


def test_an_empty_lake_is_a_build_problem_not_a_quiet_market(monkeypatch):
    monkeypatch.setattr(
        "myra_app.indicator_lake.status",
        lambda **kwargs: {"present": False, "count": 0, "reason": "missing"},
    )
    engine = Engine(_StubLibrarian())
    symbols = [f"S{i}" for i in range(20)]

    results, payload = engine._abort_empty_universe(symbols, True)

    assert results == []
    assert payload == {}
    outcome = engine.last_scan_outcome
    assert outcome.status == so.FAILED_MISSING_REQUIRED_ARTIFACTS
    assert outcome.reason == "indicator_lake_empty"
    assert outcome.failed is True
    assert "lake" in outcome.detail


def test_a_populated_lake_that_yields_nothing_is_a_shape_problem(monkeypatch):
    monkeypatch.setattr(
        "myra_app.indicator_lake.status",
        lambda **kwargs: {"present": True, "count": 7, "reason": "ok"},
    )
    engine = Engine(_StubLibrarian())

    engine._abort_empty_universe([f"S{i}" for i in range(20)], True)

    outcome = engine.last_scan_outcome
    assert outcome.status == so.FAILED_MISSING_REQUIRED_ARTIFACTS
    assert outcome.reason == "no_precomputed_rows"
    assert "7" in outcome.detail


def test_an_unusable_probe_does_not_mask_the_abort(monkeypatch):
    """A probe that raises must not hide the failure it was asked to explain."""

    def _boom(**kwargs):
        raise OSError("cannot list the lake")

    monkeypatch.setattr("myra_app.indicator_lake.status", _boom)
    engine = Engine(_StubLibrarian())

    engine._abort_empty_universe([f"S{i}" for i in range(20)], True)

    assert engine.last_scan_outcome.failed is True
    assert engine.last_scan_outcome.reason == "indicator_lake_empty"


# ── 4. ``run_scan`` records a verdict on every exit path ────────────────────


def _holiday(monkeypatch, snapshot):
    monkeypatch.setattr(
        "myra_app.fetcher.DataFetcher._is_holiday", lambda self, d: True
    )

    class _ResultsManager:
        def load_last_snapshot(self, strategy_name):
            return snapshot

    monkeypatch.setattr("myra_app.results_manager.ResultsManager", _ResultsManager)


def test_a_holiday_with_a_snapshot_reports_what_it_served(monkeypatch):
    _holiday(monkeypatch, [{"symbol": "S0"}])
    engine = Engine(_StubLibrarian())

    results, payload = engine.run_scan(["S0"], "some_strategy", silent=True)

    assert results == [{"symbol": "S0"}]
    assert payload["status"] == "HOLIDAY_SNAPSHOT"
    assert engine.last_scan_outcome.status == so.SUCCESS_WITH_CANDIDATES
    assert engine.last_scan_outcome.has_candidates is True


def test_a_holiday_without_a_snapshot_is_a_real_verdict(monkeypatch):
    _holiday(monkeypatch, None)
    engine = Engine(_StubLibrarian())

    results, payload = engine.run_scan(
        ["S0"], "some_strategy", as_of_date="2026-01-26", silent=True
    )

    assert results == []
    assert payload["status"] == "HOLIDAY_NO_DATA"
    # Nothing was screened, but that is a fact about the calendar, not a fault.
    assert engine.last_scan_outcome.status == so.SUCCESS_NO_CANDIDATES
    assert engine.last_scan_outcome.reason == "holiday_no_data"


def test_a_previous_verdict_is_cleared_at_the_start_of_a_run(monkeypatch):
    _holiday(monkeypatch, None)
    engine = Engine(_StubLibrarian())
    engine.last_scan_outcome = so.ScanOutcome(
        status=so.FAILED_INTERNAL_ERROR, reason="stale_verdict"
    )

    engine.run_scan(["S0"], "some_strategy", as_of_date="2026-01-26", silent=True)

    assert engine.last_scan_outcome.reason == "holiday_no_data"
