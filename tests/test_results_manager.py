"""Final ranking/sorting correctness for the results the user actually sees.

``ResultsManager.apply_global_ranking`` decides the order of the results table
and had no test. Sorting is silent by nature, and a missing optional column used
to disable ranking entirely (the broad ``except`` returned the rows unsorted),
so these lock both the ordering contract and that resilience.
"""

import pandas as pd
import pytest

from myra_app.results_manager import ResultsManager


@pytest.fixture
def rm(monkeypatch, tmp_path):
    # ResultsManager writes report/chart dirs under cwd; keep that out of the repo.
    monkeypatch.chdir(tmp_path)
    return ResultsManager()


def _row(sym, stage="Stage 3", stars="", flow="0 Cr", anomaly=0, **extra):
    row = {
        "Stock": sym,
        "LTP": 100,
        "Stage": stage,
        "Stars": stars,
        "Money_Flow": flow,
        "Anomaly_Score": anomaly,
    }
    row.update(extra)
    return row


def _order(rm, rows):
    out = rm.apply_global_ranking(pd.DataFrame(rows))
    return out["Stock"].tolist()


def test_stage_dominance_orders_stage2_first(rm):
    rows = [
        _row("A", stage="Stage 4"),
        _row("B", stage="Stage 2"),
        _row("C", stage="Stage 1"),
        _row("D", stage="Stage 3"),
    ]
    assert _order(rm, rows) == ["B", "C", "D", "A"]


def test_anomaly_outranks_stage(rm):
    rows = [
        _row("stage2", stage="Stage 2", anomaly=0),
        _row("stage4", stage="Stage 4", anomaly=5),
    ]
    assert _order(rm, rows) == ["stage4", "stage2"]


def test_stars_break_ties_within_same_stage_and_anomaly(rm):
    rows = [_row("few", stars="*"), _row("many", stars="*****")]
    assert _order(rm, rows) == ["many", "few"]


def test_money_flow_breaks_final_ties_and_parses_indian_format(rm):
    rows = [
        _row("small", flow="10 Cr"),
        _row("big", flow="\u20b91,234.5 Cr"),
    ]
    assert _order(rm, rows) == ["big", "small"]


def test_missing_stars_column_still_ranks(rm):
    # Regression: a scan without a Stars column used to return rows unsorted.
    rows = [
        {k: v for k, v in _row("stage1", stage="Stage 1").items() if k != "Stars"},
        {k: v for k, v in _row("stage2", stage="Stage 2").items() if k != "Stars"},
    ]
    assert _order(rm, rows) == ["stage2", "stage1"]


def test_missing_stage_column_still_ranks(rm):
    rows = [
        {k: v for k, v in _row("low", anomaly=0).items() if k != "Stage"},
        {k: v for k, v in _row("high", anomaly=9).items() if k != "Stage"},
    ]
    assert _order(rm, rows) == ["high", "low"]


def test_ranking_is_stable_for_identical_input(rm):
    rows = [_row(s, stage="Stage 2", anomaly=i) for i, s in enumerate(["A", "B", "C"])]
    first = _order(rm, rows)
    second = _order(rm, rows)
    assert first == second


def test_ranking_columns_are_not_leaked(rm):
    out = rm.apply_global_ranking(pd.DataFrame([_row("A")]))
    assert not [c for c in out.columns if str(c).startswith("_")]


def test_sanitize_results_drops_rows_without_a_price(rm):
    rows = [
        {"Stock": "OK", "LTP": 100},
        {"Stock": "", "LTP": 1},
        {"Stock": "DASH", "LTP": "-"},
        {"Stock": "NONE", "LTP": None},
    ]
    out = rm.sanitize_results(rows)
    assert [r["Stock"] for r in out] == ["OK"]


def test_sanitize_results_empty_is_empty(rm):
    assert rm.sanitize_results([]) == []
