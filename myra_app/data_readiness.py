"""Data-readiness assessment for Mission Control (Phase 9).

Why this module exists
----------------------
"Can I trust this database enough to run a screening session?" had no truthful
answer in the product. The pieces existed but were never combined, and the one
place that answered a freshness question internally
(``tasks/watchdog._expected_trading_date``) reported to a *task*, not to a
human, and could not distinguish "the market has not closed yet" from "the file
never arrived" from "ingestion failed".

The design rule enforced here is the one that makes the output useful: **a
maximum date is not completeness.** Each date concept is measured and reported
separately, and the overall state is derived from them:

``latest_technical_date``
    ``MAX(technical_data.date)`` — the newest *market* date represented in
    stored observations. A real market date, not a file mtime and not an
    ingestion timestamp.
``expected_technical_date``
    The newest trading day whose EOD data *should* already be present, derived
    from the shared oracle ``tasks.watchdog._expected_trading_date`` (weekends,
    NSE holidays, the 18:30 IST publication cutoff, and the database-truth
    override for the calendar's ``Likely holiday (zero rows)`` heuristic).
``coverage``
    ``symbols_at_expected / symbols_at_latest`` against the established
    universe, plus the universe's own size. A database can have a recent
    ``MAX(date)`` while a large share of symbols has no row for that date.
``source_state``
    The upstream EOD folder: newest date present, how many files carry it, and
    whether files are still being written (``eod2_sync.assess_source_readiness``).
``fundamentals``
    Reporting period (``fundamentals.date`` / ``quarterly_results.period_end``)
    and retrieval time (``last_updated``) **kept apart**, because they mean
    different things and the product previously had no way to say which was
    which.

Explicitly *not* claimed
------------------------
* A fundamental's ``last_updated`` is a **retrieval** timestamp, never a
  publication date.
* ``quarterly_results.report_date`` is a period label ("Sep 2026"), not an ISO
  date; the ISO ``period_end`` is preferred when present.
* Coverage is only reported against a universe the pipeline actually
  establishes. When the universe cannot be established the field is ``None``
  and the state degrades to ``unknown`` rather than assuming completeness.
* No cached "ready" is ever served after a relevant failure: the assessment is
  cheap (every query below is index-backed and measured in milliseconds), so it
  is computed fresh and never cached across an ingestion outcome.

Cost (measured on the live 8.0 M-row database, see docs/PHASE9_*.md)::

    MAX(date)                                ~0.2 ms
    COUNT(DISTINCT symbol) at one date       ~2 ms
    symbols present on a set of dates       ~3 ms
    fundamentals date/retrieval aggregates   ~5 ms

All access is read-only (``mode=ro``) so an assessment can never mutate a
database, which keeps the Phase 7/8 isolation guarantees intact.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Optional

# ``screening_modes`` imports nothing from this package at module scope, so this
# is a leaf dependency and cannot form a cycle.
from myra_app.screening_modes import MODE_BLOCKED, MODE_DEGRADED

logger = logging.getLogger(__name__)

# ── Operational constants ────────────────────────────────────────────────────

#: How many distinct recent dates coverage is measured over. A single date is
#: noisy (a late-arriving listing legitimately has no row on the newest day); a
#: short window distinguishes that from a genuine universe-wide gap without
#: scanning history.
COVERAGE_WINDOW_SESSIONS = 3

#: A session is considered *universe-complete* at this fraction of the
#: established universe. Calibrated against the live data: a normal session
#: covers ~3,200 of 3,423 tracked symbols (93 %); the remainder are delisted,
#: suspended, SME-only and newly listed names that legitimately have no row.
COVERAGE_COMPLETE_FRACTION = 0.80

#: Below this the newest session is treated as partially ingested. Below
#: ``COVERAGE_PARTIAL_FRACTION`` it is treated as a failed/incomplete session
#: rather than a merely uneven one.
COVERAGE_PARTIAL_FRACTION = 0.50

#: Publication cutoff, re-exported so callers do not re-derive it.
PUBLICATION_READY_IST = "18:30"

#: Upper bound on how long a memoised source assessment may be reused. The
#: validity token (folder + meta.json mtime/size) already invalidates on any
#: file change; this is a backstop so a change that does not move those
#: timestamps cannot pin a stale verdict indefinitely.
SOURCE_CACHE_TTL_SECONDS = 60.0

#: folder path -> {"token", "at", "value"}
_SOURCE_CACHE: dict[str, dict[str, Any]] = {}


# ── Readiness states ─────────────────────────────────────────────────────────
#: The vocabulary the UI renders. ``unknown`` is a first-class outcome: it means
#: the evidence available was insufficient, and it is preferred over a false
#: assurance.
STATE_READY = "ready"
STATE_WAITING = "waiting_for_eod"
STATE_PARTIAL = "partial"
STATE_STALE = "stale"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"
#: The database is present and current, but the *derived* artifacts the
#: screening path depends on are absent, unusable or stale, so no scan can run.
#: Deliberately separate from ``failed`` (an ingestion failure) and from
#: ``ready``: holding current data is not the same capability as producing a
#: screener result, and only this state can say so.
STATE_BLOCKED = "blocked"

STATE_LABELS = {
    STATE_READY: "Ready",
    STATE_WAITING: "Waiting for EOD files",
    STATE_PARTIAL: "Partial data",
    STATE_STALE: "Stale data",
    STATE_FAILED: "Failed ingestion",
    STATE_UNKNOWN: "Unknown",
    STATE_BLOCKED: "Screening blocked",
}


def _ro(path: str) -> Optional[sqlite3.Connection]:
    """Open a database read-only, or return None if unavailable."""
    if not path or not os.path.exists(path):
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        logger.debug("readiness: cannot open %s (%s)", path, exc)
        return None


def _db(key: str, filename: str) -> str:
    """Resolve a sidecar path through ``constants.DB_DIR`` + the canonical DB_MAP.

    ``DB_DIR`` is read from the module **at call time**, not through the
    import-time binding at the top of this file. A test that redirects
    ``constants.DB_DIR`` (or a caller that repoints it) must never be able to
    leave this module resolving - and therefore reading or writing - the live
    sidecar. This is the same hazard Phase 8 hit in ``eod2_sync``.
    """
    from myra_app import constants
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP.get(key, filename))


# ── Individual measurements ──────────────────────────────────────────────────


def technical_facts() -> dict[str, Any]:
    """Latest stored market date plus coverage on the recent sessions.

    Returns ``latest_date``, ``expected_date``, ``sessions`` (newest first) and
    per-session coverage counts. Every query is index-backed.
    """
    facts: dict[str, Any] = {
        "latest_date": None,
        "expected_date": None,
        "sessions": [],
        "coverage": None,
        "universe": None,
        "error": None,
    }

    expected = _expected_date()
    facts["expected_date"] = expected

    conn = _ro(_db("technical", "myra_technical.db"))
    if conn is None:
        facts["error"] = "technical database unavailable"
        return facts
    try:
        row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
        facts["latest_date"] = row[0] if row and row[0] else None

        sessions = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM technical_data "
                "ORDER BY date DESC LIMIT ?",
                (COVERAGE_WINDOW_SESSIONS,),
            ).fetchall()
        ]
        facts["sessions"] = sessions
        if sessions:
            placeholders = ",".join("?" * len(sessions))
            counts = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT date, COUNT(DISTINCT symbol) FROM technical_data "
                    f"WHERE date IN ({placeholders}) GROUP BY date",
                    sessions,
                ).fetchall()
            }
            facts["session_symbol_counts"] = {s: counts.get(s, 0) for s in sessions}
            # Universe = the largest symbol set seen over the window. A symbol
            # trading yesterday but not today (halted/delisted) is still part of
            # the universe, so the max under-count is never used as the
            # denominator.
            facts["universe"] = max(counts.values()) if counts else None
    except sqlite3.Error as exc:
        facts["error"] = f"technical query failed: {exc}"
        logger.debug("readiness technical query failed", exc_info=True)
    finally:
        conn.close()
    return facts


def _expected_date(now: Optional[datetime] = None) -> Optional[str]:
    """ISO expected trading date, from the shared watchdog oracle.

    Reused rather than reimplemented: it already encodes weekends, NSE
    holidays, the 18:30 IST publication cutoff and the database-truth override
    for the calendar's "Likely holiday (zero rows)" heuristic. A second,
    subtly different implementation is exactly the failure mode this module
    exists to prevent.
    """
    try:
        from myra_app.tasks.watchdog import _expected_trading_date

        return _expected_trading_date(now).isoformat()
    except Exception as exc:  # noqa: BLE001 - an unknown answer beats a wrong one
        logger.debug("readiness: expected-date oracle failed (%s)", exc)
        return None


def _coverage_state(
    latest: Optional[str], expected: Optional[str], facts: dict
) -> dict:
    """Derive coverage + state from the technical facts and the source state."""
    counts: dict[str, int] = facts.get("session_symbol_counts") or {}
    universe = facts.get("universe")
    latest = facts.get("latest_date")

    coverage: dict[str, Any] = {
        "symbols_at_expected": None,
        "symbols_at_latest": None,
        "universe": universe,
        "expected_session_complete": None,
    }
    if expected and counts:
        coverage["symbols_at_expected"] = counts.get(expected)
    if latest and counts:
        coverage["symbols_at_latest"] = counts.get(latest)

    # Completeness is only claimed when a denominator exists AND the expected
    # session is actually represented. Otherwise the honest answer is unknown.
    if coverage["symbols_at_expected"] and universe:
        coverage["expected_session_complete"] = (
            coverage["symbols_at_expected"] >= universe * COVERAGE_COMPLETE_FRACTION
        )
    elif latest and universe and counts.get(latest):
        coverage["expected_session_complete"] = (
            counts[latest] >= universe * COVERAGE_COMPLETE_FRACTION
        )
    return coverage


def fundamental_facts() -> dict[str, Any]:
    """Fundamental reporting period and retrieval time, kept separate.

    ``reporting_date``   - ``MAX(period_end)`` of ``quarterly_results`` (the
                           period the figures describe), falling back to
                           ``MAX(fundamentals.date)``.
    ``retrieval_date``   - ``MAX(fundamentals.last_updated)`` (when *we* last
                           fetched). This is **not** a publication date.
    ``coverage``         - rows with at least one core metric, over total rows.
    """
    out: dict[str, Any] = {
        "reporting_date": None,
        "reporting_date_source": None,
        "retrieval_date": None,
        "symbols": None,
        "with_core_metrics": None,
        "error": None,
    }
    conn = _ro(_db("valuation", "myra_valuation.db"))
    if conn is None:
        out["error"] = "valuation database unavailable"
        out["freshness"] = _fundamental_freshness_block(None, None)
        return out
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "quarterly_results" in tables:
            row = conn.execute(
                "SELECT MAX(period_end) FROM quarterly_results WHERE period_end IS NOT NULL"
            ).fetchone()
            if row and row[0]:
                out["reporting_date"] = str(row[0])[:10]
                out["reporting_date_source"] = "quarterly_results.period_end"
        if out["reporting_date"] is None and "fundamentals" in tables:
            row = conn.execute(
                "SELECT MAX(date) FROM fundamentals WHERE date IS NOT NULL"
            ).fetchone()
            if row and row[0]:
                out["reporting_date"] = str(row[0])[:10]
                out["reporting_date_source"] = "fundamentals.date"

        if "fundamentals" in tables:
            row = conn.execute("SELECT MAX(last_updated) FROM fundamentals").fetchone()
            out["retrieval_date"] = str(row[0])[:10] if row and row[0] else None
            total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
            out["symbols"] = total
            out["with_core_metrics"] = conn.execute(
                "SELECT COUNT(*) FROM fundamentals WHERE "
                "(market_cap IS NOT NULL AND market_cap > 0) OR roe IS NOT NULL"
            ).fetchone()[0]
    except sqlite3.Error as exc:
        out["error"] = f"valuation query failed: {exc}"
        logger.debug("readiness fundamentals query failed", exc_info=True)
    finally:
        conn.close()
    out["freshness"] = _fundamental_freshness_block(
        out.get("reporting_date"), out.get("retrieval_date")
    )
    return out


def _fundamental_freshness_block(
    reporting_date: Optional[str], retrieval_date: Optional[str]
) -> dict[str, Any]:
    """Attach Phase 11 freshness + provenance to the fundamentals block.

    Reporting-only and bounded: no ranking, scoring or screening behaviour is
    touched, and nothing here converts a missing fundamental to zero.
    """
    from myra_app import fundamental_freshness as ff

    return {
        "financials": ff.assess_row(
            reporting_date, retrieval_date, cadence="quarterly"
        ),
        "provenance": {
            name: {
                "status": meta["status"],
                "producer": meta["producer"],
                "units": meta["units"],
                "caveat": meta.get("caveat"),
            }
            for name, meta in ff.PROVENANCE.items()
        },
    }


def source_facts() -> dict[str, Any]:
    """Upstream EOD folder readiness, reusing the Phase 8 implementation.

    The Phase 8 ``assess_source_readiness`` already answers "is the folder a
    complete, settled session?" from the files and ``meta.json``. It is reused
    verbatim (not reimplemented) so Mission Control and ingestion can never
    disagree about whether the source is ready.

    **Cost control.** That assessment reads the tail of every per-symbol CSV, so
    it costs ~3 s on a 3,770-file folder. Doing that on every status refresh
    would burn a core for nothing, so the result is memoised against a cheap
    validity token: the folder's own mtime plus ``meta.json``'s mtime and size.

    This is *not* a readiness cache in the sense the directive warns about: it
    never serves a stale "ready". The token changes whenever the operator adds,
    replaces or re-copies any file (directory mtime), whenever ``meta.json`` is
    rewritten (its mtime/size), and after the bounded TTL. An incomplete or
    failing source is therefore re-read on the next tick, and after a successful
    ingestion the token is invalidated explicitly by
    :func:`invalidate_source_cache`.
    """
    try:
        from myra_app.eod2_sync import _find_eod2_daily, assess_source_readiness

        daily = _find_eod2_daily()
    except Exception as exc:  # noqa: BLE001
        logger.debug("readiness: eod2 source lookup failed (%s)", exc)
        daily = None
    if not daily:
        return {
            "available": False,
            "reason": "eod2 source folder not found",
            "ready": None,
            "last_date": None,
            "complete": None,
            "total_files": None,
        }

    token = _source_token(daily)
    now = time.monotonic()
    cached = _SOURCE_CACHE.get(daily)
    if (
        cached
        and cached["token"] == token
        and (now - cached["at"]) < SOURCE_CACHE_TTL_SECONDS
    ):
        return cached["value"]

    try:
        report = assess_source_readiness(daily, None)
    except Exception as exc:  # noqa: BLE001
        return {
            "available": True,
            "reason": f"source assessment failed: {exc}",
            "ready": None,
            "last_date": None,
            "complete": None,
            "total_files": None,
        }
    value = {
        "available": True,
        "ready": report.get("ready"),
        "reason": report.get("reason"),
        "last_date": report.get("last_date"),
        "complete": report.get("complete"),
        "total_files": report.get("total_files"),
        "unstable_files": report.get("unstable_files"),
        "delivery_pending": report.get("delivery_pending") or [],
    }
    _SOURCE_CACHE[daily] = {"token": token, "at": now, "value": value}
    return value


def _source_token(daily: str) -> tuple:
    """Cheap validity token for the source folder.

    Includes the folder mtime, ``meta.json``'s mtime/size, **and** the newest
    file mtime in the folder. The last part matters: replacing the *contents* of
    an existing file (which is exactly what a re-copy of a session does) does
    not move the directory mtime, so a token built only from the folder and
    meta.json would keep serving a pre-copy verdict. Scanning for the newest
    entry is a directory listing plus a stat per file - milliseconds for a few
    thousand files, and still far cheaper than re-reading every file's tail.
    """
    parts: list[Any] = []
    try:
        parts.append(int(os.stat(daily).st_mtime))
    except OSError:
        parts.append(None)
    newest = 0.0
    try:
        with os.scandir(daily) as entries:
            for entry in entries:
                try:
                    m = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if m > newest:
                    newest = m
    except OSError:
        newest = 0.0
    parts.append(round(newest, 3))
    meta = os.path.join(os.path.dirname(daily), "meta.json")
    try:
        st = os.stat(meta)
        parts.extend([int(st.st_mtime), st.st_size])
    except OSError:
        parts.extend([None, None])
    return tuple(parts)


def invalidate_source_cache() -> None:
    """Drop the memoised source assessment.

    Called after a successful ingestion so the very next status read reflects
    the new folder state rather than a value computed before the ingest.
    """
    _SOURCE_CACHE.clear()


def ingestion_facts() -> dict[str, Any]:
    """Last recorded ingestion outcomes from the existing ``sync_log``."""
    out: dict[str, Any] = {
        "last_success": None,
        "last_attempt": None,
        "last_status": None,
    }
    conn = _ro(_db("meta", "myra_metadata.db"))
    if conn is None:
        return out
    try:
        row = conn.execute(
            "SELECT last_run, last_status FROM sync_log WHERE task_name = 'daily_ingest'"
        ).fetchone()
        if row:
            out["last_attempt"] = row[0]
            out["last_status"] = row[1]
            if (row[1] or "").lower() in ("success", "completed"):
                out["last_success"] = row[0]
    except sqlite3.Error as exc:
        logger.debug("readiness: sync_log query failed (%s)", exc)
    finally:
        conn.close()
    return out


# ── Orchestration ────────────────────────────────────────────────────────────


def recovery_facts() -> dict[str, Any]:
    """Last automatic EOD recovery outcome (Phase 10).

    Read from the ``metadata`` key the recovery policy writes. This is a cheap
    single-row read and adds nothing to the assessment's query budget.
    """
    out: dict[str, Any] = {
        "reason": None,
        "detail": None,
        "at": None,
        "fingerprint": None,
    }
    try:
        from myra_app.eod_recovery import last_outcome

        data = last_outcome() or {}
    except Exception as exc:  # noqa: BLE001 - status is advisory
        logger.debug("readiness: recovery status unavailable (%s)", exc)
        return out
    out.update(
        {
            "reason": data.get("reason"),
            "detail": data.get("detail"),
            "at": data.get("at"),
            "fingerprint": data.get("fingerprint"),
        }
    )
    return out


def _active_universe_size() -> Optional[int]:
    """How many symbols the active universe holds, or ``None`` if unknown.

    The indicator lake is keyed by the active universe, so this is the correct
    denominator for lake coverage. Read-only and index-free, but tiny (one
    ``COUNT(*)``).
    """
    conn = _ro(_db("meta", "myra_metadata.db"))
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM symbols_master WHERE in_active_universe = 1"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except sqlite3.Error as exc:
        logger.debug("readiness: active universe count failed (%s)", exc)
        return None
    finally:
        conn.close()


def _column_population(date: Optional[str]) -> Optional[dict]:
    """Share of one session's rows that carry each TriggerScanner column.

    One aggregate over one date (served by ``idx_tech_date``), so it reads about
    one session (~3,200 rows) rather than the table. ``None`` means the session
    could not be measured, which callers must report as "unknown" - never as
    "populated". The column list is a code constant, not caller input.
    """
    if not date:
        return None
    from myra_app.screening_modes import (
        TRIGGER_CONTRIBUTING_COLUMNS,
        TRIGGER_REQUIRED_COLUMNS,
    )

    columns = tuple(TRIGGER_REQUIRED_COLUMNS) + tuple(TRIGGER_CONTRIBUTING_COLUMNS)
    conn = _ro(_db("technical", "myra_technical.db"))
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM technical_data WHERE date = ?", (date,)
        ).fetchone()
        total = int(row[0]) if row and row[0] else 0
        if not total:
            return None
        expr = ", ".join(
            f"SUM(CASE WHEN {name} IS NOT NULL THEN 1 ELSE 0 END)" for name in columns
        )
        values = conn.execute(
            f"SELECT {expr} FROM technical_data WHERE date = ?", (date,)
        ).fetchone()
        return {
            name: round(float(value or 0) / float(total), 4)
            for name, value in zip(columns, values)
        }
    except sqlite3.Error as exc:
        logger.debug("readiness: column population failed (%s)", exc)
        return None
    finally:
        conn.close()


def screening_facts(technical: Optional[dict] = None) -> dict[str, Any]:
    """Whether MYRA can actually *screen*, given its derived artifacts.

    Freshness and screening capability are different facts. ``technical_facts``
    answers "the market database holds the expected session"; this answers
    "the artifacts the screening path reads were built from it, and are
    usable". The second can be false while the first is true — that is the
    precise state MYRA was in, reporting ``ready`` with an empty indicator lake.

    Cheap and read-only: it lists the lake directory, reads the ``reference_date``
    it is given (never a new query of its own), opens at most a handful of
    artifacts, and issues one ``COUNT(*)``. Nothing is created or written — a
    probe that materialised the very directory it was checking for would be
    self-defeating.

    ``technical`` is the technical-facts mapping; its ``latest_date`` is the
    reference the lake must be at least as new as.
    """
    out: dict[str, Any] = {
        "state": STATE_UNKNOWN,
        # Defensive default only: every branch below sets both ``state`` and
        # ``reason`` before returning, so this is never what a caller observes.
        "reason": "unexamined",
        "detail": "The indicator lake was not examined.",
        "available": False,
        "lake": None,
        "expected": None,
        "coverage": None,
    }

    try:
        from myra_app.indicator_lake import status as lake_status
    except Exception as exc:  # noqa: BLE001 - never fail a probe over its own import
        out["reason"] = "probe_unavailable"
        out["detail"] = f"Indicator-lake probe unavailable: {exc}"
        return out

    reference = (technical or {}).get("latest_date")
    try:
        lake = lake_status(reference_date=reference)
    except Exception as exc:  # noqa: BLE001 - advisory measurement
        out["reason"] = "probe_failed"
        out["detail"] = f"Indicator-lake probe raised {type(exc).__name__}: {exc}"
        return out

    out["lake"] = lake
    out["available"] = bool(lake.get("present"))

    if lake.get("error"):
        out["reason"] = "unreadable"
        out["detail"] = f"The indicator lake could not be read: {lake['error']}"
        return out

    count = int(lake.get("count") or 0)

    if lake.get("invalid"):
        bad = lake["invalid"][0]
        out["state"] = STATE_BLOCKED
        out["reason"] = "invalid_artifact"
        out["detail"] = (
            f"The indicator lake holds {count} artifacts but {bad['file']} is "
            f"not usable ({bad['reason']}); a rebuild is required."
        )
        return out

    if count == 0:
        out["state"] = STATE_BLOCKED
        out["reason"] = "lake_empty"
        out["detail"] = (
            "The indicator lake (data/indicators/precomputed) is empty, so the "
            "screening path has no precomputed indicators to load. The lake is "
            "built explicitly, not as a side effect of a scan."
        )
        return out

    expected = _active_universe_size()
    out["expected"] = expected
    if expected:
        out["coverage"] = round(count / expected, 4)

    if lake.get("stale"):
        out["state"] = STATE_STALE
        out["reason"] = "stale"
        out["detail"] = (
            f"The indicator lake was last written {lake.get('freshest')}, "
            f"before the newest stored session ({reference}); its rankings "
            "would silently be stale."
        )
        return out

    if out["coverage"] is not None:
        if out["coverage"] < COVERAGE_PARTIAL_FRACTION:
            out["state"] = STATE_BLOCKED
            out["reason"] = "coverage_insufficient"
            out["detail"] = (
                f"Only {count} of {expected} active symbols have precomputed "
                "indicators; most of the universe cannot be screened."
            )
            return out
        if out["coverage"] < COVERAGE_COMPLETE_FRACTION:
            out["state"] = STATE_PARTIAL
            out["reason"] = "coverage_partial"
            out["detail"] = (
                f"{count} of {expected} active symbols have precomputed " "indicators."
            )
            return out

    out["state"] = STATE_READY
    out["reason"] = "ok"
    out["detail"] = f"The indicator lake holds {count} usable artifacts."
    return out


#: Overall states a screening verdict may escalate. A verdict never *downgrades*
#: a stronger signal — a genuine ingestion failure stays ``failed``, and
#: insufficient evidence stays ``unknown``.
_SCREENING_ESCALATABLE = (STATE_READY, STATE_WAITING, STATE_STALE)

#: blocked > stale > partial. Every escalatable state ranks 0.
_SCREENING_RANKING = {STATE_PARTIAL: 1, STATE_STALE: 2, STATE_BLOCKED: 3}


def _apply_screening(
    state: str, explanation: str, screening: Optional[dict]
) -> tuple[str, str]:
    """Escalate ``state`` when screening capability is worse than freshness.

    Escalation only. Data freshness is still the primary axis, but a current
    database with an unusable screening path must not read ``ready`` — that was
    the false assurance this state exists to prevent.
    """
    verdict = (screening or {}).get("state")
    if state not in _SCREENING_ESCALATABLE:
        return state, explanation
    if verdict not in _SCREENING_RANKING:
        return state, explanation
    if _SCREENING_RANKING[verdict] <= _SCREENING_RANKING.get(state, 0):
        return state, explanation
    base = explanation.rstrip().rstrip(".")
    suffix = (screening or {}).get("detail") or ""
    return verdict, f"{base}. {suffix}".strip()


def screening_mode_facts(
    technical: Optional[dict] = None,
    coverage: Optional[dict] = None,
    screening: Optional[dict] = None,
    populations: Optional[dict] = None,
    available_fields: Optional[set] = None,
) -> dict[str, Any]:
    """Per-mode screening capability, plus an honest aggregate (Phase 18).

    ``screening_facts`` answers "is the indicator lake usable?" - which is a
    question about one mode. MYRA has an independent mode, the web/API
    ``TriggerScanner``, that reads ``technical_data`` directly and needs no
    lake at all. Reporting a single verdict over both hid a working scanner
    behind a blocked lake.

    A probe failure here is reported, never raised: an assessment must not
    fail because one of its measurements did.
    """
    from myra_app.screening_modes import evaluate as _evaluate

    if populations is None:
        populations = _column_population((technical or {}).get("latest_date"))
    try:
        return _evaluate(
            technical=technical,
            coverage=coverage,
            screening=screening,
            populations=populations,
            available_fields=available_fields,
        )
    except Exception as exc:  # noqa: BLE001 - never fail an assessment
        logger.debug("readiness: screening-mode evaluation failed (%s)", exc)
        return {
            "overall": {
                "state": "unknown",
                "label": "Screening modes unknown",
                "detail": (
                    "Screening-mode capability could not be evaluated: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "operational_modes": [],
                "unavailable_modes": [],
                "blocked": [],
            },
            "modes": {},
        }


#: Mode-aggregate verdict -> the freshness state it escalates to. ``degraded``
#: means *some* mode still works, so it is ``partial`` (availability is real but
#: qualified), not ``blocked``. ``blocked`` means no mode can run.
_MODE_RANKING = {MODE_DEGRADED: 1, MODE_BLOCKED: 3}


def _apply_mode_capability(
    state: str, explanation: str, modes: Optional[dict]
) -> tuple[str, str]:
    """Escalate ``state`` when no screening mode can serve the operator.

    Escalation only, and deliberately *not* the same mapping as
    :func:`_apply_screening`: a mode that still works keeps the verdict at
    ``partial`` rather than ``blocked``, because "the turbo ranking is
    unavailable" and "nothing can be screened" are different facts. Freshness
    remains the primary axis - a genuine ingestion failure is never downgraded.
    """
    overall = (modes or {}).get("overall") or {}
    verdict = overall.get("state")
    if state not in _SCREENING_ESCALATABLE:
        return state, explanation
    if verdict not in _MODE_RANKING:
        return state, explanation
    target = STATE_PARTIAL if verdict == MODE_DEGRADED else STATE_BLOCKED
    if _SCREENING_RANKING[target] <= _SCREENING_RANKING.get(state, 0):
        return state, explanation
    base = explanation.rstrip().rstrip(".")
    detail = overall.get("detail") or ""
    return target, f"{base}. {detail}".strip()


def assess(now: Optional[datetime] = None) -> dict[str, Any]:
    """Full readiness assessment.

    Returns a dict shaped for direct JSON rendering by Mission Control, with
    every date concept kept distinct and an ``overall`` state plus a plain
    explanation. Computed fresh on every call: every query is index-backed and
    measured in single-digit milliseconds, so correctness never depends on a
    cache that could go stale after a failed ingestion.
    """
    technical = technical_facts()
    coverage = _coverage_state(None, None, technical)
    source = source_facts()
    fundamentals = fundamental_facts()
    ingestion = ingestion_facts()
    recovery = recovery_facts()
    screening = screening_facts(technical)

    latest = technical.get("latest_date")
    expected = technical.get("expected_date")
    # Capability is measured per mode. A built lake and a working
    # TriggerScanner are independent facts; neither stands in for the other, so
    # a working scanner is never hidden behind a blocked lake and a healthy
    # scanner never makes the v2.5 ranking look operational.
    modes = screening_mode_facts(technical, coverage, screening)

    state, explanation = _decide(technical, coverage, source, ingestion)
    # Freshness is not capability: a current database with no usable screening
    # mode must not report ``ready``. Escalation only.
    state, explanation = _apply_mode_capability(state, explanation, modes)

    return {
        "state": state,
        "label": STATE_LABELS.get(state, STATE_LABELS[STATE_UNKNOWN]),
        "explanation": explanation,
        "technical": {
            "latest_date": latest,
            "expected_date": expected,
            "sessions": technical.get("sessions") or [],
            "session_symbol_counts": technical.get("session_symbol_counts") or {},
            "error": technical.get("error"),
        },
        "coverage": coverage,
        "source": source,
        "fundamentals": fundamentals,
        "ingestion": ingestion,
        "recovery": recovery,
        "screening": screening,
        "screening_modes": modes,
        "publication_cutoff_ist": PUBLICATION_READY_IST,
        "generated_at": (now or datetime.now()).isoformat(),
    }


def _decide(
    technical: dict,
    coverage: dict,
    source: dict,
    ingestion: dict,
) -> tuple[str, str]:
    """Derive the overall state and a concrete explanation.

    Order matters: an actual ingestion failure outranks a mere data gap, and
    "the market has not closed yet" outranks everything that would otherwise
    look like staleness.
    """
    if technical.get("error"):
        return STATE_UNKNOWN, f"Could not read technical data: {technical['error']}"

    latest = technical.get("latest_date")
    expected = technical.get("expected_date")
    if not latest:
        return STATE_UNKNOWN, "No market observations are stored."

    if not expected:
        return (
            STATE_UNKNOWN,
            "Expected trading date could not be determined from the calendar.",
        )

    if latest >= expected:
        # At or past the expected session. Completeness is a separate axis.
        if coverage.get("expected_session_complete") is False:
            got = coverage.get("symbols_at_latest")
            uni = coverage.get("universe")
            return (
                STATE_PARTIAL,
                f"Latest session {latest} is stored but only {got} of {uni} "
                "tracked symbols have a row for it.",
            )
        return (
            STATE_READY,
            f"Latest market date {latest} matches the expected session "
            f"({expected}).",
        )

    # Behind the expected session. Distinguish "the file has not arrived" from
    # "the source is incomplete" from "ingestion failed".
    if (ingestion.get("last_status") or "").lower() in ("failed", "error"):
        return (
            STATE_FAILED,
            f"Data is at {latest}, behind the expected {expected}, and the last "
            "ingestion attempt reported a failure.",
        )
    if source.get("available") and source.get("last_date"):
        src_date = source["last_date"]
        if src_date < expected:
            return (
                STATE_WAITING,
                f"Data is at {latest}; the EOD source only holds up to {src_date}, "
                f"so the expected session {expected} has not been published yet.",
            )
        return (
            STATE_PARTIAL,
            f"Data is at {latest} but the EOD source already holds {src_date}; "
            "the remaining sessions have not been ingested.",
        )
    if source.get("available") and source.get("ready") is False:
        return (
            STATE_PARTIAL,
            f"Data is at {latest}, behind the expected {expected}, and the EOD "
            f"source set is incomplete ({source.get('reason')}).",
        )
    return (
        STATE_WAITING,
        f"Data is at {latest}; the expected session {expected} has not been "
        "ingested yet.",
    )


def fingerprint(assessment: dict[str, Any]) -> str:
    """Stable hash of the *decision-bearing* fields of an assessment.

    Used to suppress unchanged updates: the frontend and the status endpoint
    can compare this and skip re-rendering identical readiness. Deliberately
    excludes ``generated_at`` so an unchanged assessment always fingerprints
    identically.
    """
    payload = {
        "state": assessment.get("state"),
        "latest": (assessment.get("technical") or {}).get("latest_date"),
        "expected": (assessment.get("technical") or {}).get("expected_date"),
        "coverage": (assessment.get("coverage") or {}).get("symbols_at_latest"),
        "universe": (assessment.get("coverage") or {}).get("universe"),
        "source": (assessment.get("source") or {}).get("last_date"),
        "fundamentals": (assessment.get("fundamentals") or {}).get("reporting_date"),
        # Phase 10: a recovery starting or changing outcome is a real change and
        # must reach the UI.
        "recovery": (assessment.get("recovery") or {}).get("reason"),
        # Screening capability is decision-bearing: the lake going from blocked
        # to built is exactly the transition an operator must be told about.
        "screening": (assessment.get("screening") or {}).get("reason"),
        # Phase 18: a mode becoming available (or unavailable) is a change the
        # operator must be told about, even when the lake verdict is unchanged.
        "screening_modes": (
            (assessment.get("screening_modes") or {}).get("overall") or {}
        ).get("state"),
    }
    return json.dumps(payload, sort_keys=True)


__all__ = [
    "assess",
    "technical_facts",
    "fundamental_facts",
    "source_facts",
    "ingestion_facts",
    "screening_facts",
    "screening_mode_facts",
    "fingerprint",
    "STATE_READY",
    "STATE_WAITING",
    "STATE_PARTIAL",
    "STATE_STALE",
    "STATE_FAILED",
    "STATE_UNKNOWN",
    "STATE_BLOCKED",
    "STATE_LABELS",
]
