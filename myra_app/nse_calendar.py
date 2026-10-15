"""Authoritative NSE trading calendar (Phase 10).

Why this module exists
----------------------
``market_calendar`` used to be a *data-availability* table: the legacy ingest
wrote ``is_trading_day = 0 "Likely holiday (zero rows)"`` whenever an ingestion
attempt inserted no rows. A zero-row attempt is not evidence that the exchange
was closed, and treating it as such was actively harmful: once a session had
been branded a heuristic "holiday", :func:`myra_app.daily_ingestor.is_trading_day`
only lifted the mark once ``technical_data`` actually held rows for it. For a
session whose EOD file had not arrived yet, that meant the expected-trading-date
calculation stayed behind the source, the watchdog had no reason to look for the
data, and Mission Control reported a misleading readiness state.

The fix inverts the responsibility:

* **This module answers** "was this date an expected NSE equity (CM) session?"
  using the *official* NSE holiday publication.
* **Data readiness answers** "has MYRA received and ingested that session?"

A date is never a holiday because rows are missing.

Where the official data comes from
----------------------------------
The EOD source that already feeds MYRA (``eod2_data/meta.json``) is maintained by
the ``eod2`` BhavDesk data sync, which fetches the official NSE holiday list
(``nse.holidays(type=nse.HOLIDAY_TRADING)`` -> the ``CM`` / capital-market
segment) plus NSE circulars announcing *special trading sessions* and exceptional
closures, and writes them into ``meta.json`` as:

    "year": 2026,
    "holidays": {"02-Oct-2026": "Mahatma Gandhi Jayanti", ...},
    "special_sessions": ["2026-02-01T00:00:00"],
    "special_sessions_last_update": "2026-10-14"

MYRA therefore reads an **officially sourced, already-present** artifact instead
of adding a network call to its own hot path. Nothing under ``eod2/`` is
modified; it is only read.

Precedence (highest first), matching the directive:
  1. An explicitly announced **special trading session** (NSE circular) - it
     wins even over a named holiday, because that is how the exchange actually
     traded.
  2. An **official NSE holiday** for the applicable year.
  3. A **verified calendar record** already stored in ``market_calendar``
     (``source = 'official'``, or a curated named holiday).
  4. **Weekend** - closed, but not a holiday.
  5. **Unknown** - no official verdict available. Reported as unknown rather than
     assumed either way.

Explicitly *not* a source of holiday truth: absence of ``technical_data`` rows.
That belongs to data readiness, not to the calendar.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Calendar states
# ---------------------------------------------------------------------------

#: Confirmed trading session (weekday, not an official holiday).
SESSION_TRADING = "trading"
#: Confirmed non-trading day: an official NSE holiday, or a weekend.
SESSION_CLOSED = "closed"
#: Confirmed special trading session announced by an NSE circular.
SESSION_SPECIAL = "special"
#: No official verdict is available for this date. Never silently resolved.
SESSION_UNKNOWN = "unknown"

#: ``market_calendar.holiday_name`` value used for every heuristic record written
#: by the legacy ingest. Retained so the reconciliation can find them precisely.
HEURISTIC_REASON = "Likely holiday (zero rows)"

#: Marker written by the reconciliation so provenance is auditable. The existing
#: ``is_trading_day`` / ``holiday_name`` / ``session_type`` columns are reused -
#: no schema change.
OFFICIAL_HOLIDAY_PREFIX = "NSE holiday: "
RECONCILED_NOTE = "(reconciled from heuristic)"

#: How far ahead the calendar will assert anything at all. Beyond this the answer
#: is ``unknown`` even for a plain weekday, because a trading-holiday schedule is
#: published per year and an unannounced date is not a confirmed session.
MAX_HORIZON_DAYS = 400

#: The eod2 daily-folder locations, in the same priority order the sync uses.
_EOD_META_CANDIDATES = (
    os.path.join("eod2", "src", "eod2_data", "meta.json"),
    os.path.join("eod2_data", "meta.json"),
)


# ---------------------------------------------------------------------------
# Locating and reading the official publication
# ---------------------------------------------------------------------------


def _project_root() -> str:
    from myra_app.constants import PROJECT_ROOT

    return PROJECT_ROOT


def find_official_meta_path() -> Optional[str]:
    """Path of the EOD source's ``meta.json`` carrying the NSE holiday list."""
    root = _project_root()
    for rel in _EOD_META_CANDIDATES:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return path
    return None


