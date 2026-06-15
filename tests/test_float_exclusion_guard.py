"""
Tests for the float/promoter exclusion guard from trigger_scanner.py:167-171.
Logic: skip symbol if BOTH raw_ff_pct AND raw_promoter_pct are None or <= 0.
"""


def _should_skip(raw_ff: float | None, raw_prom: float | None) -> bool:
    """Replicates the float exclusion condition from trigger_scanner.py:170."""
    return (raw_ff is None or raw_ff <= 0) and (raw_prom is None or raw_prom <= 0)


def test_both_none():
    assert _should_skip(None, None)


def test_both_zero():
    assert _should_skip(0.0, 0.0)


def test_both_negative():
    assert _should_skip(-1.0, -5.0)


def test_ff_valid_prom_none():
    assert not _should_skip(25.0, None)


def test_ff_none_prom_valid():
    assert not _should_skip(None, 50.0)


def test_ff_zero_prom_valid():
    assert not _should_skip(0.0, 75.0)


def test_ff_valid_prom_zero():
    assert not _should_skip(30.0, 0.0)


def test_both_valid():
    assert not _should_skip(25.0, 50.0)


def test_both_negative_skipped():
    assert _should_skip(-1.0, -0.5)


def test_ff_nan_prom_valid():
    """NaN is truthy in Python → not None, and NaN <= 0 is False → not skipped."""
    assert not _should_skip(float("nan"), 50.0)


def test_ff_valid_prom_nan():
    assert not _should_skip(25.0, float("nan"))


def test_ff_infinity():
    """Infinity is not None, and inf > 0 → not skipped."""
    assert not _should_skip(float("inf"), None)
