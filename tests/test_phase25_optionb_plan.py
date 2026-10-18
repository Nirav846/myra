"""Phase 25 - the Option B three-column write plan: scope, hash and guards.

Synthetic data and temporary databases only. Nothing here touches production,
and no test performs a production write.

Covers the properties the owner needs before authorising a write:
  1. exactly the three requested columns are in scope;
  2. ``sma_200`` / ``delivery_ma_60`` can never enter the write set;
  3. changing a symbol, date, value, column, calculation fingerprint or scope
     metadata changes the content hash;
  4. an unexpected column list is rejected before any write;
  5. a stale baseline digest invalidates the plan;
  6. the old four-column manifest cannot authorise this operation;
  7. the exact target rows and cells are reproducible from plan + source data.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from myra_app import recovery_f2 as f2

SESSIONS = list(f2.TARGET_SESSIONS)
N = 6


def _history(symbol: str, closes):
    """Raw history whose trailing rows land exactly on the target sessions."""
    n = len(closes)
    leading = pd.bdate_range(
        end=pd.Timestamp(SESSIONS[0]) - pd.Timedelta(days=1),
        periods=n - len(SESSIONS),
    )
    dates = pd.DatetimeIndex(list(leading) + [pd.Timestamp(d) for d in SESSIONS])
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "high": [c + 3.0 for c in closes],
            "low": [c - 3.0 for c in closes],
            "close": closes,
        }
    )


def _closes(n=N * 12, amp=20.0, base=100.0, period=17):
    """Saw-tooth, so a 252-row extreme is not simply today's value."""
    return [base + amp * abs(((i % (period * 2)) - period)) / period for i in range(n)]


def _plan(rows=None, **overrides):
    closes = _closes()
    history = {}
    stored = {}
    for s in range(3):
        sym = f"SYM{s:02d}"
        h = _history(sym, closes)
        history[sym] = h
        for d in SESSIONS:
            row = h[h["date"] == pd.Timestamp(d)]
            if row.empty:
                continue
            c = float(row["close"].iloc[0])
            stored[(sym, d)] = {
                "sma_50": c,
                "high_52w": c + 3.0,
                "low_52w": c - 3.0,
            }
    kwargs = dict(
        db_path="/tmp/does-not-matter.db",
        db_identity={},
        sessions=SESSIONS,
        history_by_symbol=history,
        stored_by_key=stored,
        baseline_scope_digest="baseline-digest",
        symbol_count=len(history),
        code_sha256="code-fingerprint",
    )
    kwargs.update(overrides)
    plan = f2.build_write_plan(**kwargs)
    if rows is not None:
        plan = f2.WritePlan(payload=plan.payload, rows=tuple(rows))
    return plan


# --- 1/2. scope containment -------------------------------------------------


def test_the_plan_writes_exactly_the_three_requested_columns():
    plan = _plan()
    assert plan.columns == ("sma_50", "high_52w", "low_52w")
    assert plan.columns == f2.OPTION_B_COLUMNS


def test_deferred_columns_can_never_enter_the_write_set():
    assert "sma_200" in f2.DEFERRED_COLUMNS
    assert "delivery_ma_60" in f2.DEFERRED_COLUMNS
    assert not set(f2.OPTION_B_COLUMNS) & set(f2.DEFERRED_COLUMNS)
    plan = _plan()
    assert not set(plan.columns) & set(f2.DEFERRED_COLUMNS)


def test_a_column_list_containing_sma_200_is_rejected():
    plan = _plan()
    tampered = f2.WritePlan(
        payload={
            **plan.payload,
            "write_columns": ["sma_50", "sma_200", "high_52w", "low_52w"],
        },
        rows=plan.rows,
    )
    with pytest.raises(f2.ScopeError):
        tampered.assert_scope()


def test_a_column_list_containing_delivery_ma_60_is_rejected():
    plan = _plan()
    tampered = f2.WritePlan(
        payload={
            **plan.payload,
            "write_columns": ["sma_50", "high_52w", "low_52w", "delivery_ma_60"],
        },
        rows=plan.rows,
    )
    with pytest.raises(f2.ScopeError):
        tampered.assert_scope()


def test_a_short_column_list_is_rejected():
    plan = _plan()
    tampered = f2.WritePlan(
        payload={**plan.payload, "write_columns": ["sma_50", "high_52w"]},
        rows=plan.rows,
    )
    with pytest.raises(f2.ScopeError):
        tampered.assert_scope()


# --- 3. hash sensitivity ----------------------------------------------------


def test_the_hash_is_deterministic():
    assert _plan().content_hash == _plan().content_hash


def test_changing_a_target_value_changes_the_hash():
    plan = _plan()
    mutated = list(plan.rows)
    mutated[0] = mutated[0][:2] + (mutated[0][2] + 1.0,) + mutated[0][3:]
    other = f2.WritePlan(payload=plan.payload, rows=tuple(mutated))
    assert other.content_hash != plan.content_hash


def test_changing_a_symbol_changes_the_hash():
    plan = _plan()
    mutated = list(plan.rows)
    mutated[0] = ("ZZZ_OTHER",) + mutated[0][1:]
    other = f2.WritePlan(payload=plan.payload, rows=tuple(mutated))
    assert other.content_hash != plan.content_hash


def test_changing_a_date_changes_the_hash():
    plan = _plan()
    mutated = list(plan.rows)
    mutated[0] = mutated[0][:1] + ("2026-10-14",) + mutated[0][2:]
    other = f2.WritePlan(payload=plan.payload, rows=tuple(mutated))
    assert other.content_hash != plan.content_hash


