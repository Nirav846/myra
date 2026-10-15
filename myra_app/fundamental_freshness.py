"""Fundamental-data provenance and freshness (Phase 11 Priority 2).

Purpose
-------
MYRA's fundamental columns mix numbers from at least four different kinds of
thing: prices-derived values, bulk-provider financials, month-end shareholding,
and a set of columns that exist in the schema but have **no producer anywhere in
the repository**. A coverage percentage alone hides that difference, and a
consumer that treats "non-NULL" as "trustworthy" will happily rank a stock on a
number nobody produces.

This module states, per field:

* whether a producer actually exists (``verified`` / ``unverified`` /
  ``unavailable``), so an empty column is labelled honestly instead of looking
  like a transient gap;
* the units, because a percentage and a fraction are the same SQL type;
* how old the *information* is, keeping these three strictly separate:

  ====================  =========================================================
  reporting period      the period the financials describe (e.g. 2026-09-30)
  retrieved at          when *we* last fetched the row (not a publication date)
  updated in db         when the row was last written locally
  ====================  =========================================================

A row retrieved today that describes a quarter ending eight months ago is
**not** fresh financial information, and this module says so.

Deliberate non-goals
--------------------
* No scoring weights, ranking defaults, or screening thresholds are touched.
* Nothing here converts a missing fundamental to zero. Missing stays missing.
* Phase 6's opt-in ranking inputs remain opt-in; this module only *reports*.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

# ── freshness states ─────────────────────────────────────────────────────────

FRESH = "fresh"
AGING = "aging"
STALE = "stale"
UNKNOWN = "unknown"

FRESHNESS_STATES = (FRESH, AGING, STALE, UNKNOWN)

#: Cadence assumptions, by the *kind* of source behind a field. These are policy,
#: not source-confirmed facts, and are labelled as such wherever surfaced.
#:
#: Market-derived values move daily; company financials are only published
#: quarterly, so demanding daily freshness of them would mark almost everything
#: stale and train the operator to ignore the indicator.
CADENCE_DAYS: dict[str, int] = {
    "daily": 7,  # price/shares derived; tolerate a week (holidays, gaps)
    "monthly": 45,  # month-end shareholding
    "quarterly": 120,  # published financials: ~1 quarter + reporting lag
}

# ── provenance, verified against the code, not the schema ───────────────────
#
# `status` is the honest answer to "does a producer exist?":
#   "verified"   - a writer was found in the repository
#   "unverified" - values exist but the producing path could not be confirmed
#   "unavailable"- no producer found and/or no data at all

PROVENANCE: dict[str, dict[str, Any]] = {
    "market_cap": {
        "status": "verified",
        "producer": "fundamental_sync._compute_market_cap_from_prices (derived)",
        "units": "INR",
        "cadence": "daily",
        "kind": "market",
    },
    "shares_outstanding": {
        "status": "verified",
        "producer": "fundamental_sync (shares issued)",
        "units": "shares",
        "cadence": "daily",
        "kind": "market",
    },
    "pe": {
        "status": "verified",
        "producer": "fundamental_sync (bulk provider)",
        "units": "ratio",
        "cadence": "daily",
        "kind": "market",
    },
    "current_ratio": {
        "status": "verified",
        "producer": "fundamental_sync (bulk provider)",
        "units": "ratio",
        "cadence": "quarterly",
        "kind": "financial",
    },
    "roe": {
        "status": "verified",
        "producer": "fundamental_sync (Screener.in / bulk) + fundamentals.returnOnEquity",
        "units": "percent (0-100)",
        "cadence": "quarterly",
        "kind": "financial",
        "caveat": "values above ~100 are possible for high-leverage firms; "
        "negative values are legitimate losses",
    },
    "net_margin": {
        "status": "verified",
        # Phase 13: the producer and the units are both confirmed now. The
        # earlier "MIXED / up to 699 = percent" reading was wrong - see caveat.
        "producer": "fundamental_sync._fetch_morningstar_bulk -> MS_CANONICAL_MAP"
        "['netMargin'] -> net_margin. Faithful pass-through; no scaling is"
        " applied in either direction.",
        "units": "fraction (0.1442 = 14.42%). Never a percent - do not compare a"
        " stored value against a 0-100 threshold.",
        "cadence": "quarterly",
        "kind": "financial",
        "caveat": "Confirmed against the live source, not inferred: 20MICRONS"
        "=0.07137, 360ONE=0.37408, 3BBLACKBIO=0.37129, all fractions."
        " The 'mixed units' defect previously recorded here did NOT hold - of"
        " 3309 non-null rows, 95.1% are |v|<=1 and every row above 1 is a"
        " fraction whose denominator is near-zero revenue (e.g. MMTC 163.87,"
        " RAINBOWPAP -26570.08, ESSARSHPNG 699.92 all verified byte-for-byte"
        " against the source, and ESSARSHPNG's value is stable across three"
        " archived snapshots). Those are genuine distressed/penny-sheet"
        " inputs, not a stored percent. Display code must multiply by 100;"
        " ranking code must not treat these as percentages.",
    },
    "inst_holding": {
        "status": "verified",
        "producer": "institutional sidecar, month-end shareholding pattern",
        "units": "percent",
        "cadence": "monthly",
        "kind": "ownership",
        "caveat": "month-end shareholding is a stock, not a flow. It cannot "
        "show who bought during a quarter and must not be presented as "
        "'smart money activity'.",
    },
    "dividend_yield": {
        "status": "verified",
        "producer": "fundamental_sync (bulk provider)",
        "units": "percent",
        "cadence": "quarterly",
        "kind": "financial",
    },
    "profit_growth": {
        "status": "unavailable",
        "producer": "NONE - mapping exists in fundamental_sync.MS_CANONICAL_MAP "
        "(earningsGrowth -> profit_growth) but the upstream field is never "
        "populated",
        "units": "unknown",
        "cadence": "quarterly",
        "kind": "financial",
    },
    "sales_growth": {
        "status": "unavailable",
        "producer": "NONE - same dead mapping (revenueGrowth -> sales_growth)",
        "units": "unknown",
        "cadence": "quarterly",
        "kind": "financial",
    },
    "debt_to_equity": {
        "status": "unavailable",
        "producer": "NONE - mapping exists (debtToEquity -> debt_to_equity), "
        "upstream never populated",
        "units": "unknown",
        "cadence": "quarterly",
        "kind": "financial",
    },
    "roe_ttm": {
        "status": "unavailable",
        # Phase 13: a producer *was* written, but it can never fire. Two
        # independent defects, both required for the column to populate.
        "producer": "fundamental_sync writes it from row['roeTTM'], but"
        " (1) 'roeTTM' is absent from the Morningstar securityDataPoints"
        " request, and the API honours that list - verified: with the current"
        " field list the response contains no roeTTM key at all, while adding"
        " 'roeTTM' to the request returns values; and (2) the merge step reads"
        " ms['roeTTM'] although the fetch stored the value under 'roe_ttm'."
        " Either one alone leaves the column NULL; both must be fixed.",
        "units": "source value is a fraction (0.16485 = 16.485%); the sibling"
        " row `roe` is a percent (median 12.42). Normalising would be required"
        " before any future consumer treats them alike.",
        "cadence": "quarterly",
        "kind": "financial",
        "caveat": "Left NULL on purpose in Phase 13: there is no consumer, and"
        " populating it would introduce a second, differently-scaled"
        " profitability column next to `roe`. The fix recipe is recorded"
        " above rather than applied.",
    },
    "smart_money_score": {
        "status": "unavailable",
        "producer": "NONE - read with a 0 default in universe_loader and "
        "worker_pool; never written. Phase 6 documented the same blocker and "
        "ranking_inputs deliberately does not produce it.",
        "units": "unknown",
        "cadence": "daily",
        "kind": "derived",
    },
}


def provenance(field: str) -> dict[str, Any]:
    """Provenance for one field, with ``status`` defaulting to unverified."""
    return dict(
        PROVENANCE.get(field)
        or {
            "status": "unverified",
            "producer": "no provenance recorded",
            "units": "unknown",
            "cadence": "quarterly",
            "kind": "financial",
        }
    )


def _parse(value: Any) -> Optional[date]:
    """Parse a stored timestamp leniently; return None rather than guess."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # ISO first (handles 'YYYY-MM-DDTHH:MM:SS' and trailing 'Z').
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[: len(fmt) + 6].strip(), fmt).date()
        except ValueError:
            continue
    return None


