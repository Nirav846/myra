"""Validated annual (YoY) growth signals from ``upstox_income_statement``.

Phase 14. The production ``fundamentals`` table holds **no** growth data at all
(``profit_growth`` / ``sales_growth`` are non-NULL on 0 of 3,948 rows), so the
v2.5 scorer's growth input has never contributed anything but its documented
neutral fallback. ``upstox_income_statement`` does contain real reported growth,
but its meaning had to be established before anything could consume it.

This module encodes that established definition. It is **additive and
read-only**: it never writes to a database, never changes scoring weights or
thresholds, and is not wired into the live ranking path. Its only consumer is
the opt-in shadow comparison tool.

Verified source contract (checked against the live database, not inferred)
-------------------------------------------------------------------------
* Table ``upstox_income_statement`` in the valuation database
  (``schema/valuation.sql``), primary key
  ``(isin, period, report_type, time_period)``.
* Only ``time_period = 'yearly'`` rows take part. The ``quarterly`` rows are
  **quarter-on-quarter** comparisons -- a same-quarter-previous-year pair does
  not exist in the source at all (lag-4 matching yields 0 pairs), so quarterly
  data can never be turned into valid YoY growth and is excluded outright.
* ``period`` is a ``"Mar 2026"``-style label. Fiscal year ends are
  **company-specific**: the live table holds March (26 labels), December (23),
  June (23), September (22) and November (5) year-ends, so a December
  assumption would corrupt a large minority of the universe.
* ``net_profit`` / ``revenue`` are reported in **crore``(``units_in`` is
  ``'crore'`` on every row); ``report_type`` is ``consolidated`` throughout.
* Growth is a **percentage** and is defined as
  ``(current - prior) / abs(prior) * 100``. The absolute denominator is the
  provider's own convention: a swing from a loss to a profit reads positive
  rather than negative. That convention is reproduced here so the signal agrees
  with the provider rather than merely looking similar.

Missing is missing
------------------
An observation that cannot be computed is returned with ``profit_growth=None``
and a machine-readable ``reason``. It is **never** coerced to ``0``: the
``0`` sentinel is what made this field useless upstream (a constant ``0`` column
ranks at the *maximum* percentile, so "no data" silently received the best
possible growth rank). Every exclusion is counted by :func:`coverage_report`.
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

#: Table that owns the raw annual figures.
SOURCE_TABLE = "upstox_income_statement"

#: The only ``time_period`` accepted for a year-on-year signal.
ANNUAL_TIME_PERIOD = "yearly"

#: The non-annual period type, recorded so exclusions are auditable.
QUARTERLY_TIME_PERIOD = "quarterly"

#: Source unit for ``revenue`` / ``net_profit`` (all live rows).
SOURCE_UNIT = "crore"

#: Scorer metric key written into ``results`` rows by
#: :func:`myra_app.ranking_inputs.attach_ranking_inputs`. Matches the column
#: ``myra_app.score_components_v2.precompute_ranks`` reads for ``_rank_growth``.
GROWTH_METRIC_KEY = "ProfitGrowth"

#: Revenue growth is derived and reported for context, but it is deliberately
#: **not** a v2.5 scorer input (``precompute_ranks`` never reads it), so it
#: cannot move a ranking. Kept so the evidence is complete.
REVENUE_METRIC_KEY = "SalesGrowth"

#: Database key resolved through :data:`myra_app.constants.DB_DIR`.
SOURCE_DB_KEY = "valuation"

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Exclusion reasons. ``None`` of these ever yields a value; they exist so the
# audit can attribute every unavailable symbol to a specific cause.
REASON_ELIGIBLE = "eligible"
REASON_NO_ANNUAL_RECORDS = "no_annual_records"
REASON_NO_PRIOR_PERIOD = "no_prior_period"
REASON_NON_CONSECUTIVE = "non_consecutive_periods"
REASON_CONFLICTING = "conflicting_records"
REASON_STALE = "stale_period"
REASON_MISSING_VALUES = "missing_values"
REASON_MISSING_FRESHNESS = "missing_freshness_metadata"
REASON_ZERO_BASE = "zero_prior_base"
REASON_BASE_BELOW_FLOOR = "prior_base_below_floor"

REASONS = (
    REASON_ELIGIBLE,
    REASON_NO_ANNUAL_RECORDS,
    REASON_NO_PRIOR_PERIOD,
    REASON_NON_CONSECUTIVE,
    REASON_CONFLICTING,
    REASON_STALE,
    REASON_MISSING_VALUES,
    REASON_MISSING_FRESHNESS,
    REASON_ZERO_BASE,
    REASON_BASE_BELOW_FLOOR,
)


@dataclass(frozen=True)
class FiscalPeriod:
    """A fiscal year end, e.g. ``Mar 2026`` -> (2026, 3)."""

    year: int
    month: int
    label: str

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.year, self.month)


@dataclass(frozen=True)
class AnnualRecord:
    """One row of ``upstox_income_statement`` (or an injected test equivalent).

    ``time_period`` defaults to annual so a caller building a single-fiscal-year
    fixture does not have to spell it out; :func:`derive_growth` still filters on
    it so a quarterly row can never be mistaken for an annual one.
    """

    symbol: str
    period: str | None
    net_profit: float | None
    revenue: float | None = None
    isin: str | None = None
    time_period: str | None = ANNUAL_TIME_PERIOD
    net_profit_change: str | None = None
    revenue_change: str | None = None
    fetched_at: str | None = None


@dataclass(frozen=True)
class GrowthObservation:
    """The derived (or refused) annual growth signal for one symbol."""

    symbol: str
    profit_growth: float | None
    sales_growth: float | None
    latest_period: str | None
    prior_period: str | None
    prior_net_profit_cr: float | None
    reason: str
    provider_profit_change: str | None = None

    @property
    def eligible(self) -> bool:
        """True only when a usable YoY number was derived."""
        return self.profit_growth is not None


@dataclass(frozen=True)
class EligibilityPolicy:
    """Documented, explicit eligibility thresholds.

    ``min_prior_base_cr``: growth measured off a near-zero prior base is not a
    comparable cross-sectional observation. The floor is applied to the *prior
    period's stored base* (an input), never to the resulting growth value (an
    output) — discarding results by magnitude alone would hide real business
    changes behind a statistical filter.

    ``freshness_years``: how far behind the newest fiscal year present in the
    source a symbol's latest annual period may sit before it is treated as
    stale rather than current.

    ``require_freshness_metadata``: when set, the latest period's ``fetched_at``
    must be present and parseable; an unverifiable record is treated as
    unavailable rather than assumed current.
    """

    min_prior_base_cr: float = 1.0
    freshness_years: int = 1
    require_freshness_metadata: bool = True

    def __post_init__(self) -> None:
        if self.min_prior_base_cr < 0:
            raise ValueError("min_prior_base_cr must be non-negative")
        if self.freshness_years < 0:
            raise ValueError("freshness_years must be non-negative")


def parse_period(label: str | None) -> FiscalPeriod | None:
    """Parse an Upstox period label (``'Mar 2026'``) into a :class:`FiscalPeriod`.

    Returns ``None`` for anything that is not a well-formed ``Mon YYYY`` label,
    so a malformed row is reported as invalid instead of being silently dropped
    or mis-ordered.
    """
    if not label:
        return None
    parts = str(label).strip().split()
    if len(parts) != 2:
        return None
    month = _MONTHS.get(parts[0][:3].lower())
    if month is None:
        return None
    try:
        year = int(parts[1])
    except ValueError:
        return None
    if not 1990 <= year <= 2100:
        return None
    return FiscalPeriod(year=year, month=month, label=str(label).strip())


def parse_fetched_at(value: str | None) -> datetime | None:
    """Parse an ISO-8601 ``fetched_at`` timestamp, or ``None`` if unusable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_percent(text: str | None) -> float | None:
    """Parse a provider change string such as ``'+10.18%'`` into ``10.18``.

    Used only to cross-check the derived number against the provider's own
    field; the signal itself is always computed from the stored levels.
    """
    if text is None:
        return None
    cleaned = str(text).strip().rstrip("%").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def year_over_year(current: float | None, prior: float | None) -> float | None:
    """``(current - prior) / abs(prior) * 100`` as a percentage.

    ``abs(prior)`` is the provider's convention: a swing from a loss to a profit
    reads positive. Returns ``None`` when either level is missing, when the
    prior base is exactly zero, or when the result is not finite.
    """
    if current is None or prior is None or prior == 0:
        return None
    value = (float(current) - float(prior)) / abs(float(prior)) * 100.0
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def connect_readonly(db_key: str = SOURCE_DB_KEY) -> sqlite3.Connection:
    """Open a MYRA database strictly read-only.

    The URI carries ``mode=ro`` so the connection cannot mutate the database
    even if a caller tries; the path is always resolved through
    :data:`myra_app.constants.DB_DIR` and ``LibrarianCore.DB_MAP`` rather than
    being hardcoded.
    """
    from myra_app import constants
    from myra_app.librarian_core import LibrarianCore

    path = os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP[db_key])
    uri = "file:{}?mode=ro".format(path.replace("\\", "/"))
    return sqlite3.connect(uri, uri=True)