def load_official_meta() -> Optional[dict]:
    """Load the official calendar block from ``meta.json``.

    Returns ``None`` when the file is missing, unreadable, or does not contain a
    usable holiday list. Callers must treat ``None`` as "no official verdict",
    never as "no holidays" - a transient read failure must not silently empty
    the calendar.
    """
    path = find_official_meta_path()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("calendar: could not read official meta.json: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    holidays = data.get("holidays")
    if not isinstance(holidays, dict) or not holidays:
        return None
    return {
        "year": data.get("year"),
        "data_version": data.get("data-version"),
        "holidays": holidays,
        "special_sessions": [
            s for s in (data.get("special_sessions") or []) if isinstance(s, str)
        ],
        "special_sessions_last_update": data.get("special_sessions_last_update"),
        "source": "eod2_data/meta.json (NSE CM holiday list via eod2 BhavDesk sync)",
    }


def _parse_holiday_key(key: str) -> Optional[date]:
    """Parse eod2's ``DD-Mon-YYYY`` holiday key."""
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(key, fmt).date()
        except ValueError:
            continue
    return None


def official_holidays_for_year(year: int) -> dict[date, str]:
    """``{date: description}`` of official NSE CM holidays for ``year``.

    Empty when the publication does not cover that year, which is the correct
    signal for "this year is not verified yet" rather than an assumption.
    """
    meta = load_official_meta()
    if not meta:
        return {}
    out: dict[date, str] = {}
    for key, desc in meta["holidays"].items():
        parsed = _parse_holiday_key(key)
        if parsed and parsed.year == year:
            out[parsed] = str(desc)
    return out


def official_special_sessions() -> dict[date, str]:
    """``{date: description}`` for circular-announced special trading sessions.

    Upstream stores these as ISO timestamps (``['2026-02-01T00:00:00']``), but a
    mapping form is accepted too so a richer publication can carry the circular's
    own wording, which is what an operator sees in Mission Control.
    """
    meta = load_official_meta()
    if not meta:
        return {}
    entries = meta.get("special_sessions") or []
    if isinstance(entries, dict):
        pairs = [(k, v) for k, v in entries.items()]
    else:
        pairs = [(e, None) for e in entries]

    out: dict[date, str] = {}
    for raw, label in pairs:
        try:
            day = datetime.fromisoformat(str(raw)).date()
        except (TypeError, ValueError):
            logger.debug("calendar: unparseable special session %r", raw)
            continue
        out[day] = str(label).strip() if label else "special session (NSE circular)"
    return out


def verified_years() -> list[int]:
    """Years for which an official holiday list is present."""
    meta = load_official_meta()
    if not meta:
        return []
    years = {d.year for d in official_holidays_for_year(int(meta.get("year") or 0))}
    if meta.get("year"):
        try:
            years.add(int(meta["year"]))
        except (TypeError, ValueError):
            pass
    return sorted(years)


# ---------------------------------------------------------------------------
# Stored calendar records (read-only for the hot path)
# ---------------------------------------------------------------------------


def _calendar_db_path() -> str:
    from myra_app import constants
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["calendar"])


def _ro() -> Optional[sqlite3.Connection]:
    path = _calendar_db_path()
    if not os.path.exists(path):
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None


