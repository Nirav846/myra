"""Shared correctness helpers for reading fundamentals fields.

Two concerns, one home, because both are about not serving a misleading value
and both have already gone wrong independently:

* **Column resolution** - `canonical_or_legacy` picks the right column. Several
  fields have a canonical snake_case column that a live writer now populates,
  sitting beside a legacy camelCase column holding a frozen pre-2026-09-01
  value. Reading the legacy one serves stale data or nothing at all.
* **Staleness disclosure** - `staleness_flags` flags what is still frozen, so
  the value that is served cannot be mistaken for current.

Extracted from `myra_web/routes/fundamentals.py` so the portfolio route shares
one implementation instead of re-deriving the same arithmetic.

Background and measurements: docs/DB_CONTEXT.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def canonical_or_legacy(row, canonical_col, legacy_col):
    """Return the canonical column's value, falling back to legacy only if NULL.

    Prefers the canonical snake_case column because a live writer populates it;
    the legacy camelCase column holds a value frozen when the Upstox writer was
    removed and will never refresh.

    The NULL test is ``is not None``, never truthiness. These are ratios where
    0.0 is a real, reportable value (a company with no quick or current assets,
    or no dividend paid) - an ``or`` chain would silently drop it and let the
    frozen legacy value win.

    Args:
        row: a fundamentals row as a mapping (sqlite3.Row or dict).
        canonical_col: preferred column name.
        legacy_col: fallback column name, used only when canonical is NULL.

    Returns:
        The canonical value, else the legacy value, else None.
    """
    value = row.get(canonical_col)
    if value is not None:
        return value
    return row.get(legacy_col)


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
