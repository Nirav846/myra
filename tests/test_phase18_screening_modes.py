"""Phase 18: screening availability is reported per mode.

Phase 16 modelled screening capability as one verdict - the indicator lake is
usable or it is not. That was one axis too few, and it produced two opposite
lies:

* a working ``TriggerScanner`` was hidden behind a global "Screening blocked"
  whenever the lake was empty, so a usable mode looked unusable; and
* a healthy ``TriggerScanner`` would make the incomplete v2.5 ranking look
  operational.

These tests pin the honest behaviour: one state per mode, an aggregate that is
never stronger than the weakest mode, and no missing value ever turned into a
zero, a score, or a clean bill of health.
"""

import pytest

import myra_app.data_readiness as dr
import myra_app.screening_modes as sm

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_live_probes(monkeypatch):
    """Never read this machine's lake or this repo's live module source.

    ``lake_field_names`` reads the real lake directory and the two runtime
    detectors read the real ``worker_pool``/``engine`` source, so both are
    disabled by default and each test states its own premise. The detectors
    themselves are exercised with synthetic sources via :func:`_fake_source`.
    """
    monkeypatch.setattr(sm, "lake_field_names", lambda: None)

    def _no_source(obj):
        raise OSError("source inspection is disabled in this module")

    monkeypatch.setattr(sm.inspect, "getsource", _no_source)


def _tech(latest="2026-01-02", expected="2026-01-02"):
    return {"latest_date": latest, "expected_date": expected}


def _cov(universe=3000):
    return {"universe": universe}


def _populations(**overrides):
    """Every trigger column fully populated, unless a test says otherwise."""
    pop = {name: 1.0 for name in sm.TRIGGER_REQUIRED_COLUMNS}
    pop.update({name: 1.0 for name in sm.TRIGGER_CONTRIBUTING_COLUMNS})
    pop.update(overrides)
    return pop


def _screening(state="ready", reason="ok", available=True, detail="Lake built."):
    return {
        "state": state,
        "reason": reason,
        "available": available,
        "detail": detail,
    }


def _built_lake():
    """A screening verdict for a lake that exists and is current."""
    return _screening()


def _run(**kwargs):
    kwargs.setdefault("technical", _tech())
    kwargs.setdefault("coverage", _cov())
    kwargs.setdefault("screening", _built_lake())
    kwargs.setdefault("populations", _populations())
    kwargs.setdefault("available_fields", set(sm.V25_REQUIRED_INPUTS))
    kwargs.setdefault("probe_fields", False)
    return sm.evaluate(**kwargs)


# ── 1. A working scanner is available even when the lake is empty ────────────


def test_a_working_scanner_is_available_when_the_lake_is_empty():
    """The headline fix: an empty lake must not hide the web/API scanner."""
    result = _run(
        screening=_screening(
            state=dr.STATE_BLOCKED,
            reason="lake_empty",
            available=False,
            detail="The indicator lake (data/indicators/precomputed) is empty.",
        ),
    )

    assert result["modes"][sm.MODE_TRIGGER]["state"] == sm.MODE_OPERATIONAL
    assert result["modes"][sm.MODE_V25_TURBO]["state"] == sm.MODE_BLOCKED
    # Some mode still works, so availability is real but qualified.
    assert result["overall"]["state"] == sm.MODE_DEGRADED
    assert result["overall"]["operational_modes"] == [sm.MODE_TRIGGER]
    assert sm.MODE_V25_TURBO in result["overall"]["unavailable_modes"]


def test_an_empty_lake_blocks_the_v25_mode_and_gives_the_remedy():
    result = _run(
        screening=_screening(
            state=dr.STATE_BLOCKED,
            reason="lake_empty",
            available=False,
            detail="The indicator lake is empty.",
        ),
    )

    v25 = result["modes"][sm.MODE_V25_TURBO]
    assert v25["state"] == sm.MODE_BLOCKED
    assert v25["reason"] == "lake_empty"
    # Every required input is reported missing - not silently defaulted.
    assert v25["missing"] == list(sm.V25_REQUIRED_INPUTS)
    assert "until it is built" in v25["detail"].lower()
    assert v25["reads_lake"] is True


def test_the_trigger_mode_does_not_read_the_lake():
    trigger = _run()["modes"][sm.MODE_TRIGGER]
    assert trigger["reads_lake"] is False


# ── 2. Results are never mislabelled as MYRA_Score_v25 ──────────────────────


def test_the_two_modes_never_share_a_score_name():
    assert sm.MODE_SCORES[sm.MODE_TRIGGER] == "trigger_score"
    assert sm.MODE_SCORES[sm.MODE_V25_TURBO] == "MYRA_Score_v25"
    assert sm.MODE_SCORES[sm.MODE_TRIGGER] != sm.MODE_SCORES[sm.MODE_V25_TURBO]


