"""Tests for the shared ratio plausibility guard.

The guard exists because Morningstar's quickRatio/currentRatio are real
arithmetic over a near-zero denominator, producing values like 13935.9 that
are true to the source and useless to a reader. These tests pin that it
suppresses rather than clamps, that it does not eat legitimate values, and
that 0 survives - the case most likely to be broken by a truthiness check.
"""

from __future__ import annotations

import math

import pytest

from myra_app.ratio_sanitize import DEFAULT_RATIO_MAX_BOUND, sanitize_ratio

FIELD = "quick_ratio"


def test_plausible_value_passes_through():
    assert sanitize_ratio(1.8, DEFAULT_RATIO_MAX_BOUND, FIELD) == 1.8


def test_zero_is_preserved():
    """A company with no quick assets reports 0. That is a value, not absence."""
    assert sanitize_ratio(0, DEFAULT_RATIO_MAX_BOUND, FIELD) == 0.0
    assert sanitize_ratio(0.0, DEFAULT_RATIO_MAX_BOUND, FIELD) == 0.0


def test_value_above_bound_is_suppressed():
    assert sanitize_ratio(13935.914815, DEFAULT_RATIO_MAX_BOUND, FIELD) is None


def test_value_exactly_at_bound_is_preserved():
    assert (
        sanitize_ratio(DEFAULT_RATIO_MAX_BOUND, DEFAULT_RATIO_MAX_BOUND, FIELD) == 10.0
    )


def test_value_just_above_bound_is_suppressed():
    assert sanitize_ratio(10.000001, DEFAULT_RATIO_MAX_BOUND, FIELD) is None


def test_negative_is_suppressed():
    assert sanitize_ratio(-0.5, DEFAULT_RATIO_MAX_BOUND, FIELD) is None


def test_none_passes_through_as_none():
    assert sanitize_ratio(None, DEFAULT_RATIO_MAX_BOUND, FIELD) is None


def test_absent_value_does_not_log(caplog):
    """None means "no data", not "implausible data" - it must stay quiet.

    Without the early return, None falls through to float(None), is caught as
    non-numeric and returns None all the same, so the return value alone cannot
    distinguish the two. The difference is the log line: an absent field is the
    normal case, and warning on every one of them would flood the log and train
    operators to ignore it.
    """
    with caplog.at_level("WARNING"):
        assert sanitize_ratio(None, DEFAULT_RATIO_MAX_BOUND, FIELD) is None
    assert caplog.text == ""


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_is_suppressed(bad):
    assert sanitize_ratio(bad, DEFAULT_RATIO_MAX_BOUND, FIELD) is None


@pytest.mark.parametrize("bad", ["not-a-number", "", object()])
def test_non_numeric_is_suppressed(bad):
    assert sanitize_ratio(bad, DEFAULT_RATIO_MAX_BOUND, FIELD) is None


def test_suppression_is_not_clamping():
    """The whole point: a capped 10.0 would still assert false precision."""
    huge = sanitize_ratio(13935.9, DEFAULT_RATIO_MAX_BOUND, FIELD)
    assert huge is None
    assert huge != DEFAULT_RATIO_MAX_BOUND


def test_integer_and_string_inputs_are_coerced():
    assert sanitize_ratio(2, DEFAULT_RATIO_MAX_BOUND, FIELD) == 2.0
    assert sanitize_ratio("1.25", DEFAULT_RATIO_MAX_BOUND, FIELD) == 1.25


def test_bound_is_configurable():
    assert sanitize_ratio(50, 100.0, FIELD) == 50.0
    assert sanitize_ratio(50, 10.0, FIELD) is None


def test_suppression_logs_field_name_and_value(caplog):
    """An operator must be able to trace which field was suppressed."""
    with caplog.at_level("WARNING"):
        sanitize_ratio(13935.9, DEFAULT_RATIO_MAX_BOUND, FIELD)
    assert FIELD in caplog.text
    assert "13935.9" in caplog.text


def test_plausible_value_does_not_log(caplog):
    with caplog.at_level("WARNING"):
        sanitize_ratio(1.8, DEFAULT_RATIO_MAX_BOUND, FIELD)
    assert caplog.text == ""


def test_default_bound_is_ten():
    assert DEFAULT_RATIO_MAX_BOUND == 10.0
    assert sanitize_ratio(11.0, field_name=FIELD) is None
    assert sanitize_ratio(9.0, field_name=FIELD) == 9.0


def test_nan_never_equals_itself_guard():
    """Sanity on the test data itself, not the helper."""
    assert math.isnan(float("nan"))
    assert not math.isnan(1.0)