def read_annual_records(
    conn: sqlite3.Connection, symbols=None, time_period: str | None = None
) -> list[AnnualRecord]:
    """Read raw source rows as :class:`AnnualRecord` objects.

    ``time_period`` is left unset by default so the adapter mirrors the table
    faithfully and :func:`derive_growth` performs the annual filtering (and can
    therefore *count* the quarterly rows it ignores).
    """
    conditions, params = [], []
    if time_period is not None:
        conditions.append("time_period = ?")
        params.append(time_period)
    wanted = [str(sym).upper() for sym in symbols or [] if str(sym).strip()]
    if wanted:
        placeholders = ",".join(["?"] * len(wanted))
        conditions.append(f"symbol IN ({placeholders})")
        params.extend(wanted)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        "SELECT symbol, isin, period, time_period, net_profit, revenue,"
        " net_profit_change, revenue_change, fetched_at"
        f" FROM {SOURCE_TABLE}{where}"
    )
    return [
        AnnualRecord(
            symbol=str(row[0] or ""),
            isin=row[1],
            period=row[2],
            time_period=row[3],
            net_profit=None if row[4] is None else float(row[4]),
            revenue=None if row[5] is None else float(row[5]),
            net_profit_change=row[6],
            revenue_change=row[7],
            fetched_at=row[8],
        )
        for row in conn.execute(sql, params)
    ]