def classify(
    reporting_period: Any = None,
    retrieved_at: Any = None,
    cadence: str = "quarterly",
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Freshness of the *information*, not of our fetch.

    ``retrieved_at`` alone can never make stale financials look fresh: when a
    reporting period is known, that period is what decides. When nothing is
    known the answer is ``unknown`` - never ``fresh``.
    """
    cadence_days = CADENCE_DAYS.get(cadence, CADENCE_DAYS["quarterly"])
    today = today or date.today()
    period = _parse(reporting_period)
    retrieved = _parse(retrieved_at)

    if period is None:
        return {
            "state": UNKNOWN,
            "age_days": None,
            "cadence": cadence,
            "cadence_days": cadence_days,
            "basis": "no reporting period recorded",
        }

    age = (today - period).days
    if age < 0:
        # A future period is either a typo or a projection; neither is evidence.
        return {
            "state": UNKNOWN,
            "age_days": age,
            "cadence": cadence,
            "cadence_days": cadence_days,
            "basis": f"reporting period {period} is in the future",
        }
    if age <= cadence_days:
        state = FRESH
    elif age <= cadence_days * 2:
        state = AGING
    else:
        state = STALE
    return {
        "state": state,
        "age_days": age,
        "cadence": cadence,
        "cadence_days": cadence_days,
        "basis": f"reporting period {period} is {age} days old",
        "retrieved_at": retrieved.isoformat() if retrieved else None,
    }


def assess_row(
    reporting_period: Any = None,
    retrieved_at: Any = None,
    cadence: str = "quarterly",
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Freshness plus an explicit note that retrieval time is not the period."""
    out = classify(reporting_period, retrieved_at, cadence, today)
    retrieved = _parse(retrieved_at)
    out["retrieved_at"] = retrieved.isoformat() if retrieved else None
    out["reporting_period"] = (
        _parse(reporting_period).isoformat() if _parse(reporting_period) else None
    )
    out["note"] = (
        "reporting period describes the information; retrieval time only "
        "describes our fetch. A recent fetch of an old period is still old."
    )
    return out


__all__ = [
    "FRESH",
    "AGING",
    "STALE",
    "UNKNOWN",
    "FRESHNESS_STATES",
    "CADENCE_DAYS",
    "PROVENANCE",
    "provenance",
    "classify",
    "assess_row",
]
