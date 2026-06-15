"""
Tests for Gate 1 Float Absorption score formula.
Formula (from trigger_scanner.py:186):
    gate1_score = min(100.0, float_util_pct / 40.0 * 100)
"""


def _gate1_score(float_util_pct: float) -> float:
    """Replicates the gate1_score calculation from trigger_scanner.py."""
    return min(100.0, float_util_pct / 40.0 * 100)


def test_float_util_zero():
    assert _gate1_score(0.0) == 0.0


def test_float_util_ten():
    assert _gate1_score(10.0) == 25.0


def test_float_util_twenty():
    assert _gate1_score(20.0) == 50.0


def test_float_util_forty():
    assert _gate1_score(40.0) == 100.0


def test_float_util_sixty_capped():
    assert _gate1_score(60.0) == 100.0


def test_float_util_negative():
    assert _gate1_score(-5.0) == -12.5


def test_float_util_five():
    assert _gate1_score(5.0) == 12.5


def test_float_util_thirty():
    assert _gate1_score(30.0) == 75.0
