"""Automatic EOD recovery policy (Phase 10, workstream 2).

This module decides *whether* an automatic recovery attempt is warranted and
records the attempt. It deliberately contains **no scheduler, no thread and no
polling of its own**: the caller is the existing 60-second watchdog loop in
:mod:`myra_app.tasks.watchdog`, which already owns when to look. What was missing
was not another driver but three things:

1. a correct expected trading date (Phase 10 WS1 - the official calendar);
2. an explicit statement of *why* an attempt is eligible, so a repeated
   incomplete file set does not restart expensive ingestion every minute;
3. a bound on how often the same input state may be retried, which must reset
   when the input genuinely changes (a late-arriving EOD file).

Eligibility requires **all** of:

* the expected trading date is a **confirmed** session (an official holiday or an
  unknown calendar never triggers ordinary recovery);
* the database is actually behind that session;
* the after-close publication window has opened;
* the source is either already ahead of the database, or simply not yet settled
  - a missing file is not a reason to hammer the importer, but it is not a
  reason to stop looking either;
* no equivalent recovery is already running (the ingest task's own
  ``_INGEST_LOCK`` plus the shared attempt cooldown);
* the per-input-state attempt budget has not been exhausted.

Bounded policy
--------------
Attempts are keyed by an **input fingerprint** - the expected session plus a
digest of the source state (newest source date + how many files carry it). This
gives the two behaviours the directive requires:

* the same incomplete file set does not bypass the cooldown - the fingerprint is
  unchanged, so the shared attempt cooldown applies as before;
* a genuinely new input (a late-arriving file, a new session) produces a new
  fingerprint, so recovery becomes eligible again instead of being permanently
  suppressed by a previous failure.

Two bounds are layered on top, because they answer different questions:

* the **cooldown** (``_attempt_cooldown_elapsed``, ``STALE_RETRY_COOLDOWN_MINUTES``)
  spaces attempts apart and is shared with the ingest task, so the two drivers
  cannot both fire for the same window;
* the **retry budget** (:data:`MAX_ATTEMPTS_PER_INPUT`) is a hard cap on attempts
  against one unchanged input state. A cooldown alone would retry an incomplete
  source every 30 minutes indefinitely; the budget stops that, while a new
  fingerprint starts from zero.

The budget lives in the existing ``metadata`` key/value sidecar under
``eod_auto_recovery_budget:<fingerprint>``, so it is shared across processes and
survives a restart. No new table, no new service.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: sync_log task_name used for the recovery attempt record.
RECOVERY_LABEL = "eod_auto_recovery"

#: sync_log task_name holding the last recovery outcome, for Mission Control.
RECOVERY_STATUS_LABEL = "eod_auto_recovery_status"

#: ``metadata`` key prefix holding the per-input-state retry budget.
BUDGET_LABEL = "eod_auto_recovery_budget"

#: Maximum automatic attempts against one and the same input state.
#:
#: A cooldown only spaces attempts out; it does not stop them. Without a hard
#: bound, a source that stays incomplete for days is re-imported every 30 minutes
#: forever, which is neither bounded nor useful. The budget is keyed by the input
#: fingerprint, so a genuinely new input (a late-arriving EOD file, a new
#: session) starts from zero and is never suppressed by an earlier failure.
MAX_ATTEMPTS_PER_INPUT = 6

#: Reasons an attempt was (or was not) made. Stable strings: they are persisted
#: and rendered in Mission Control.
REASON_RECOVERED = "recovered"
REASON_PARTIAL = "partial_recovery"
REASON_WAITING = "waiting_for_source"
REASON_NO_NEW_DATA = "no_new_data"
REASON_FAILED = "failed"
REASON_SKIPPED_RUNNING = "skipped_already_running"
REASON_SKIPPED_COOLDOWN = "skipped_cooldown"
REASON_SKIPPED_BUDGET = "skipped_retry_budget_exhausted"
REASON_SKIPPED_HOLIDAY = "skipped_official_holiday"
REASON_SKIPPED_UNKNOWN = "skipped_calendar_unknown"
REASON_SKIPPED_READY = "skipped_already_current"
REASON_SKIPPED_WINDOW = "skipped_before_publication_window"

#: Terminal / informational reasons that must not be retried aggressively.
_NO_RETRY_REASONS = frozenset(
    {REASON_SKIPPED_HOLIDAY, REASON_SKIPPED_UNKNOWN, REASON_SKIPPED_READY}
)


def _meta_path() -> str:
    from myra_app import constants
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["meta"])


def input_fingerprint(expected_date: Optional[str], source: dict) -> str:
    """Stable digest of "which session, and what source state do we have?".

    Two identical inputs produce the same fingerprint, so the attempt budget and
    the shared cooldown both apply. A late-arriving file changes
    ``last_date``/``complete`` and therefore produces a new fingerprint, which is
    what makes a fresh attempt eligible.
    """
    payload = "|".join(
        [
            str(expected_date or "none"),
            str(source.get("last_date") or "none"),
            str(source.get("complete") or "none"),
            str(source.get("total_files") or "none"),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Per-input-state retry budget (cross-process, survives restart)
# ---------------------------------------------------------------------------


def _budget_key(fingerprint: str) -> str:
    return f"{BUDGET_LABEL}:{fingerprint}"


def _read_metadata(key: str) -> Optional[str]:
    """Read one metadata value read-only. Returns None if absent/unreadable."""
    import json as _json

    try:
        path = _meta_path()
        if not os.path.exists(path):
            return None
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("recovery: metadata read failed for %s (%s)", key, exc)
        return None


def budget_state(fingerprint: str) -> dict[str, Any]:
    """Attempts recorded against this input state so far."""
    raw = _read_metadata(_budget_key(fingerprint))
    if not raw:
        return {"attempts": 0, "first": None, "last": None}
    try:
        import json as _json

        data = _json.loads(raw)
        return {
            "attempts": int(data.get("attempts", 0)),
            "first": data.get("first"),
            "last": data.get("last"),
        }
    except Exception:  # noqa: BLE001 - a corrupt row is treated as zero
        return {"attempts": 0, "first": None, "last": None}


def budget_allows(fingerprint: str) -> tuple[bool, dict[str, Any]]:
    """True while this input state still has attempts left."""
    state = budget_state(fingerprint)
    return state["attempts"] < MAX_ATTEMPTS_PER_INPUT, state


def note_attempt(fingerprint: str, reason: str = "") -> dict[str, Any]:
    """Consume one unit of budget for this input state.

    Called immediately before an attempt actually starts, so a crash still
    spends the budget rather than allowing a tight retry loop. Keyed by the input
    fingerprint: new input means a new key and therefore a fresh budget.
    """
    import json
    from datetime import datetime

    state = budget_state(fingerprint)
    now_iso = datetime.now().isoformat()
    state["attempts"] = int(state["attempts"]) + 1
    state["last"] = now_iso
    if not state.get("first"):
        state["first"] = now_iso
    state["last_reason"] = reason

    try:
        path = _meta_path()
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (_budget_key(fingerprint), json.dumps(state)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("recovery: could not record attempt budget (%s)", exc)
    return state


def refund_attempt(fingerprint: str) -> dict[str, Any]:
    """Give back one unit of budget for an attempt that never really ran.

    Used when the ingest lock is held by another driver: we were eligible, but
    no work happened on our behalf, so charging for it would slowly exhaust the
    budget through no fault of the source. Never goes below zero.
    """
    import json
    from datetime import datetime

    state = budget_state(fingerprint)
    state["attempts"] = max(0, int(state["attempts"]) - 1)
    state["last"] = datetime.now().isoformat()
    try:
        path = _meta_path()
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (_budget_key(fingerprint), json.dumps(state)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("recovery: could not refund attempt budget (%s)", exc)
    return state


def budget_summary() -> dict[str, Any]:
    """Operator view: how much budget is left for the current input state."""
    from myra_app import data_readiness as dr

    expected = dr._expected_date()
    source = dr.source_facts()
    fp = input_fingerprint(expected, source)
    allowed, state = budget_allows(fp)
    return {
        "fingerprint": fp,
        "attempts": state["attempts"],
        "max_attempts": MAX_ATTEMPTS_PER_INPUT,
        "remaining": max(0, MAX_ATTEMPTS_PER_INPUT - state["attempts"]),
        "exhausted": not allowed,
        "first": state.get("first"),
        "last": state.get("last"),
    }


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def evaluate(
    now=None,
    *,
    expected_date: Optional[str] = None,
    db_latest: Optional[str] = None,
    source: Optional[dict] = None,
    after_publication_window: bool = True,
    cooldown_minutes: int = 30,
) -> dict[str, Any]:
    """Decide whether an automatic recovery attempt is warranted right now.

    Pure decision function - it performs no I/O of its own beyond the two
    existing helpers it calls, so it is straightforward to unit test with fully
    controlled inputs. Returns a dict with ``eligible`` (bool), ``reason`` (a
    stable string) and ``fingerprint``.
    """
    from myra_app import data_readiness as dr
    from myra_app.utils.task_utils import _attempt_cooldown_elapsed, now_ist

    now = now or now_ist()
    source = source if source is not None else dr.source_facts()
    if expected_date is None:
        expected_date = dr._expected_date(now)
    if db_latest is None:
        from myra_app.daily_ingestor import get_db_latest_date

        db_latest = get_db_latest_date()

    fp = input_fingerprint(expected_date, source)

    def _out(eligible: bool, reason: str) -> dict[str, Any]:
        return {
            "eligible": eligible,
            "reason": reason,
            "fingerprint": fp,
            "expected_date": expected_date,
            "db_latest": db_latest,
            "source_last_date": source.get("last_date"),
        }

    # 1. A confirmed session is required. An official holiday (or an unknown
    #    calendar) must not trigger ordinary EOD recovery.
    #
    #    The check goes through the watchdog's shared seam (`_is_trading_day`)
    #    rather than re-classifying, so the watchdog, the ingest task and the
    #    recovery policy can never disagree about what a trading day is - and so
    #    existing tests that already stub that seam keep working.
    if expected_date is None:
        return _out(False, REASON_SKIPPED_UNKNOWN)
    try:
        from datetime import datetime as _dt

        from myra_app.tasks.watchdog import _is_trading_day

        day = _dt.strptime(expected_date, "%Y-%m-%d").date()
        if not _is_trading_day(day):
            # Distinguish a real closure from an unverifiable calendar so the
            # recorded reason is truthful.
            from myra_app import nse_calendar

            verdict = nse_calendar.classify(day)
            reason = (
                REASON_SKIPPED_UNKNOWN
                if not verdict["verified"]
                else REASON_SKIPPED_HOLIDAY
            )
            return _out(False, reason)
    except Exception as exc:  # noqa: BLE001 - never block recovery on a parse slip
        logger.debug("recovery: trading-day check failed (%s)", exc)
        return _out(False, REASON_SKIPPED_UNKNOWN)

    # 2. Already current for the expected session.
    if db_latest is not None and db_latest >= expected_date:
        return _out(False, REASON_SKIPPED_READY)

    # 3. The publication window must have opened — but only when we are actually
    #    waiting for a session the source has not published yet.
    #
    #    This gate exists because "has *today's* EOD landed?" cannot be answered
    #    before ~18:30 IST. It must not, however, delay catch-up of sessions the
    #    source demonstrably already carries: if the newest source date is at or
    #    beyond the expected session, that session is published and waiting for
    #    today's cutoff would suppress recovery for no reason (which is exactly
    #    what left 2026-10-13/14 un-ingested on the morning of 2026-10-15).
    source_has_expected = bool(
        expected_date
        and source.get("last_date")
        and str(source["last_date"]) >= expected_date
    )
    if not after_publication_window and not source_has_expected:
        return _out(False, REASON_SKIPPED_WINDOW)

    # 4. The shared attempt cooldown (written by the ingest task at the *start* of
    #    every attempt, so a crash cannot produce a tight retry loop).
    if not _attempt_cooldown_elapsed("daily_ingest", cooldown_minutes, now):
        return _out(False, REASON_SKIPPED_COOLDOWN)

    # 5. Hard bound on attempts against this exact input state. The cooldown
    #    above only spaces attempts out; this stops them. A new fingerprint (a
    #    late-arriving file, a new session) means a fresh budget, so a previous
    #    failure can never permanently suppress recovery.
    allowed, state = budget_allows(fp)
    if not allowed:
        return _out(False, REASON_SKIPPED_BUDGET)

    # Eligible. A source that has not yet reached the expected date is allowed -
    #    the importer is cheap when there is nothing to do, and the cooldown
    #    bounds how often that can happen.
    return _out(True, "eligible")


# ---------------------------------------------------------------------------
# Outcome recording (cross-process, survives restart)
# ---------------------------------------------------------------------------


def record_outcome(reason: str, detail: str = "", fingerprint: str = "") -> bool:
    """Persist the last recovery outcome. Best-effort; never raises.

    Also publishes the transition through the **existing** PipelineControl event
    bus when one is running, so Mission Control sees a recovery start/finish
    without a new endpoint or a new poll. Nothing is emitted when the reason is
    unchanged (the "already current" / cooldown ticks), so idle monitoring stays
    quiet.
    """
    import json
    from datetime import datetime

    payload = {
        "reason": reason,
        "detail": detail[:400],
        "fingerprint": fingerprint,
        "at": datetime.now().isoformat(),
    }
    try:
        path = _meta_path()
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (RECOVERY_STATUS_LABEL, json.dumps(payload)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - status is advisory
        logger.debug("recovery: could not record outcome (%s)", exc)
        return False
    _publish_event(payload)
    return True


def _publish_event(payload: dict) -> None:
    """Emit a recovery event on the existing SSE bus, only on a real change."""
    try:
        from myra_app.pipeline_control import control

        previous = _last_published_reason.get("reason")
        if payload.get("reason") == previous:
            return  # unchanged: never emit
        _last_published_reason["reason"] = payload.get("reason")
        control._emit(  # noqa: SLF001 - same module family, same event bus
            {
                "type": "eod_recovery",
                "reason": payload.get("reason"),
                "detail": payload.get("detail"),
            }
        )
    except Exception as exc:  # noqa: BLE001 - events are advisory
        logger.debug("recovery: could not publish event (%s)", exc)


#: Last reason handed to the event bus, so an unchanged outcome is not re-emitted
#: on every 60 s watchdog tick.
_last_published_reason: dict[str, str] = {"reason": None}


def last_outcome() -> dict[str, Any]:
    """Last recorded recovery outcome, or an empty dict."""
    import json

    try:
        path = _meta_path()
        if not os.path.exists(path):
            return {}
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (RECOVERY_STATUS_LABEL,)
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return {}
        data = json.loads(row[0])
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("recovery: could not read outcome (%s)", exc)
        return {}


__all__ = [
    "RECOVERY_LABEL",
    "RECOVERY_STATUS_LABEL",
    "BUDGET_LABEL",
    "MAX_ATTEMPTS_PER_INPUT",
    "REASON_RECOVERED",
    "REASON_PARTIAL",
    "REASON_WAITING",
    "REASON_NO_NEW_DATA",
    "REASON_FAILED",
    "REASON_SKIPPED_RUNNING",
    "REASON_SKIPPED_COOLDOWN",
    "REASON_SKIPPED_BUDGET",
    "REASON_SKIPPED_HOLIDAY",
    "REASON_SKIPPED_UNKNOWN",
    "REASON_SKIPPED_READY",
    "REASON_SKIPPED_WINDOW",
    "evaluate",
    "input_fingerprint",
    "record_outcome",
    "last_outcome",
    "budget_state",
    "budget_allows",
    "budget_summary",
    "note_attempt",
    "refund_attempt",
]
