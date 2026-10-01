"""Shared plausibility guard for ratio-style fundamentals fields.

Some upstream ratio fields are mathematically valid but economically
meaningless. Morningstar's `quickRatio` and `currentRatio` are the live case:
both divide by current liabilities, which approaches zero for companies holding
almost no short-term debt. The result is real arithmetic on a real balance
sheet producing values like 13935.9 - a "quick ratio" no reader can act on.
Measured across 3336 NSE securities: 98 `quickRatio` values (3.0%) and 136
`currentRatio` values (4.2%) exceed 10.0, against a median near 1.8. The values
are correctly scaled (not a units bug) and correctly ordered
(`quick < current`), so the only safe response is to suppress the implausible
ones rather than to reinterpret them.

This is the landing place for ratio-plausibility checks going forward. The same
fix pattern has now appeared independently in several places - inline sanity
bounds in `myra_app/strategies/dcb_bargain.py` and `_sanitize_float` in
`myra_app/strategies/bottom_hunter.py` both predate this module. Those call
sites are deliberately left untouched: they are working, tested code and
refactoring them is out of scope. New ratio fields should come through
`sanitize_ratio` instead of growing another inline bound.

Note on the choice of a numeric bound over a sector guard: the implausible
values are finance-heavy but **not** confined to finance. They appear in 10 of
11 sectors, so a sector allow/deny list would miss roughly half of them while
adding a rule that asserts a causal story the data does not support.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Documented default ceiling for liquidity ratios (quick ratio, current ratio).
# A genuine quick or current ratio essentially never exceeds single digits for
# any sector; a company with near-zero current liabilities is what pushes the
# arithmetic into the hundreds or thousands, and the resulting figure carries
# no information about solvency. Suppressing above this bound is deliberately
# cause-agnostic: it does not assume to know *why* a value is absurd.
DEFAULT_RATIO_MAX_BOUND = 10.0


def sanitize_ratio(value, max_bound=DEFAULT_RATIO_MAX_BOUND, field_name="ratio"):
    """Return `value` if it is a plausible ratio, else None (logged).

    A value is implausible - and therefore suppressed to None - when it is
    non-numeric, NaN/inf, negative, or above `max_bound`. Suppression rather
    than clamping or winsorising: a clamped 10.0 still asserts a level of
    precision the data does not support, and the caller cannot distinguish
    "measured 10" from "garbage capped to 10". None is honest.

    Zero passes. It is a real, reportable value (a company with no quick or
    current assets), not missing data.

    Args:
        value: the raw field value; None passes through as None unserved.
        max_bound: exclusive upper bound; see DEFAULT_RATIO_MAX_BOUND.
        field_name: used only for the warning message, so an operator can trace
            which field was suppressed and why.

    Returns:
        The value as a float, or None if implausible/absent.
    """
    if value is None:
        return None

    try:
        num = float(value)
    except (TypeError, ValueError):
        logger.warning(
            "[sanitize_ratio] %s: non-numeric value %r - suppressed", field_name, value
        )
        return None

    if math.isnan(num) or math.isinf(num):
        logger.warning(
            "[sanitize_ratio] %s: non-finite value %r - suppressed", field_name, value
        )
        return None

    if num < 0:
        logger.warning(
            "[sanitize_ratio] %s: negative value %r is not a valid ratio - suppressed",
            field_name,
            value,
        )
        return None

    if num > max_bound:
        logger.warning(
            "[sanitize_ratio] %s: value %r exceeds plausible bound %s - suppressed "
            "(likely near-zero current liabilities making the ratio uninformative)",
            field_name,
            value,
            max_bound,
        )
        return None

    return num
