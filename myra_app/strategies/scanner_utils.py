"""Shared utilities for all scanners."""

import math


def sanitize_float(value):
    """Return *value* unchanged, or None if it is NaN / Inf / None."""
    if value is None:
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
    except TypeError:
        pass
    return value
