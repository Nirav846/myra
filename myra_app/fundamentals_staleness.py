"""Shared staleness disclosure for fundamentals fields.

Some fundamentals fields lost their only writer when the Upstox fundamentals
write was removed (upstox_fetcher commit d2f0de6), and Morningstar does not
return a substitute for several of them. Any non-null value the API serves for
those fields is therefore a real-but-frozen snapshot. Rather than let it read as
current, both `/api/fundamentals/live/{symbol}` and the portfolio endpoint flag
per-field staleness with an age-based check.

Extracted from `myra_web/routes/fundamentals.py` so the portfolio route can
share one implementation instead of re-deriving the age arithmetic.

Background and measurements: docs/DB_CONTEXT.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# API fields whose historical writer was removed, and which are therefore
# candidates for a staleness flag. Keyed by the API field name the routes
# return; the comment gives the concept and the columns it reads.
#
# Being on this list does NOT mean the value is stale - it means we cannot
# assume it is fresh, so we measure it. Fields that a live writer has since
# repopulated (quick_ratio, payout_ratio, current_ratio) clear their own flag
# automatically once the row is refreshed.
FROZEN_SOURCE_FIELDS = (
    "pb",  # price-to-book      <- priceToBook
    "roe",  # return on equity    <- roe / returnOnEquity
    "quick_ratio",  # quick ratio          <- quick_ratio / quickRatio
    "revenue_growth",  # sales growth        <- revenueGrowth
    "earnings_growth",  # earnings growth   <- earningsGrowth
    "payout_ratio",  # payout ratio        <- payout_ratio / payoutRatio
    "current_ratio",  # current ratio      <- current_ratio / currentRatio
)

# A served value whose row has not been refreshed within this many days is
# reported as stale. Deliberately age-based rather than hardcoded "always
# stale": if a live writer lands and refreshes the row, last_updated advances
# past this threshold and the flag clears by itself with no code change.
STALENESS_MAX_AGE_DAYS = 30

# Row columns that can carry the timestamp, most specific first. `date` is
# deliberately not used: it is the fundamentals period, not a refresh time.
_TIMESTAMP_COLUMNS = ("last_updated", "last_fundamental_update")


def parse_timestamp(value):
    """Parse a fundamentals timestamp, or return None if absent/unparseable.

    Handles the formats actually present in the table: ISO with offset
    ('2026-08-31T19:04:49.052692+00:00'), naive ISO ('2026-09-24T13:15:40')
    and bare date ('2026-04-04'). Naive values are read as UTC so every
    timestamp in the table is compared on the same clock.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def row_as_of(row):
    """Best available refresh timestamp for a fundamentals row, or None."""
    for column in _TIMESTAMP_COLUMNS:
        try:
            parsed = parse_timestamp(row.get(column))
        except (AttributeError, TypeError):
            continue
        if parsed is not None:
            return parsed
    return None


def staleness_flags(merged, row, now=None):
    """Build '<field>_stale' flags for FROZEN_SOURCE_FIELDS.

    A field is stale when the route is serving a non-null value AND that value
    cannot be trusted as current. That covers two cases:

    * the row's refresh timestamp is older than STALENESS_MAX_AGE_DAYS, or
    * the row carries no usable timestamp at all, in which case we cannot claim
      the value is current and must say so (several legacy rows have
      last_updated IS NULL).

    A field whose value is null is never stale - nothing is being served, so
    there is nothing misleading to flag. Zero is a real value, not missing
    data, and is evaluated normally.

    Args:
        merged: the fundamentals dict being returned (values are read from it).
        row: the raw fundamentals row, used for the refresh timestamp.
        now: override for the current time (tests); defaults to UTC now.

    Returns:
        dict of field name -> bool, e.g. {"pb_stale": True, "roe_stale": False}.
    """
    reference = now or datetime.now(timezone.utc)
    as_of = row_as_of(row)
    cutoff = reference - timedelta(days=STALENESS_MAX_AGE_DAYS)

    flags = {}
    for field in FROZEN_SOURCE_FIELDS:
        if merged.get(field) is None:
            flags[f"{field}_stale"] = False
            continue
        flags[f"{field}_stale"] = as_of is None or as_of < cutoff
    return flags
