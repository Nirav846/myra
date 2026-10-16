"""Phase 12 — missing-data safety in investment decisions.

The defects under test were all confirmed against the live database first
(see tools/phase12_decision_impact.py and tools/phase12_real_impact.py). These
tests pin the corrected semantics and, just as importantly, pin the cases that
must NOT change:

* a valid numeric zero is still a zero, not a null;
* a well-measured row keeps its established score magnitude and UI band;
* Phase 6 ranking inputs remain opt-in;
* ``smart_money_score`` stays unavailable - no proxy is invented for it.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from myra_web.routes.fund_traction import quality_score_sql

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The pre-Phase-12 expression, kept verbatim so the before/after comparison in
#: the tests measures the real regression rather than a paraphrase.
OLD_SQL = """
ROUND(
    COALESCE(f.roe, 0) * 0.4
    + COALESCE(f.net_margin, 0) * 0.3
    + (100 - MIN(COALESCE(f.pe, 100), 100)) * 0.3, 2
)
"""


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def score_db(tmp_path):
    """Synthetic fundamentals with one row per scenario under test."""
    conn = sqlite3.connect(tmp_path / "val.db")
    conn.execute(
        "CREATE TABLE fundamentals (symbol TEXT, roe REAL, net_margin REAL, pe REAL)"
    )
    rows = [
        # symbol, roe, net_margin, pe
        ("COMPLETE", 25.0, 0.15, 20.0),
        ("NO_ROE", None, 0.15, 20.0),
        ("NO_PE", 25.0, 0.15, None),
        ("LOSS", None, None, -341.7),
        ("CORRUPT_NM", 25.0, -26570.0, 20.0),
        ("ALL_MISSING", None, None, None),
        ("ZERO_ROE", 0.0, None, 20.0),
        ("ZERO_PE", 25.0, None, 0.0),
        ("NEG_ROE", -50.0, None, 20.0),
        ("HIGH_PE", 25.0, None, 10000.0),
        ("MID_PE", 25.0, None, 150.0),
    ]
    conn.executemany("INSERT INTO fundamentals VALUES (?,?,?,?)", rows)
    conn.commit()
    yield conn
    conn.close()


def _score(conn, symbol):
    row = conn.execute(
        f"SELECT ({quality_score_sql('f')}) FROM fundamentals f WHERE f.symbol = ?",
        (symbol,),
    ).fetchone()
    return row[0]


def _old(conn, symbol):
    row = conn.execute(
        f"SELECT ({OLD_SQL}) FROM fundamentals f WHERE f.symbol = ?", (symbol,)
    ).fetchone()
    return row[0]


# ── missing data must not become a fabricated number ─────────────────────────


def test_nothing_known_yields_unavailable_not_zero(score_db):
    """ "We know nothing" is not the same claim as "quality is 0"."""
    assert _score(score_db, "ALL_MISSING") is None
    assert _old(score_db, "ALL_MISSING") == 0.0, "old behaviour fabricated a 0"


def test_absent_roe_no_longer_subtracts_points(score_db):
    """85% of real rows have no ROE; being unmeasured was a de facto penalty."""
    new = _score(score_db, "NO_ROE")
    old = _old(score_db, "NO_ROE")
    assert new is not None
    assert new > old, f"missing ROE should not lower the score ({old} -> {new})"


def test_absent_pe_no_longer_subtracts_points(score_db):
    new = _score(score_db, "NO_PE")
    assert new is not None
    assert new > _old(score_db, "NO_PE")


def test_a_loss_making_company_is_no_longer_the_highest_score(score_db):
    """Negative P/E had no floor: pe=-341.7 scored 441.7 raw points."""
    assert (
        _score(score_db, "LOSS") is None
    ), "a negative P/E carries no cheapness information"
    assert _old(score_db, "LOSS") > 100, "old behaviour rewarded losses"


def test_corrupt_net_margin_no_longer_dominates_the_score(score_db):
    """A -26,570 mixed-scale value contributed -7,971 and swamped everything."""
    corrupt = _score(score_db, "CORRUPT_NM")
    complete = _score(score_db, "COMPLETE")
    assert corrupt is not None and complete is not None
    assert corrupt == pytest.approx(
        complete, abs=0.1
    ), "the untrustworthy component must no longer move the score"


# ── valid zero and valid values are preserved ────────────────────────────────


def test_a_valid_zero_roe_still_flows_through(score_db):
    """A breakeven company genuinely has ROE 0 - that is not missing data."""
    score = _score(score_db, "ZERO_ROE")
    assert score is not None
    assert score == pytest.approx(_old(score_db, "ZERO_ROE"), abs=0.5)


def test_a_zero_p_e_is_treated_as_unavailable_not_as_free(score_db):
    """P/E of exactly 0 is not a valuation multiple (it implies infinite
    earnings), so it is absent evidence rather than maximum cheapness."""
    assert _score(score_db, "ZERO_PE") is not None
    assert _score(score_db, "ZERO_PE") < _old(score_db, "ZERO_PE")


def test_negative_roe_is_valid_and_kept(score_db):
    """Loss-making is a real, measurable ROE and must not be nulled away."""
    assert _score(score_db, "NEG_ROE") is not None


def test_very_high_pe_is_capped_not_negative(score_db):
    """pe=10000 used to subtract points; it must not reward or punish.

    roe=25 contributes 10.0 and the capped cheapness term contributes 0, so the
    row keeps exactly the ROE component rather than being dragged negative.
    """
    assert _score(score_db, "HIGH_PE") == pytest.approx(10.0, abs=0.01)
    assert _score(score_db, "HIGH_PE") >= 0


def test_the_generated_sql_never_coalesces_a_null_to_zero():
    """Checked on the produced SQL, not the source: the docstring quotes the
    old expression verbatim so a source-level grep would match it."""
    generated = quality_score_sql("f")
    assert "COALESCE(" not in generated
    assert "IFNULL(" not in generated


def test_cheap_stock_outscores_expensive_one(score_db):
    cheap = _score(score_db, "COMPLETE")
    dear = _score(score_db, "MID_PE")
    assert cheap > dear


# ── established policy preserved ─────────────────────────────────────────────


def test_a_fully_measured_row_keeps_its_established_magnitude(score_db):
    """Renormalising must not shift the score band of a well-measured stock.

    The UI bands results at 0-40 / 40-70 / 70+, so an unmeasured input may
    redistribute weight but must not rescale a complete row.
    """
    new = _score(score_db, "COMPLETE")
    old = _old(score_db, "COMPLETE")
    assert new == pytest.approx(
        old, abs=0.1
    ), f"complete row drifted {old} -> {new}; that is a policy change, not a fix"


def test_weights_are_unchanged():
    from myra_web.routes.fund_traction import (
        _QUALITY_WEIGHT_PE,
        _QUALITY_WEIGHT_ROE,
    )

    assert _QUALITY_WEIGHT_ROE == 0.4
    assert _QUALITY_WEIGHT_PE == 0.3


def test_net_margin_is_excluded_and_documented():
    """Deferred to a data-repair phase, not guessed at."""
    src = _read("myra_web/routes/fund_traction.py")
    assert "net_margin" not in quality_score_sql(
        "f"
    ), "untrustworthy net_margin must not enter the score"
    assert "unresolved" in src or "deferred" in src.lower()


def test_the_scoring_query_no_longer_coalesces_to_zero():
    """The docstring quotes the old expression, so assert on the generated SQL."""
    assert "COALESCE(" not in quality_score_sql("f")


# ── universe_loader: missing optional fundamentals stay missing ─────────────


def test_universe_loader_preserves_missing_optional_fundamentals():
    src = _read("myra_app/universe_loader.py")
    for field in ("profit_growth", "sales_growth", "debt_to_equity"):
        assert f'f.get("{field}")' in src, f"{field} must preserve None"
        assert (
            f'f.get("{field}", 0) or 0' not in src
        ), f"{field} must not be coerced to 0"


def test_universe_loader_still_exposes_the_other_fields():
    src = _read("myra_app/universe_loader.py")
    for key in ("PE", "ROE", "MCap", "Inst_Hold"):
        assert f'"{key}"' in src


def test_smart_money_score_stays_unavailable():
    src = _read("myra_app/universe_loader.py")
    assert '"smart_money_score": c.get("smart_money_score")' in src
    assert "smart_money_score" in _read("myra_app/fundamental_freshness.py")


def test_worker_pool_does_not_fabricate_a_zero_score():
    """Strip comments first: the explanatory comment quotes the old pattern."""
    src = _read("myra_app/worker_pool.py")
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert 'funda.get("smart_money_score", 0)' not in code
    assert "_sm is not None" in code


def test_worker_pool_star_logic_is_safe_for_none():
    """The comparison must neither crash nor award a star when unavailable."""
    for funda in ({}, {"smart_money_score": None}, {"smart_money_score": 0.0}):
        _sm = funda.get("smart_money_score")
        assert (_sm is not None and _sm > 0.7) is False
    assert ({"smart_money_score": 0.9}.get("smart_money_score") > 0.7) is True


# ── Phase 6 opt-in and untouched paths ──────────────────────────────────────


def test_phase6_ranking_inputs_remain_opt_in():
    src = _read("myra_app/ranking_inputs.py")
    assert 'ENV_FLAG = "MYRA_V25_RANKING_INPUTS"' in src
    assert 'os.environ.get(ENV_FLAG, "")' in src
    # Default must stay False without the env var.
    os.environ.pop("MYRA_V25_RANKING_INPUTS", None)
    from myra_app.ranking_inputs import ranking_inputs_enabled

    assert ranking_inputs_enabled() is False


#: Source markers that only appear on lines carrying ranking behaviour.
RANKING_MARKERS = ("stars_count", "s_score", "multibagger_early", "smc_phase")


def test_scoring_components_are_not_modified_by_this_phase():
    """The ranking maths stays byte-identical across phases.

    Phase 6/15 pinned this with a whole-file ``git diff`` guard over four files.
    Phase 16 legitimately edits ``myra_app/screener.py`` — it replaces the
    "starting background refresh" message with the lake's real state (the
    refresh it described could never have worked), and makes an empty result say
    *why* it was empty. Neither is a ranking change, so the guard is split: the
    three files carrying the scoring maths stay untouched, and ``screener.py``
    is checked for ranking-relevant edits specifically.
    """
    import subprocess

    scoring = subprocess.run(
        [
            "git",
            "diff",
            "--stat",
            "HEAD",
            "--",
            "myra_app/score_components_v2.py",
            "myra_app/positional_engine.py",
            "myra_app/ranking_inputs.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert (
        scoring.stdout.strip() == ""
    ), f"scoring files were modified:\n{scoring.stdout}"

    # ``-U0`` keeps the output to changed lines only, so no surrounding context
    # can trip the marker check by accident.
    screener = subprocess.run(
        ["git", "diff", "-U0", "HEAD", "--", "myra_app/screener.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    for marker in RANKING_MARKERS:
        assert marker not in screener.stdout, (
            f"a ranking-relevant line in screener.py changed ({marker}):\n"
            f"{screener.stdout}"
        )


def test_the_two_coalesce_filters_are_left_as_they_are():
    """`COALESCE(market_cap,0)` in a positive-threshold filter is NOT a defect.

    NULL and 0 both fail `> 0` and both fail `BETWEEN min AND max`, so the rows
    are excluded either way. The COALESCE is redundant, not wrong, and removing
    it would be a cosmetic change to a screening path.
    """
    for rel in ("myra_web/routes/scanners.py", "myra_web/confluence_batch.py"):
        src = _read(rel)
        assert "COALESCE(market_cap, 0)" in src, f"{rel} should be unchanged"


def test_screener_display_fallback_is_left_alone():
    """Explicitly documented as a display-compatibility default, not a ranking
    input; changing it would alter rendering without fixing evidence."""
    src = _read("myra_app/screener.py")
    assert 'r["ProfitGrowth"] = 0' in src
    assert "display compatibility" in src