def test_each_mode_declares_that_it_is_not_comparable_with_the_other():
    result = _run()
    for mode in (sm.MODE_TRIGGER, sm.MODE_V25_TURBO):
        comparability = result["modes"][mode]["comparability"]
        assert "not comparable" in comparability.lower()
    assert (
        result["modes"][sm.MODE_TRIGGER]["comparability"]
        != result["modes"][sm.MODE_V25_TURBO]["comparability"]
    )


def test_a_trigger_result_carries_the_trigger_score_name():
    trigger = _run()["modes"][sm.MODE_TRIGGER]
    assert trigger["score"] == "trigger_score"
    assert "MYRA_Score_v25" not in trigger["score"]


# ── 3. Missing optional data qualifies the mode, visibly ────────────────────


def test_missing_optional_trigger_inputs_qualify_the_mode():
    result = _run(
        populations=_populations(delivery=None, sma_50=None, high_52w=None),
    )

    trigger = result["modes"][sm.MODE_TRIGGER]
    assert trigger["state"] == sm.MODE_DEGRADED
    assert trigger["reason"] == "columns_partly_populated"
    assert set(trigger["missing"]) == {"delivery", "sma_50", "high_52w"}
    # The operator must be told the gates are weaker than they look.
    assert "weaker than they look" in trigger["detail"].lower()


def test_a_barely_populated_optional_column_also_qualifies_the_mode():
    """A column present in only a few rows is a limitation, not a zero."""
    result = _run(populations=_populations(delivery_pct=0.4))
    trigger = result["modes"][sm.MODE_TRIGGER]
    assert trigger["state"] == sm.MODE_DEGRADED
    assert trigger["missing"] == ["delivery_pct"]


# ── 4. Failed mandatory inputs stay explicit failures ───────────────────────


def test_a_mandatory_trigger_column_gone_blocks_the_mode():
    result = _run(
        populations=_populations(close=0.1),
        screening=_screening(state=dr.STATE_BLOCKED, available=False),
    )

    trigger = result["modes"][sm.MODE_TRIGGER]
    assert trigger["state"] == sm.MODE_BLOCKED
    assert trigger["reason"] == "required_columns_missing"
    assert trigger["missing"] == ["close"]
    # With no mode left, the aggregate is blocked outright.
    assert result["overall"]["state"] == sm.MODE_BLOCKED


def test_a_half_empty_volume_column_is_a_hard_failure_not_a_rounding():
    result = _run(populations=_populations(volume=sm.COLUMN_BLOCK_FRACTION))
    assert result["modes"][sm.MODE_TRIGGER]["state"] == sm.MODE_BLOCKED


@pytest.mark.parametrize("universe", [None, 0])
def test_an_absent_universe_blocks_the_trigger_mode(universe):
    result = _run(coverage={"universe": universe})
    trigger = result["modes"][sm.MODE_TRIGGER]
    assert trigger["state"] == sm.MODE_BLOCKED
    assert trigger["reason"] == "universe_unavailable"


def test_a_stale_session_qualifies_the_trigger_mode():
    result = _run(technical=_tech(latest="2026-01-01", expected="2026-01-02"))
    trigger = result["modes"][sm.MODE_TRIGGER]
    assert trigger["state"] == sm.MODE_DEGRADED
    assert trigger["reason"] == "source_stale"
    assert "2026-01-01" in trigger["detail"]


def test_an_unmeasurable_population_does_not_manufacture_a_verdict():
    """No population measurement means the column checks are skipped.

    They are *not* passed, and the reason string stays ``ok`` because no column
    was observed to be empty - absence of evidence is not reported as either
    health or failure. The column-level checks are covered above with real
    measurements.
    """
    result = _run(populations=None)
    trigger = result["modes"][sm.MODE_TRIGGER]
    assert trigger["missing"] == []
    assert trigger["reason"] == "ok"
    assert trigger["state"] == sm.MODE_OPERATIONAL


# ── 5. A missing score cannot be silently fabricated ────────────────────────


def test_unreadable_lake_fields_are_reported_not_assumed_fine(monkeypatch):
    """``None`` fields means "could not read", never "nothing missing"."""
    # No available_fields supplied, and the lake probe returns None.
    result = _run(available_fields=None, probe_fields=True)
    v25 = result["modes"][sm.MODE_V25_TURBO]
    assert v25["state"] != sm.MODE_OPERATIONAL
    assert v25["missing"] == list(sm.V25_REQUIRED_INPUTS)
    assert v25["reason"] == "inputs_unsourced"