def _collapse(entries):
    """Reduce equal-period records to one, or flag a genuine conflict.

    Duplicate rows with identical levels are harmless repeats; records that
    disagree about the same fiscal period are not resolvable from this source,
    so the symbol is refused rather than an arbitrary row being preferred.
    """
    levels = {(entry.net_profit, entry.revenue) for entry in entries}
    if len(levels) > 1:
        return None
    return entries[0]


def derive_growth(
    records,
    policy: EligibilityPolicy | None = None,
    reference_year: int | None = None,
) -> dict[str, GrowthObservation]:
    """Derive the annual YoY growth signal for every symbol in ``records``.

    Pure function: it reads nothing and writes nothing, so the entire
    eligibility contract is unit-testable without a database.

    ``reference_year`` is the newest fiscal year considered current; it defaults
    to the newest annual year present in ``records`` and can be pinned by a
    caller that wants a fixed, reproducible cutoff.
    """
    policy = policy or EligibilityPolicy()

    annual: dict[str, list[AnnualRecord]] = defaultdict(list)
    seen: set[str] = set()
    for record in records or []:
        symbol = str(getattr(record, "symbol", "") or "").strip().upper()
        if not symbol:
            continue
        seen.add(symbol)
        if str(record.time_period or "").strip().lower() != ANNUAL_TIME_PERIOD:
            continue
        series = annual.setdefault(symbol, [])
        period = parse_period(record.period)
        if period is None:
            # Keep an explicit sentinel so the symbol is recorded as invalid
            # rather than silently appearing to have no annual history.
            series.append((None, record))
            continue
        series.append((period, record))

    if reference_year is None:
        years = [
            period.year
            for series in annual.values()
            for period, _record in series
            if period is not None
        ]
        reference_year = max(years) if years else None

    observations: dict[str, GrowthObservation] = {}
    symbols = {
        str(record.symbol).upper()
        for record in records
        if str(record.symbol or "").strip()
    }
    for symbol in symbols:
        observations[symbol] = _observe(
            symbol, annual.get(symbol, []), policy, reference_year
        )
    return observations


