"""
Tests for Invisible Hand composite score logic.
Formulas from invisible_hand_scanner.py:
  - ih_score = der_score * 0.35 + ddas_score * 0.30 + dcs_score * 0.20 + qcd_score * 0.15
  - der_score = min(100.0, max(0.0, (der_ratio - 1.0) / 2.0 * 100))
  - der_ratio = curr_der / hist_der if hist_der > 0.1 else 1.0
"""

import pytest


def _ih_score(
    der_score: float, ddas_score: float, dcs_score: float, qcd_score: float
) -> float:
    """Replicates the composite IH score from invisible_hand_scanner.py:223-228."""
    return der_score * 0.35 + ddas_score * 0.30 + dcs_score * 0.20 + qcd_score * 0.15


def _der_score(der_ratio: float) -> float:
    """Replicates der_ratio→der_score mapping from invisible_hand_scanner.py:180."""
    return min(100.0, max(0.0, (der_ratio - 1.0) / 2.0 * 100))


def _der_ratio_impl(curr_der: float, hist_der: float) -> float:
    """Replicates der_ratio logic from invisible_hand_scanner.py:179."""
    return curr_der / hist_der if hist_der > 0.1 else 1.0


# ---- ih_score tests ----


def test_ih_score_all_max():
    assert _ih_score(100, 100, 100, 100) == 100.0


def test_ih_score_all_zero():
    assert _ih_score(0, 0, 0, 0) == 0.0


def test_ih_score_der_only():
    assert _ih_score(100, 0, 0, 0) == 35.0


def test_ih_score_ddas_only():
    assert _ih_score(0, 100, 0, 0) == 30.0


def test_ih_score_dcs_only():
    assert _ih_score(0, 0, 100, 0) == 20.0


def test_ih_score_qcd_only():
    assert _ih_score(0, 0, 0, 100) == 15.0


def test_ih_score_weights_sum():
    """Verify the weights sum to 100%."""
    weights = [0.35, 0.30, 0.20, 0.15]
    assert sum(weights) == 1.0


def test_ih_score_mid_values():
    result = _ih_score(50, 60, 70, 80)
    expected = 50 * 0.35 + 60 * 0.30 + 70 * 0.20 + 80 * 0.15
    assert result == expected


# ---- der_score tests ----


def test_der_ratio_one():
    assert _der_score(1.0) == 0.0


def test_der_ratio_two():
    assert _der_score(2.0) == 50.0


def test_der_ratio_three():
    assert _der_score(3.0) == 100.0


def test_der_ratio_below_one():
    assert _der_score(0.5) == 0.0


def test_der_ratio_one_point_five():
    assert _der_score(1.5) == 25.0


def test_der_ratio_four():
    """der_ratio=4 means 300% increase over baseline → score capped at 100."""
    assert _der_score(4.0) == 100.0


def test_der_ratio_one_point_two():
    """der_ratio=1.2 means 20% increase."""
    assert _der_score(1.2) == pytest.approx(10.0)


# ---- der_ratio_impl tests ----


def test_der_ratio_normal():
    assert _der_ratio_impl(3.0, 1.0) == 3.0


def test_der_ratio_hist_low():
    """When hist_der <= 0.1, ratio defaults to 1.0."""
    assert _der_ratio_impl(3.0, 0.05) == 1.0


def test_der_ratio_hist_zero():
    assert _der_ratio_impl(3.0, 0.0) == 1.0


def test_der_ratio_curr_equal_hist():
    assert _der_ratio_impl(2.0, 2.0) == 1.0