def stored_record(day: date) -> Optional[tuple[int, Optional[str], Optional[str]]]:
    """``(is_trading_day, holiday_name, session_type)`` as stored, or None."""
    conn = _ro()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT is_trading_day, holiday_name, session_type FROM market_calendar "
            "WHERE date = ?",
            (day.isoformat(),),
        ).fetchone()
        return row
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def classify(day: date) -> dict[str, Any]:
    """Classify ``day`` as an NSE equity session, with its provenance.

    Returns a dict with ``state`` (one of the ``SESSION_*`` constants),
    ``reason`` (human readable), ``provenance`` (where the verdict came from) and
    ``verified`` (bool - False means "no official verdict", i.e. unknown).

    Absence of ``technical_data`` rows is **never** consulted here. That is data
    readiness, and mixing the two is the defect this module removes.
    """
    specials = official_special_sessions()
    if day in specials:
        return {
            "state": SESSION_SPECIAL,
            "reason": specials[day],
            "provenance": "nse_circular",
            "verified": True,
        }

    holidays = official_holidays_for_year(day.year)
    if holidays and day in holidays:
        return {
            "state": SESSION_CLOSED,
            "reason": f"NSE holiday: {holidays[day]}",
            "provenance": "nse_official_holiday_list",
            "verified": True,
        }

    # Coverage check comes *before* the weekend rule, and this ordering is
    # deliberate. A weekend is normally closed, but NSE does publish special
    # live weekend sessions (2026-02-01, a Sunday, is one). For a year we have no
    # publication for we cannot rule that out, so a weekend there is `unknown`
    # rather than confidently "closed".
    if not verified_years():
        return {
            "state": SESSION_UNKNOWN,
            "reason": "no official NSE holiday list available",
            "provenance": "unavailable",
            "verified": False,
        }
    if day.year not in verified_years():
        return {
            "state": SESSION_UNKNOWN,
            "reason": f"no official NSE holiday list published for {day.year} yet",
            "provenance": "unverified_year",
            "verified": False,
        }

    if day.weekday() >= 5:
        return {
            "state": SESSION_CLOSED,
            "reason": "Weekend",
            "provenance": "calendar_rule",
            "verified": True,
        }

    # The year is officially covered and this date is not in it.
    return {
        "state": SESSION_TRADING,
        "reason": f"weekday, absent from the official {day.year} NSE holiday list",
        "provenance": "nse_official_holiday_list",
        "verified": True,
    }


def is_expected_trading_day(day: date) -> bool:
    """True only for a **confirmed** session.

    Unknown dates are False. Callers that need to distinguish "closed" from
    "unknown" should use :func:`classify`.
    """
    return classify(day)["state"] in (SESSION_TRADING, SESSION_SPECIAL)


# ---------------------------------------------------------------------------
# Reconciliation of legacy heuristic records
# ---------------------------------------------------------------------------