def test_a_lake_missing_inputs_cannot_be_promoted_to_operational():
    result = _run(available_fields=set(sm.V25_LAKE_PRODUCED_INPUTS))
    v25 = result["modes"][sm.MODE_V25_TURBO]
    assert v25["state"] == sm.MODE_DEGRADED
    assert v25["reason"] == "inputs_unsourced"
    # The reason the score cannot be trusted is spelled out.
    assert "zero-filled" in v25["detail"].lower()
    assert set(v25["missing"]) == set(sm.V25_REQUIRED_INPUTS) - set(
        sm.V25_LAKE_PRODUCED_INPUTS
    )


def test_a_complete_lake_with_a_runtime_defect_is_blocked_not_degraded(monkeypatch):
    """The data being present does not mean a score can be produced."""
    monkeypatch.setattr(sm, "worker_path_blocker", lambda: "fake_unassigned_field")
    result = _run()

    v25 = result["modes"][sm.MODE_V25_TURBO]
    assert v25["state"] == sm.MODE_BLOCKED
    assert v25["reason"] == "worker_path_defect"
    assert v25["missing"] == []
    assert "fake_unassigned_field" in v25["detail"]


def test_a_stale_lake_qualifies_the_v25_mode():
    result = _run(
        screening=_screening(
            state=dr.STATE_STALE, reason="lake_stale", detail="Lake predates it."
        )
    )
    v25 = result["modes"][sm.MODE_V25_TURBO]
    assert v25["state"] == sm.MODE_DEGRADED
    assert v25["reason"] == "lake_stale"


def test_an_unreadable_lake_blocks_the_v25_mode():
    result = _run(
        screening=_screening(
            state=dr.STATE_PARTIAL,
            reason="probe_failed",
            available=False,
            detail="The lake could not be examined.",
        )
    )
    v25 = result["modes"][sm.MODE_V25_TURBO]
    assert v25["state"] == sm.MODE_BLOCKED
    assert v25["reason"] == "lake_probe_failed"


def test_a_complete_lake_and_a_clean_path_is_operational():
    result = _run()
    assert result["modes"][sm.MODE_V25_TURBO]["state"] == sm.MODE_OPERATIONAL
    assert result["overall"]["state"] == sm.MODE_OPERATIONAL
    assert result["overall"]["blocked"] == []


# ── 6. The runtime detectors are derived, and clear themselves ──────────────


def _fake_source(monkeypatch, text):
    monkeypatch.setattr(sm.inspect, "getsource", lambda obj: text)


def test_the_fvg_mid_detector_fires_on_an_unassigned_reference(monkeypatch):
    """``worker_pool`` matching on a name nothing assigns yields no candidate."""
    _fake_source(monkeypatch, "def build_row(d):\n    return {'fvg_mid': fvg_mid}\n")
    assert sm.worker_path_blocker() == "worker_pool_fvg_mid_unassigned"


def test_the_fvg_mid_detector_clears_itself_once_assigned(monkeypatch):
    _fake_source(
        monkeypatch,
        "def build_row(d):\n    fvg_mid = d['fvg_mid']\n    return {'fvg_mid': fvg_mid}\n",
    )
    assert sm.worker_path_blocker() is None


def test_the_fvg_mid_detector_stays_quiet_when_the_name_is_absent(monkeypatch):
    _fake_source(monkeypatch, "def build_row(d):\n    return {}\n")
    assert sm.worker_path_blocker() is None


def test_the_polars_detector_fires_on_implicit_schema_inference(monkeypatch):
    _fake_source(monkeypatch, "df = pl.read_database(sql, conn)\n")
    assert sm.polars_schema_blocker() == "engine_read_database_implicit_schema"


@pytest.mark.parametrize(
    "source",
    [
        "df = pl.read_database(sql, conn, infer_schema_length=None)\n",
        "df = pl.read_database(sql, conn, schema_overrides=SCHEMA)\n",
    ],
)
def test_the_polars_detector_clears_itself_with_an_explicit_schema(monkeypatch, source):
    _fake_source(monkeypatch, source)
    assert sm.polars_schema_blocker() is None


def test_a_broken_probe_does_not_fabricate_a_blocker(monkeypatch):
    """If the module cannot be inspected, say nothing rather than guess."""

    def _boom(obj):
        raise OSError("no source")

    monkeypatch.setattr(sm.inspect, "getsource", _boom)
    assert sm.worker_path_blocker() is None
    assert sm.polars_schema_blocker() is None


# ── 7. The aggregate is never stronger than the weakest mode ────────────────


def test_the_aggregate_is_blocked_only_when_no_mode_can_run():
    result = _run(
        populations=_populations(close=0.0),
        screening=_screening(state=dr.STATE_BLOCKED, available=False),
    )
    assert result["overall"]["state"] == sm.MODE_BLOCKED
    assert result["overall"]["operational_modes"] == []
    assert set(result["overall"]["blocked"]) == {sm.MODE_TRIGGER, sm.MODE_V25_TURBO}