def test_changing_scope_metadata_changes_the_hash():
    plan = _plan()
    other = f2.WritePlan(
        payload={**plan.payload, "baseline_scope_digest": "different"}, rows=plan.rows
    )
    assert other.content_hash != plan.content_hash


def test_changing_the_calculation_fingerprint_changes_the_hash():
    plan = _plan()
    tampered_rule = {
        **f2.CALCULATION_RULE,
        "sma_50": {**f2.CALCULATION_RULE["sma_50"], "window_rows": 51},
    }
    other = f2.WritePlan(
        payload={**plan.payload, "calculation_rule": tampered_rule}, rows=plan.rows
    )
    assert other.content_hash != plan.content_hash


def test_changing_the_code_fingerprint_changes_the_hash():
    plan = _plan()
    other = f2.WritePlan(
        payload={**plan.payload, "code_sha256": "other"}, rows=plan.rows
    )
    assert other.content_hash != plan.content_hash


def test_changing_the_tool_version_changes_the_hash():
    a = _plan()
    b = _plan(version="phase25-optionB-v0")
    assert a.content_hash != b.content_hash


# --- 4/5/6. fail-closed guards ---------------------------------------------


def test_an_unexpected_column_expectation_is_refused():
    """The old four-column operation must not be able to run this plan."""
    plan = _plan()
    with pytest.raises(f2.ScopeError):
        f2.validate_plan(
            plan,
            live_scope_digest="baseline-digest",
            expected_columns=("sma_50", "sma_200", "high_52w", "low_52w"),
        )


def test_a_stale_baseline_digest_is_refused():
    plan = _plan()
    with pytest.raises(f2.ScopeError, match="moved since"):
        f2.validate_plan(plan, live_scope_digest="production-changed")


def test_a_matching_digest_passes_validation():
    plan = _plan()
    f2.validate_plan(plan, live_scope_digest="baseline-digest")


def test_apply_refuses_a_stale_plan_before_touching_the_database(tmp_path):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE technical_data (symbol TEXT, date TEXT, sma_50 REAL,"
        " high_52w REAL, low_52w REAL, PRIMARY KEY(symbol,date))"
    )
    conn.execute("INSERT INTO technical_data VALUES ('AAA','2026-08-17',1,2,3)")
    conn.commit()
    plan = _plan()
    with pytest.raises(f2.ScopeError):
        f2.apply_write_plan(conn, plan, live_scope_digest="production-changed")
    still = conn.execute("SELECT sma_50 FROM technical_data").fetchone()[0]
    assert still == 1, "a refused plan must not write"
    conn.close()


def test_a_changed_calculation_rule_is_refused():
    plan = _plan()
    other = f2.WritePlan(
        payload={
            **plan.payload,
            "calculation_rule": {**f2.CALCULATION_RULE, "high_52w": "nonsense"},
        },
        rows=plan.rows,
    )
    with pytest.raises(f2.ScopeError, match="calculation rule changed"):
        f2.validate_plan(other, live_scope_digest="baseline-digest")


def test_a_plan_without_a_code_fingerprint_is_refused():
    plan = _plan()
    other = f2.WritePlan(payload={**plan.payload, "code_sha256": ""}, rows=plan.rows)
    with pytest.raises(f2.ScopeError, match="fingerprint"):
        f2.validate_plan(other, live_scope_digest="baseline-digest")


def test_row_arity_is_validated():
    plan = _plan()
    bad = f2.WritePlan(payload=plan.payload, rows=(("AAA", "2026-08-17", 1.0),))
    with pytest.raises(f2.ScopeError, match="arity"):
        bad.assert_scope()


# --- 7. reproducibility -----------------------------------------------------


def test_the_write_rows_are_reproducible_from_plan_and_source_data():
    plan = _plan()
    again = _plan()
    assert plan.rows == again.rows, "the same inputs must produce the same rows"
    assert plan.content_hash == again.content_hash


def test_every_planned_row_is_a_target_session():
    plan = _plan()
    assert plan.rows, "the synthetic fixture must produce work"
    for r in plan.rows:
        assert r[1] in set(SESSIONS)
        assert len(r) == 5


def test_rows_already_holding_the_planned_value_are_excluded():
    plan = _plan()
    for r in plan.rows:
        assert r[2] is not None and r[3] is not None and r[4] is not None
    # Re-planning against the repaired state must find nothing to do.
    stored = {
        (r[0], r[1]): {"sma_50": r[2], "high_52w": r[3], "low_52w": r[4]}
        for r in plan.rows
    }
    empty = _plan(stored_by_key=stored)
    assert empty.rows == ()


def test_the_plan_records_the_deferred_columns_explicitly():
    payload = _plan().payload
    assert payload["deferred_columns"] == ["sma_200", "delivery_ma_60"]
    assert payload["option"] == "B"
    assert payload["write_columns"] == ["sma_50", "high_52w", "low_52w"]


def test_the_execution_contract_states_the_price_basis():
    contract = f2.EXECUTION_CONTRACT
    assert "adjusted" in contract["price_basis"]
    assert "ohlcv cache" in contract["price_basis"].lower()
    assert contract["window_unit"].startswith("trading sessions")
    assert (
        contract["min_periods"]
        if "min_periods" in contract
        else "min_periods=1" in contract["warm_up"]
    )