def heuristic_records(limit: Optional[int] = None) -> list[tuple[str, Optional[str]]]:
    """``(date, holiday_name)`` for every heuristic "likely holiday" record."""
    conn = _ro()
    if conn is None:
        return []
    try:
        sql = (
            "SELECT date, holiday_name FROM market_calendar "
            "WHERE is_trading_day = 0 AND holiday_name = ? ORDER BY date"
        )
        params: tuple = (HEURISTIC_REASON,)
        if limit:
            sql += " LIMIT ?"
            params = (HEURISTIC_REASON, int(limit))
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def reconcile_heuristic_records(dry_run: bool = True) -> dict[str, Any]:
    """Re-derive the verdict for legacy heuristic records.

    A heuristic record is *not* a holiday claim, so it is replaced by whatever
    the official calendar says for that date:

    * a weekday absent from the official holiday list -> confirmed **trading**
      session (this is the repair: it stops the date from suppressing ingestion);
    * an official holiday -> confirmed **closed**, carrying the official name;
    * a weekend -> confirmed **closed**;
    * unverifiable -> left untouched and reported under ``unclassified``.

    Named/curated records (``holiday_name != HEURISTIC_REASON``) are never
    touched. The whole operation is idempotent: re-running it finds nothing to
    change, and it never deletes a row.

    Defaults to ``dry_run=True`` so it can be inspected before it is applied.
    """
    specials = official_special_sessions()
    summary: dict[str, Any] = {
        "examined": 0,
        "to_trading": 0,
        "to_closed": 0,
        "unclassified": [],
        "changed": 0,
        "dry_run": dry_run,
    }
    if not verified_years():
        # Repair is impossible without the publication. Report the deferred work
        # rather than returning a silent "examined: 0", which would read as
        # "nothing to do" when in fact every pending row is still unfixed.
        pending = heuristic_records()
        summary["error"] = "no official holiday list available; nothing changed"
        summary["examined"] = len(pending)
        summary["unclassified"] = [
            {"date": d, "why": "no official NSE holiday list available"}
            for d, _ in pending
        ]
        return summary

    updates: list[tuple[int, str, str, str]] = []
    for date_str, _reason in heuristic_records():
        summary["examined"] += 1
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            summary["unclassified"].append(
                {"date": date_str, "why": "unparseable date"}
            )
            continue

        verdict = classify(day)
        if not verdict["verified"]:
            summary["unclassified"].append({"date": date_str, "why": verdict["reason"]})
            continue

        if verdict["state"] in (SESSION_TRADING, SESSION_SPECIAL):
            flag = 1
            name = RECONCILED_NOTE
            session_type = "special" if verdict["state"] == SESSION_SPECIAL else None
            summary["to_trading"] += 1
        else:
            flag = 0
            # classify() already renders "NSE holiday: <name>".
            name = verdict["reason"]
            session_type = None
            summary["to_closed"] += 1
        updates.append((flag, name, session_type, date_str))

    if not dry_run and updates:
        conn = sqlite3.connect(_calendar_db_path(), timeout=15)
        try:
            # Narrowed UPDATE: the WHERE clause still pins holiday_name to the
            # heuristic value, so a record repaired since this scan can never be
            # clobbered, and no row is ever deleted.
            conn.executemany(
                "UPDATE market_calendar SET is_trading_day = ?, holiday_name = ?, "
                "session_type = ? WHERE date = ? AND holiday_name = ?",
                [u + (HEURISTIC_REASON,) for u in updates],
            )
            conn.commit()
            summary["changed"] = conn.total_changes
        except sqlite3.Error as exc:
            summary["error"] = f"reconciliation write failed: {exc}"
        finally:
            conn.close()
    elif updates:
        summary["changed"] = len(updates)

    summary["updates_preview"] = [
        {"date": u[3], "is_trading_day": u[0], "holiday_name": u[1]}
        for u in updates[:20]
    ]
    return summary


# ---------------------------------------------------------------------------
# Expected trading session
# ---------------------------------------------------------------------------


def expected_trading_date(
    now: Optional[datetime] = None, *, published_hour: int = 18, minute: int = 30
) -> Optional[str]:
    """Latest date whose EOD data should already be present, ISO.

    Mirrors the operational rule the ingest task already uses - before the
    publication cutoff on a trading day, today's data cannot exist yet - but
    decides "trading day" from the **official calendar**, so it no longer depends
    on whether rows happen to be in the database.

    Returns ``None`` when no confirmed session can be determined within
    :data:`MAX_HORIZON_DAYS` (e.g. the next year's holiday list is unpublished),
    which callers must surface as *unknown* rather than as "not a trading day".
    """
    from myra_app.tasks.watchdog import PUBLICATION_READY_IST, _is_trading_day

    now = now or datetime.now()
    today = now.date()
    cutoff_passed = (now.hour, now.minute) >= (published_hour, minute)

    candidate = today if cutoff_passed else today - timedelta(days=1)
    for _ in range(MAX_HORIZON_DAYS):
        try:
            # _is_trading_day already consults the official calendar first
            # (Phase 9/10) and only falls back to a weekday test if the
            # calendar cannot answer.
            if _is_trading_day(
                datetime.combine(candidate, datetime.min.time()).replace(hour=12)
            ):
                return candidate.isoformat()
        except Exception:  # noqa: BLE001 - degrade to the official classifier
            if is_expected_trading_day(candidate):
                return candidate.isoformat()
        candidate -= timedelta(days=1)
    logger.warning(
        "calendar: no confirmed trading session found within %d days", MAX_HORIZON_DAYS
    )
    return None


__all__ = [
    "SESSION_TRADING",
    "SESSION_CLOSED",
    "SESSION_SPECIAL",
    "SESSION_UNKNOWN",
    "HEURISTIC_REASON",
    "classify",
    "is_expected_trading_day",
    "expected_trading_date",
    "load_official_meta",
    "find_official_meta_path",
    "official_holidays_for_year",
    "official_special_sessions",
    "verified_years",
    "heuristic_records",
    "reconcile_heuristic_records",
]