def test_a_working_scanner_never_makes_v25_look_operational():
    result = _run(screening=_screening(state=dr.STATE_BLOCKED, available=False))
    assert sm.MODE_TRIGGER in result["overall"]["operational_modes"]
    assert sm.MODE_V25_TURBO not in result["overall"]["operational_modes"]
    assert result["overall"]["state"] != sm.MODE_OPERATIONAL


def test_a_broken_scanner_never_hides_behind_a_healthy_lake():
    """A healthy v2.5 lake must not make a broken scanner invisible."""
    result = _run(populations=_populations(close=0.0))

    assert sm.MODE_V25_TURBO in result["overall"]["operational_modes"]
    assert sm.MODE_TRIGGER in result["overall"]["blocked"]
    assert sm.MODE_TRIGGER not in result["overall"]["operational_modes"]
    # One mode is down, so the aggregate can never read fully operational.
    assert result["overall"]["state"] != sm.MODE_OPERATIONAL


def test_the_aggregate_names_which_mode_survives():
    result = _run(screening=_screening(state=dr.STATE_BLOCKED, available=False))
    detail = result["overall"]["detail"]
    assert "TriggerScanner (web/API) is operational" in detail
    assert "not interchangeable" in detail


def test_the_requirement_and_the_capability_are_reported_separately():
    result = _run()
    assert result["required_v25_inputs"] == list(sm.V25_REQUIRED_INPUTS)
    assert result["lake_produced_v25_inputs"] == list(sm.V25_LAKE_PRODUCED_INPUTS)
    # The capability is a strict subset: a built lake is not a trustworthy score.
    assert set(sm.V25_LAKE_PRODUCED_INPUTS) < set(sm.V25_REQUIRED_INPUTS)


def test_missing_inputs_are_named_never_numerically_defaulted():
    missing = sm.missing_v25_inputs({"sma50"})
    assert missing
    assert all(isinstance(name, str) for name in missing)
    assert "sma50" not in missing
    assert missing == [n for n in sm.V25_REQUIRED_INPUTS if n != "sma50"]


def test_no_mode_verdict_contains_a_fabricated_zero(monkeypatch):
    """Nothing in a verdict is a numeric substitute for an absent value."""
    monkeypatch.setattr(sm, "worker_path_blocker", lambda: None)
    result = _run(
        populations=_populations(delivery=None),
        screening=_screening(state=dr.STATE_BLOCKED, available=False),
    )
    for mode in result["modes"].values():
        assert all(isinstance(m, str) for m in mode["missing"])
        for key, value in mode.items():
            assert not isinstance(value, (int, float)) or isinstance(value, bool)


# ── 8. Readiness must never fail because a probe failed ─────────────────────


def test_a_probe_failure_is_reported_as_unknown_not_raised(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(sm, "evaluate", _boom)
    result = dr.screening_mode_facts()

    assert result["overall"]["state"] == "unknown"
    assert "probe exploded" in result["overall"]["detail"]


def test_an_unknown_mode_verdict_never_escalates_to_ready():
    modes = {"overall": {"state": "unknown", "detail": "Could not evaluate."}}
    state, explanation = dr._apply_mode_capability(dr.STATE_READY, "Fresh.", modes)
    assert state == dr.STATE_READY
    assert explanation == "Fresh."


def test_mode_escalation_never_downgrades_a_failure():
    modes = {"overall": {"state": sm.MODE_BLOCKED, "detail": "No mode can run."}}
    state, _ = dr._apply_mode_capability(dr.STATE_FAILED, "Ingestion failed.", modes)
    assert state == dr.STATE_FAILED


def test_a_degraded_aggregate_escalates_only_to_partial():
    modes = {"overall": {"state": sm.MODE_DEGRADED, "detail": "One mode is down."}}
    state, explanation = dr._apply_mode_capability(dr.STATE_READY, "Fresh.", modes)
    assert state == dr.STATE_PARTIAL
    assert "One mode is down." in explanation


def test_a_blocked_aggregate_escalates_to_blocked():
    modes = {"overall": {"state": sm.MODE_BLOCKED, "detail": "No mode can run."}}
    state, _ = dr._apply_mode_capability(dr.STATE_READY, "Fresh.", modes)
    assert state == dr.STATE_BLOCKED


def test_mode_escalation_never_downgrades_a_partial_state():
    modes = {"overall": {"state": sm.MODE_DEGRADED, "detail": "One mode is down."}}
    state, _ = dr._apply_mode_capability(dr.STATE_PARTIAL, "Already partial.", modes)
    assert state == dr.STATE_PARTIAL
