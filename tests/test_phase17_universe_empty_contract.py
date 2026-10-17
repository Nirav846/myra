"""Phase 17: the empty indicator lake must produce a *build* verdict, not an
``AttributeError``.

``universe_loader.load_universe`` returns ``cache_df`` — a DataFrame — on every
path except one: when the indicator lake is empty it returned the literal list
``[]``. ``Engine.run_scan`` then evaluated ``cache_df.empty`` on that list,
raised ``AttributeError``, and classified the whole scan as an opaque
``failed_indicator_computation`` (``'list' object has no attribute 'empty'``).

Because that happened *before* the engine could call
``_abort_empty_universe``, the actionable "the indicator lake is empty" verdict
was unreachable in production. Its Phase 16 unit test passed anyway, because it
called ``_abort_empty_universe`` directly: the unit was correct and the
integration was never asserted. These tests assert the integration.
"""

import pandas as pd

import myra_app.scan_outcome as so
from myra_app.engine import Engine
from myra_app.universe_loader import load_universe

BROAD = [f"S{i}" for i in range(20)]


class _EmptyLakeLibrarian:
    """A librarian whose indicator lake holds nothing."""

    _tech_conn = None

    def connect(self):
        self._tech_conn = True

    def precompute_indicators(self, as_of_date=None):
        return pd.DataFrame()

    def get_active_universe(self):
        return ["S0"]


def _not_a_holiday(monkeypatch):
    monkeypatch.setattr(
        "myra_app.fetcher.DataFetcher._is_holiday", lambda self, dt: False
    )


def _empty_lake(monkeypatch):
    monkeypatch.setattr(
        "myra_app.indicator_lake.status",
        lambda **kwargs: {"present": False, "count": 0, "reason": "missing"},
    )


# ── the contract ────────────────────────────────────────────────────────────


def test_an_empty_lake_returns_a_frame_not_a_list():
    """``cache_df`` must be a DataFrame, because that is what callers index."""
    cache_df, *_ = load_universe(
        _EmptyLakeLibrarian(), BROAD, "2026-10-16", silent=True
    )

    assert isinstance(cache_df, pd.DataFrame)
    assert cache_df.empty


def test_both_empty_paths_agree_on_the_return_type():
    """The ``<10`` fast path and the empty-lake path must not disagree."""
    lib = _EmptyLakeLibrarian()

    narrow, *_ = load_universe(lib, ["S0"], "2026-10-16", silent=True)
    broad, *_ = load_universe(lib, BROAD, "2026-10-16", silent=True)

    assert type(narrow) is type(broad)
    assert narrow.empty and broad.empty


# ── the integration the Phase 16 unit test never covered ────────────────────


def test_a_broad_scan_with_an_empty_lake_reports_a_missing_artifact(monkeypatch):
    """``run_scan`` must reach ``_abort_empty_universe``, not raise.

    Without the fix this raises ``AttributeError: 'list' object has no
    attribute 'empty'`` and the caller sees ``failed_indicator_computation``.
    """
    _not_a_holiday(monkeypatch)
    _empty_lake(monkeypatch)
    engine = Engine(_EmptyLakeLibrarian())

    results, payload = engine.run_scan(
        BROAD, "some_strategy", as_of_date="2026-10-16", silent=True
    )

    assert results == []
    assert payload == {}
    outcome = engine.last_scan_outcome
    assert outcome is not None, "run_scan recorded no verdict"
    assert outcome.status == so.FAILED_MISSING_REQUIRED_ARTIFACTS
    assert outcome.reason == "indicator_lake_empty"
    assert outcome.failed is True
    # The detail must name the remedy, not an internal exception.
    assert "lake" in outcome.detail.lower()
    assert "attribute" not in outcome.detail.lower()


def test_the_verdict_names_the_lake_rather_than_an_internal_error(monkeypatch):
    """A populated-but-unusable lake stays a *shape* problem, not a build one."""
    _not_a_holiday(monkeypatch)
    monkeypatch.setattr(
        "myra_app.indicator_lake.status",
        lambda **kwargs: {"present": True, "count": 5, "reason": "ok"},
    )
    engine = Engine(_EmptyLakeLibrarian())

    engine.run_scan(BROAD, "some_strategy", as_of_date="2026-10-16", silent=True)

    outcome = engine.last_scan_outcome
    assert outcome.status == so.FAILED_MISSING_REQUIRED_ARTIFACTS
    assert outcome.reason == "no_precomputed_rows"
    assert "5" in outcome.detail