def _observe(symbol, series, policy, reference_year):
    """Apply the eligibility contract to one symbol's annual rows."""
    if not series:
        return _refused(symbol, REASON_NO_ANNUAL_RECORDS)

    by_month: dict[int, list[tuple[FiscalPeriod, AnnualRecord]]] = defaultdict(list)
    for period, record in series:
        if period is None:
            continue
        by_month[period.month].append((period, record))
    if not by_month:
        return _refused(symbol, REASON_NO_PRIOR_PERIOD)

    # Company-specific year ends: evaluate each fiscal-end month on its own and
    # keep the cohort containing the most recent annual period.
    pairs = []
    failures = []
    for month, cohort in by_month.items():
        buckets: dict[tuple[int, int], list[AnnualRecord]] = defaultdict(list)
        for period, record in cohort:
            buckets[period.sort_key].append(record)

        resolved = []
        conflicted = False
        for key in sorted(buckets):
            chosen = _collapse(buckets[key])
            if chosen is None:
                conflicted = True
                break
            resolved.append((key, chosen))

        newest_key = max(buckets)
        if conflicted:
            failures.append((newest_key, REASON_CONFLICTING))
            continue
        if len(resolved) < 2:
            failures.append((newest_key, REASON_NO_PRIOR_PERIOD))
            continue
        latest_key, latest = resolved[-1]
        prior_key, prior = resolved[-2]
        if latest_key[0] - prior_key[0] != 1:
            failures.append((latest_key, REASON_NON_CONSECUTIVE))
            continue
        pairs.append((latest_key, month, prior_key, latest, prior))

    if not pairs:
        # No cohort yielded a consecutive pair; report the most recent reason so
        # the audit explains the symbol's newest filing rather than an old one.
        failures.sort(key=lambda item: item[0])
        reason = failures[-1][1] if failures else REASON_NO_PRIOR_PERIOD
        return _refused(symbol, reason)

    pairs.sort(key=lambda item: item[0])
    latest_key, month, prior_key, latest, prior = pairs[-1]
    latest_period = f"{_month_name(month)} {latest_key[0]}"
    prior_period = f"{_month_name(month)} {prior_key[0]}"

    if latest.net_profit is None or prior.net_profit is None:
        return _refused(symbol, REASON_MISSING_VALUES, latest_period, prior_period)

    if (
        policy.require_freshness_metadata
        and parse_fetched_at(latest.fetched_at) is None
    ):
        return _refused(symbol, REASON_MISSING_FRESHNESS, latest_period, prior_period)

    if reference_year is not None and (
        latest_key[0] < reference_year - policy.freshness_years
    ):
        return _refused(symbol, REASON_STALE, latest_period, prior_period)

    if prior.net_profit == 0:
        return _refused(symbol, REASON_ZERO_BASE, latest_period, prior_period)
    if abs(prior.net_profit) < policy.min_prior_base_cr:
        return _refused(
            symbol,
            REASON_BASE_BELOW_FLOOR,
            latest_period,
            prior_period,
            prior_base=prior.net_profit,
        )

    profit_growth = year_over_year(latest.net_profit, prior.net_profit)
    if profit_growth is None:
        return _refused(symbol, REASON_MISSING_VALUES, latest_period, prior_period)

    return GrowthObservation(
        symbol=symbol,
        profit_growth=profit_growth,
        sales_growth=year_over_year(latest.revenue, prior.revenue),
        latest_period=latest_period,
        prior_period=prior_period,
        prior_net_profit_cr=prior.net_profit,
        reason=REASON_ELIGIBLE,
        provider_profit_change=latest.net_profit_change,
    )


_MONTH_NAMES = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


def _month_name(month: int) -> str:
    return _MONTH_NAMES.get(month, "?")


def _refused(symbol, reason, latest_period=None, prior_period=None, prior_base=None):
    """Build an explicitly-unavailable observation (never a zero)."""
    return GrowthObservation(
        symbol=symbol,
        profit_growth=None,
        sales_growth=None,
        latest_period=latest_period,
        prior_period=prior_period,
        prior_net_profit_cr=prior_base,
        reason=reason,
    )


def growth_inputs(observations, metric: str = GROWTH_METRIC_KEY) -> dict[str, dict]:
    """Eligible observations as ``{SYMBOL: {metric: value}}`` for the scorer.

    Only eligible symbols appear, and no ``None`` is ever emitted, so passing
    this to :func:`myra_app.ranking_inputs.attach_ranking_inputs` cannot
    overwrite a real value with an unavailable one.
    """
    return {
        symbol: {metric: observation.profit_growth}
        for symbol, observation in observations.items()
        if observation.profit_growth is not None
    }


def coverage_report(observations) -> dict:
    """Count eligible symbols and attribute every exclusion to a reason."""
    counts = dict.fromkeys(REASONS, 0)
    for observation in observations.values():
        counts[observation.reason] = counts.get(observation.reason, 0) + 1
    eligible = counts[REASON_ELIGIBLE]
    return {
        "symbols": len(observations),
        "eligible": eligible,
        "unavailable": len(observations) - eligible,
        "reasons": counts,
    }


__all__ = [
    "ANNUAL_TIME_PERIOD",
    "AnnualRecord",
    "EligibilityPolicy",
    "FiscalPeriod",
    "GROWTH_METRIC_KEY",
    "GrowthObservation",
    "QUARTERLY_TIME_PERIOD",
    "REASONS",
    "REASON_BASE_BELOW_FLOOR",
    "REASON_CONFLICTING",
    "REASON_ELIGIBLE",
    "REASON_MISSING_FRESHNESS",
    "REASON_MISSING_VALUES",
    "REASON_NON_CONSECUTIVE",
    "REASON_NO_ANNUAL_RECORDS",
    "REASON_NO_PRIOR_PERIOD",
    "REASON_STALE",
    "REASON_ZERO_BASE",
    "REVENUE_METRIC_KEY",
    "SOURCE_DB_KEY",
    "SOURCE_TABLE",
    "SOURCE_UNIT",
    "connect_readonly",
    "coverage_report",
    "derive_growth",
    "growth_inputs",
    "parse_fetched_at",
    "parse_percent",
    "parse_period",
    "read_annual_records",
    "year_over_year",
]
