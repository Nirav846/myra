"""Unified data-operations entry point (Phase 18).

Why this module exists
----------------------
MYRA's data maintenance was spread across ad-hoc entry points: root
``run_pipeline.py`` flags, ``python -m myra_app.lake_build``, the failing
enrichers, and the background scheduler's own registry. Remembering which one
applies to a given problem - and which are safe to run - was left to the
operator, and several of them look interchangeable while doing very different
things.

This module is a thin **front door**, not a second pipeline:

* it *discovers* operations rather than reimplementing them, deriving the
  scheduler-backed ones from :data:`myra_app.tasks.registry.TASKS` so the list
  cannot drift from the single source of truth;
* it *reports* prerequisites and limitations it can actually evidence, and says
  "no operation can produce this" when that is the truth;
* it *never* runs anything by itself. ``recommend`` only advises, and every
  operation that writes requires explicit confirmation.

What it deliberately is not
---------------------------
* Not a scheduler. It never starts background threads, never claims the
  scheduler lock, and does not reschedule anything.
* Not a fallback writer. If the data has no source in this repository, no
  operation is offered for it - inventing one would be exactly the silent
  fabrication the readiness work exists to prevent.
* Not an override. A live scheduler keeps owning its tasks; manual dispatch of a
  task it also runs is refused unless the operator explicitly acknowledges it.

Everything is lazy: importing this module does not import the enrichers, the
librarian, or any task module, so a broken optional import (for example an
unset ``MORNINGSTAR_TOKEN``) cannot break ``status``.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Missing-data taxonomy ────────────────────────────────────────────────────
#
# The point of naming these is that only some of them are data problems at all.
# Collapsing "we never built the derived artifact" and "nothing in this repo
# produces that value" into one "missing data" label is what led operators to
# reach for the wrong tool, repeatedly.

#: Nothing is wrong; the operation is simply available.
CATEGORY_RUNNABLE = "runnable"
#: The upstream publishes on a cadence and local data is behind it.
CATEGORY_STALE = "stale"
#: A usable source exists but the data was never fetched.
CATEGORY_NOT_INGESTED = "not_ingested"
#: The raw inputs exist; the derived artifact was never built.
CATEGORY_DERIVED_MISSING = "derived_missing"
#: The value exists under a different key or unit than the consumer reads.
CATEGORY_MAPPING_MISMATCH = "mapping_mismatch"
#: No source for this exists anywhere in the repository.
CATEGORY_UNSOURCED = "unsourced"
#: A prerequisite is absent, or a runtime defect prevents the operation.
CATEGORY_BLOCKED = "blocked"

CATEGORY_MEANING = {
    CATEGORY_RUNNABLE: "Nothing is wrong; the operation is available.",
    CATEGORY_STALE: (
        "The source is published on a cadence and local data is behind it; "
        "refreshing fixes it."
    ),
    CATEGORY_NOT_INGESTED: (
        "A usable source exists but the data was never fetched; a sync fixes it."
    ),
    CATEGORY_DERIVED_MISSING: (
        "The raw inputs are present but a derived artifact was never built; a "
        "build fixes it."
    ),
    CATEGORY_MAPPING_MISMATCH: (
        "The value is present under a different key or unit than the consumer "
        "reads; only a code change fixes it, not an operation."
    ),
    CATEGORY_UNSOURCED: (
        "No source for this value exists in the repository; no data operation "
        "can produce it."
    ),
    CATEGORY_BLOCKED: (
        "A prerequisite is missing or a runtime defect blocks it, so the "
        "operation cannot succeed yet."
    ),
}

#: Categories that no operation in this module can resolve. Reporting these as
#: "run X to fix it" would be a lie the operator would act on.
NOT_FIXABLE_BY_OPERATION = (CATEGORY_MAPPING_MISMATCH, CATEGORY_UNSOURCED)

#: Reporting order, so ``recommend`` output is deterministic.
CATEGORY_ORDER = (
    CATEGORY_BLOCKED,
    CATEGORY_STALE,
    CATEGORY_NOT_INGESTED,
    CATEGORY_DERIVED_MISSING,
    CATEGORY_MAPPING_MISMATCH,
    CATEGORY_UNSOURCED,
    CATEGORY_RUNNABLE,
)


@dataclasses.dataclass(frozen=True)
class DataOp:
    """One operator-reachable data operation.

    ``writes``, ``network`` and ``heavy`` drive the confirmation gate and the
    warnings, so they are declared conservatively and explicitly rather than
    guessed: anything that touches a database or cache sets ``writes``.
    """

    key: str
    title: str
    summary: str
    writes: bool = False
    network: bool = False
    heavy: bool = False
    #: Registry key of the scheduler task this dispatches to, if any.
    scheduler_task: Optional[str] = None
    #: ``"module:attr"`` of a zero-arg-to-kwargs callable, if any.
    runner: Optional[str] = None
    enabled: bool = True
    disabled_reason: Optional[str] = None
    #: Values a reader might reasonably expect this to produce, but it cannot.
    cannot_produce: tuple[str, ...] = ()
    #: Honest caveat shown before running, if any.
    note: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Recommendation:
    """An evidence-backed suggestion. Never executed by this module."""

    category: str
    title: str
    detail: str
    operation: Optional[str] = None
    requires_confirmation: bool = False

    @property
    def fixable(self) -> bool:
        return (
            self.operation is not None and self.category not in NOT_FIXABLE_BY_OPERATION
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "meaning": CATEGORY_MEANING.get(self.category, ""),
            "title": self.title,
            "detail": self.detail,
            "operation": self.operation,
            "fixable": self.fixable,
            "requires_confirmation": self.requires_confirmation,
        }


# ── Adapters ─────────────────────────────────────────────────────────────────
#
# Every adapter imports its heavy dependency *inside* the function. Importing
# this module must stay cheap and safe: ``fundamental_sync`` reads
# ``os.environ["MORNINGSTAR_TOKEN"]`` at import time and raises KeyError when it
# is unset, so a top-level import here would break ``status`` on a machine that
# has not configured it.


def _run_readiness() -> dict[str, Any]:
    from myra_app.data_readiness import assess

    return assess()


def _run_screening_modes() -> dict[str, Any]:
    from myra_app.data_readiness import assess

    return assess().get("screening_modes") or {}


def _run_scheduler_status() -> dict[str, Any]:
    from myra_app.scheduler_health import get_state

    return get_state()


def _run_lake_status() -> dict[str, Any]:
    from myra_app.data_readiness import technical_facts
    from myra_app.indicator_lake import status as lake_status

    reference = (technical_facts() or {}).get("latest_date")
    return lake_status(reference_date=reference)


def _run_lake_build(**options: Any) -> Any:
    """Build or refresh the indicator lake, delegating to its own CLI.

    ``lake_build.main`` is already resumable (an interrupted run leaves valid
    artifacts and re-running continues) and bounds work with ``--limit``, so
    this adapter passes options through instead of reimplementing the loop.
    """
    from myra_app.lake_build import main as build_main

    argv: list[str] = []
    if options.get("limit"):
        argv += ["--limit", str(int(options["limit"]))]
    if options.get("symbols"):
        argv += ["--symbols", str(options["symbols"])]
    if options.get("force"):
        argv.append("--force")
    return build_main(argv)


def _run_corporate_actions() -> Any:
    from myra_app.db.enrichers.corporate_actions_enricher import (
        enrich_corporate_actions,
    )

    return enrich_corporate_actions(force=True, days_back=365)


def _run_screener_enrich() -> Any:
    from myra_app.db.enrichers.screener_enricher import (
        enrich_screener_fundamentals,
    )

    return enrich_screener_fundamentals(force=True)


def _run_bse_backfill(**options: Any) -> Any:
    import asyncio

    from myra_app.utils.bse_shareholding import backfill_shareholding

    limit = int(options["limit"]) if options.get("limit") else None
    asyncio.run(backfill_shareholding(max_symbols=limit))

    from tools.sync_market_cap import sync_shareholding_and_float

    return sync_shareholding_and_float(limit=limit)


def _run_registered_task(name: str) -> Any:
    """Invoke a scheduler task's own entrypoint, once, in this process.

    Reuses the real contract (``resolve_entrypoint`` + ``TaskContext``) rather
    than reimplementing the executor, and passes a never-set ``shutdown_event``
    so task exit paths behave normally. Running one task once is not the same as
    scheduling it: no thread, no period, no ownership.
    """
    from myra_app.tasks.context import TaskContext
    from myra_app.tasks.executor import resolve_entrypoint
    from myra_app.tasks.registry import TASKS

    spec = TASKS.get(name)
    if spec is None:
        raise KeyError(f"unknown scheduler task: {name}")
    fn = resolve_entrypoint(spec)
    if fn is None:
        raise RuntimeError(f"could not load entrypoint for task '{name}'")
    ctx = TaskContext(
        shutdown_event=threading.Event(),
        logger=logging.getLogger(f"data_ops.{name}"),
        pause_event=None,
    )
    return fn(ctx)


# ── The operation registry ───────────────────────────────────────────────────

_LAKE_PREFIX = "myra_app.data_ops:"

#: Manual operations. These are the ones with no scheduler equivalent.
_MANUAL_OPS: tuple[DataOp, ...] = (
    DataOp(
        key="readiness",
        title="Data readiness report",
        summary="Every freshness and capability axis readiness assesses. Reads only.",
        runner=f"{_LAKE_PREFIX}_run_readiness",
    ),
    DataOp(
        key="screening-modes",
        title="Screening availability per mode",
        summary="Which screening modes can run right now, and why. Reads only.",
        runner=f"{_LAKE_PREFIX}_run_screening_modes",
    ),
    DataOp(
        key="scheduler-status",
        title="Scheduler ownership and health",
        summary="Who owns the scheduler lock, and whether its heartbeat is live.",
        runner=f"{_LAKE_PREFIX}_run_scheduler_status",
    ),
    DataOp(
        key="lake-status",
        title="Indicator lake status",
        summary="Whether the derived indicator lake exists, is fresh, and is readable.",
        runner=f"{_LAKE_PREFIX}_run_lake_status",
    ),
    DataOp(
        key="lake-build",
        title="Build the indicator lake",
        summary=(
            "Build or resume the derived indicator lake the v2.5 turbo ranking "
            "loads. Resumable; an interrupted run keeps its valid artifacts."
        ),
        writes=True,
        heavy=True,
        runner=f"{_LAKE_PREFIX}_run_lake_build",
        cannot_produce=(
            "sma150",
            "atr_pct",
            "pct_above_ma50_60d",
            "keltner_upper",
            "keltner_lower",
            "atr5",
            "drawdown",
        ),
        note=(
            "A built lake is necessary but not sufficient for a trustworthy "
            "MYRA_Score_v25: the scorer reads inputs the lake producer does not "
            "emit, which are then zero-filled. Use --limit for a bounded run."
        ),
    ),
    DataOp(
        key="enrich-corporate-actions",
        title="Backfill corporate actions",
        summary="Refresh corporate actions (splits, bonuses, dividends).",
        writes=True,
        network=True,
        heavy=True,
        runner=f"{_LAKE_PREFIX}_run_corporate_actions",
    ),
    DataOp(
        key="enrich-screener",
        title="Enrich fundamentals from Screener.in",
        summary=(
            "Slow HTML scrape for the ROE/ROCE metrics no other source provides. "
            "Deliberately not scheduled; manual use only."
        ),
        writes=True,
        network=True,
        heavy=True,
        runner=f"{_LAKE_PREFIX}_run_screener_enrich",
        note="Requires MORNINGSTAR_TOKEN to be set. Approximately 1.5s per symbol.",
    ),
    DataOp(
        key="backfill-bse",
        title="Backfill BSE shareholding + free float",
        summary=(
            "Fetch BSE shareholding and then sync free float / shares "
            "outstanding from yfinance."
        ),
        writes=True,
        network=True,
        heavy=True,
        runner=f"{_LAKE_PREFIX}_run_bse_backfill",
    ),
)

#: Per-scheduler-task classification. Every entry declares ``network`` and
#: ``heavy`` explicitly rather than inheriting a default, because both drive the
#: warning shown to the operator and a wrong default is a wrong warning. Every
#: registered task writes, since persisting data is what they exist to do.
_REGISTRY_META: dict[str, dict[str, Any]] = {
    "daily-ingest": {
        "title": "Daily market ingest",
        "summary": "Fetch and store the EOD bhavcopy session for the latest trading day.",
        "network": True,
        "heavy": True,
        "note": "Gated: declines outside the post-close window and on non-trading days.",
    },
    "etf-sync": {
        "title": "ETF sync",
        "summary": "Refresh the ETF reference set and prices.",
        "network": True,
        "heavy": True,
    },
    "index-sync": {
        "title": "Index sync",
        "summary": "Refresh index constituents and levels.",
        "network": True,
        "heavy": True,
    },
    "fundamentals-sync": {
        "title": "Fundamentals sync (full)",
        "summary": "Full fundamentals refresh from the primary vendor.",
        "network": True,
        "heavy": True,
    },
    "fundamentals-daily": {
        "title": "Fundamentals daily delta",
        "summary": "Weekday post-close fundamentals delta.",
        "network": True,
        "heavy": True,
        "note": "Gated: declines on weekends and before 18:00 IST.",
    },
    "institutional-sync": {
        "title": "Institutional holdings sync",
        "summary": "Refresh institutional / promoter holding data.",
        "network": True,
        "heavy": True,
    },
    "db-backup": {
        "title": "Database backup",
        "summary": "Nightly backup of the SQLite sidecars. Local disk only.",
        "network": False,
        "heavy": True,
    },
    "screener-enrich": {
        "title": "Screener.in enrichment (registry-disabled)",
        "summary": "Superseded by the manual enrich-screener operation.",
        "network": True,
        "heavy": True,
    },
    "fund-traction-sync": {
        "title": "Fund traction sync",
        "summary": "Sync the monthly fund traction artifact when a new month lands.",
        "network": True,
        "heavy": False,
        "note": "Smart-gated: no-ops and exits cheaply when no new month is published.",
    },
    "cross-buy-sync": {
        "title": "Cross-buy sync",
        "summary": "Refresh the cross-buy (promoter/insider buying) dataset.",
        "network": True,
        "heavy": False,
    },
    "traction-sma-update": {
        "title": "Traction SMA update",
        "summary": "Recompute moving averages on the traction dataset. Local CPU only.",
        "network": False,
        "heavy": False,
    },
    "mf-holdings-sync": {
        "title": "Mutual fund holdings sync",
        "summary": "RupeeVest portfolio sync (funds, holdings, AUM).",
        "network": True,
        "heavy": True,
    },
    "fundamentals-enrich": {
        "title": "Fundamentals enrichment",
        "summary": "Daily post-close gap-fill through the resilient source layer.",
        "network": True,
        "heavy": True,
        "note": "Gated: declines before 18:00 IST so the executor can retry later.",
    },
    "symbol-identity": {
        "title": "Symbol identity bridge",
        "summary": "Rebuild the alias -> canonical NSE ticker map.",
        "network": True,
        "heavy": False,
    },
    "traction-symbols": {
        "title": "Traction symbol resolution",
        "summary": "Resolve still-unmapped fund-traction keys to canonical tickers.",
        "network": True,
        "heavy": False,
    },
    "point-in-time-archive": {
        "title": "Point-in-time archive",
        "summary": "Append the rolling snapshots that are otherwise overwritten in place.",
        "network": False,
        "heavy": True,
    },
    "watchdog": {
        "title": "EOD recovery watchdog",
        "summary": "Self-managed recovery loop owned by the scheduler.",
        "network": True,
        "heavy": False,
    },
}


def _registry_ops() -> list[DataOp]:
    """Derive one operation per scheduler task, from the registry itself."""
    from myra_app.tasks.registry import TASKS

    ops: list[DataOp] = []
    for name, spec in TASKS.items():
        meta = dict(_REGISTRY_META.get(name, {}))
        note = meta.pop("note", None)
        title = meta.pop("title", name)
        summary = meta.pop("summary", f"Scheduler task '{name}'.")
        network = bool(meta.pop("network", False))
        heavy = bool(meta.pop("heavy", False))

        enabled = True
        reason = None
        if spec.self_loop:
            enabled = False
            reason = (
                "Self-managed loop, owned by the scheduler. Running it here "
                "would spin forever and duplicate the scheduler's own thread."
            )
        elif not spec.enabled:
            enabled = False
            reason = (
                "Disabled in the task registry (auto-run off; manual use only). "
                "The manual heterogeneous operation supersedes it."
            )

        ops.append(
            DataOp(
                key=name,
                title=title,
                summary=summary,
                # Every registered task writes: they exist to persist data.
                writes=True,
                network=network,
                heavy=heavy,
                scheduler_task=name,
                enabled=enabled,
                disabled_reason=reason,
                note=note,
            )
        )
    return ops


def _build_registry() -> dict[str, DataOp]:
    ops = {op.key: op for op in _MANUAL_OPS}
    for op in _registry_ops():
        # Manual operations win: they are the reviewed, documented surface.
        ops.setdefault(op.key, op)
    return dict(sorted(ops.items()))


#: The full operation registry, keyed by operation name.
OPERATIONS: dict[str, DataOp] = _build_registry()


# ── Probing ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _try(fn: Callable[[], Any], default: Any = None) -> Any:
    """Call ``fn``, returning ``default`` if it raises. Never propagates."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - reporting must not fail
        logger.debug("data_ops probe failed: %s", exc)
        return default


def scheduler_state() -> dict[str, Any]:
    """Scheduler ownership/health, or an explicit 'could not determine'."""
    state = _try(_run_scheduler_status)
    if not isinstance(state, dict):
        return {"state": "unknown", "error": "scheduler health could not be read"}
    return state


def operation_state(op: DataOp) -> dict[str, Any]:
    """Whether one operation can be dispatched right now, and by whom."""
    out: dict[str, Any] = {
        "key": op.key,
        "title": op.title,
        "writes": op.writes,
        "network": op.network,
        "heavy": op.heavy,
        "scheduler_task": op.scheduler_task,
        "enabled": op.enabled,
    }
    if not op.enabled:
        out.update(state="unavailable", reason=op.disabled_reason)
        return out

    from myra_app.utils import task_lock

    owner = _try(lambda: task_lock.read_owner(op.key))
    if owner:
        out.update(state="running", owner_pid=owner)
    else:
        out.update(state="idle", owner_pid=None)
    return out


def status() -> dict[str, Any]:
    """One screen: scheduler, readiness, screening modes, lake, operations.

    Read-only. Every probe is individually guarded so a single unreadable axis
    degrades that axis to ``unknown`` rather than failing the whole report.
    """
    assessment = _try(_run_readiness) or {}
    return {
        "generated_at": _now_iso(),
        "scheduler": scheduler_state(),
        "readiness": (
            {
                "state": assessment.get("state"),
                "label": assessment.get("label"),
                "explanation": assessment.get("explanation"),
            }
            if assessment
            else {"state": "unknown"}
        ),
        "screening_modes": (assessment.get("screening_modes") or {}).get("overall"),
        "lake": {
            "state": (assessment.get("screening") or {}).get("state"),
            "reason": (assessment.get("screening") or {}).get("reason"),
        },
        "operations": {key: operation_state(op) for key, op in OPERATIONS.items()},
    }


# ── Recommendations ──────────────────────────────────────────────────────────


def _scheduler_recommendation(state: dict[str, Any]) -> Optional[Recommendation]:
    """The operator's actual next action when no scheduler owns the lock."""
    verdict = state.get("state")
    if verdict in (None, "running", "starting"):
        return None
    if verdict == "disabled":
        return Recommendation(
            category=CATEGORY_BLOCKED,
            title="Background data operations are disabled by configuration",
            detail=(
                "DISABLE_SCHEDULER is set, so no scheduled ingest or EOD "
                "recovery will run. Unset it and start the application to "
                "restore them. This is an environment change, not a data task."
            ),
        )
    reason = state.get("reason") or "no live scheduler owns the lock"
    return Recommendation(
        category=CATEGORY_BLOCKED,
        title="No scheduler is running",
        detail=(
            f"{reason.rstrip('.')}. Start the application through the entry "
            "point that owns the scheduler (python run_fastapi.py) rather than "
            "clearing the lock: nothing is holding it, so the lock is not the "
            "fault."
        ),
    )


def _readiness_recommendation(assessment: dict[str, Any]) -> Optional[Recommendation]:
    """Map a non-ready freshness verdict onto the operation that fixes it."""
    state = assessment.get("state")
    if state in (None, "ready"):
        return None
    reason = assessment.get("reason") or ""
    explanation = assessment.get("explanation") or ""

    technical = assessment.get("technical") or {}
    latest, expected = technical.get("latest_date"), technical.get("expected_date")

    if (
        reason in ("source_missing", "source_empty")
        or "never been ingested" in explanation
    ):
        return Recommendation(
            category=CATEGORY_NOT_INGESTED,
            title="Market data has never been ingested",
            detail=(
                "technical_data holds no session at all, so every scanner has "
                "nothing to rank. " + (explanation or "")
            ).strip(),
            operation="daily-ingest",
            requires_confirmation=True,
        )

    if state == "stale" or (latest and expected and latest < expected):
        return Recommendation(
            category=CATEGORY_STALE,
            title="Local market data is behind the expected session",
            detail=(
                f"Newest stored session is {latest} and {expected} is expected. "
                "A daily ingest is needed to catch up."
            ),
            operation="daily-ingest",
            requires_confirmation=True,
        )

    if state == "failed":
        return Recommendation(
            category=CATEGORY_BLOCKED,
            title="The last ingestion attempt failed",
            detail=(
                (explanation or "")
                + " Re-running ingest is the direct fix; the failure itself "
                "should be read first."
            ).strip(),
            operation="daily-ingest",
            requires_confirmation=True,
        )

    if state == "waiting":
        return Recommendation(
            category=CATEGORY_RUNNABLE,
            title="Waiting on the upstream publication window",
            detail=(
                (explanation or "")
                + " No action is needed; nothing is broken and there is nothing "
                "to fetch yet."
            ).strip(),
        )

    # Anything else - most often a capability shortfall - is described precisely
    # by the screening-mode recommendations, so no generic note is added here.
    return None


def _v25_recommendations(modes: dict[str, Any]) -> list[Recommendation]:
    """Split the missing v2.5 inputs into 'a build fixes it' and 'nothing does'.

    This is the distinction the old single screening verdict erased, and the one
    an operator most needs: building the lake is a real, bounded job, but it
    cannot supply inputs that no code path in this repository produces.
    """
    from myra_app.screening_modes import (
        V25_LAKE_PRODUCED_INPUTS,
        V25_REQUIRED_INPUTS,
    )

    v25 = (modes or {}).get("modes", {}).get("v25_turbo") or {}
    state = v25.get("state")
    if state in (None, "operational"):
        return []

    out: list[Recommendation] = []
    missing = list(v25.get("missing") or [])
    produced = set(V25_LAKE_PRODUCED_INPUTS)
    derivable = sorted(set(missing) & produced)
    unsourced = sorted(set(missing) - produced)

    if v25.get("reason") in ("lake_empty", "lake_stale") or derivable:
        supplied = ", ".join(sorted(produced)) or "none"
        out.append(
            Recommendation(
                category=CATEGORY_DERIVED_MISSING,
                title="The indicator lake the v2.5 ranking loads was not built",
                detail=(
                    "The lake is derived, not ingested, so a scan will never "
                    "create it. Building it supplies the inputs it does produce "
                    f"({supplied}), out of {len(V25_REQUIRED_INPUTS)} the scorer "
                    "reads."
                ),
                operation="lake-build",
                requires_confirmation=True,
            )
        )

    if unsourced:
        out.append(
            Recommendation(
                category=CATEGORY_UNSOURCED,
                title="Some v2.5 inputs have no producer in this repository",
                detail=(
                    f"{len(unsourced)} of the {len(V25_REQUIRED_INPUTS)} inputs "
                    f"the scorer reads are not emitted by any code path "
                    f"({', '.join(unsourced)}); they are zero-filled instead. "
                    "No data operation can supply them - this needs an "
                    "implementation decision, and until then MYRA_Score_v25 "
                    "must not be published as authoritative."
                ),
            )
        )

    if v25.get("blocked_by"):
        out.append(
            Recommendation(
                category=CATEGORY_BLOCKED,
                title="The v2.5 candidate path fails at runtime",
                detail=(
                    "Even with the data present, candidate construction fails: "
                    + ", ".join(v25["blocked_by"])
                    + ". This is a code defect, not a data operation."
                ),
            )
        )
    return out


def recommend() -> list[Recommendation]:
    """Evidence-backed next steps. Deterministic, read-only, never self-running.

    Nothing here dispatches anything; the caller (or the operator) decides. Every
    recommendation that maps to an operation that writes requires explicit
    confirmation before that operation will run.
    """
    assessment = _try(_run_readiness) or {}
    modes = assessment.get("screening_modes") or {}
    found: list[Recommendation] = []

    for candidate in (
        _scheduler_recommendation(scheduler_state()),
        _readiness_recommendation(assessment),
    ):
        if candidate is not None:
            found.append(candidate)

    found.extend(_v25_recommendations(modes))

    if not found:
        state = assessment.get("state")
        if state not in (None, "ready"):
            # Non-ready for a reason none of the specific detectors recognised:
            # report it rather than claim everything is clean.
            found.append(
                Recommendation(
                    category=CATEGORY_BLOCKED,
                    title="Data readiness is not clean",
                    detail=assessment.get("explanation")
                    or f"Readiness reports '{state}'.",
                )
            )
        else:
            found.append(
                Recommendation(
                    category=CATEGORY_RUNNABLE,
                    title="No data problems detected",
                    detail=(
                        "Every readiness axis is clean and each screening mode "
                        "has the data it needs."
                    ),
                )
            )

    order = {name: i for i, name in enumerate(CATEGORY_ORDER)}
    return sorted(found, key=lambda r: (order.get(r.category, 99), r.title))


# ── Running ──────────────────────────────────────────────────────────────────


def get_operation(key: str) -> Optional[DataOp]:
    return OPERATIONS.get(key)


def _resolve_runner(dotted: str) -> Callable[..., Any]:
    module_name, _, attr = dotted.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _hint_for(error: str) -> Optional[str]:
    if "MORNINGSTAR_TOKEN" in error:
        return (
            "Set the MORNINGSTAR_TOKEN environment variable and re-run; the "
            "vendor module reads it at import time."
        )
    if "No such file" in error or "cannot find the file" in error.lower():
        return "A required upstream file appears to be absent."
    return None


def is_live(scheduler: Optional[dict[str, Any]] = None) -> bool:
    """Whether a scheduler currently owns the lock. Ambiguity counts as live."""
    state = scheduler if scheduler is not None else scheduler_state()
    # 'unknown' is deliberately treated as live: refusing is the safe side,
    # because the alternative is two writers on the same database.
    return state.get("state") in ("running", "starting", "unknown")


def run(
    key: str,
    confirm: bool = False,
    options: Optional[dict[str, Any]] = None,
    acknowledge_scheduler: bool = False,
) -> dict[str, Any]:
    """Dispatch one operation, once, in this process.

    Refuses (rather than queuing or forcing) in every ambiguous case: an
    unavailable operation, a write without confirmation, a duplicate run, or a
    task a live scheduler already owns. Never raises for an operational reason;
    failures come back in the result dict so callers can print them.
    """
    from myra_app.utils import task_lock

    options = dict(options or {})
    op = get_operation(key)
    if op is None:
        return {"ran": False, "reason": "unknown_operation", "key": key}

    if not op.enabled:
        return {
            "ran": False,
            "reason": "unavailable",
            "key": key,
            "detail": op.disabled_reason,
        }

    if op.writes and not confirm:
        return {
            "ran": False,
            "reason": "confirmation_required",
            "key": key,
            "detail": (
                f"'{key}' writes to a database or cache. Re-run with explicit "
                "confirmation once you have reviewed what it changes."
            ),
        }

    scheduler = scheduler_state()
    if op.scheduler_task and is_live(scheduler) and not acknowledge_scheduler:
        return {
            "ran": False,
            "reason": "scheduler_owns_task",
            "key": key,
            "detail": (
                f"A scheduler is {scheduler.get('state')} and already runs "
                f"'{op.scheduler_task}'. Running it here as well would put two "
                "writers on the same data. Use the scheduler's own cadence, or "
                "pass --acknowledge-scheduler if you have stopped it."
            ),
        }

    acquired, owner = task_lock.acquire(op.key)
    if not acquired:
        return {
            "ran": False,
            "reason": "already_running",
            "key": key,
            "owner_pid": owner,
            "detail": f"Another process (pid {owner}) already holds '{op.key}'.",
        }

    started = time.time()
    try:
        if op.scheduler_task:
            result = _run_registered_task(op.scheduler_task)
        elif op.runner:
            result = _resolve_runner(op.runner)(**options)
        else:  # pragma: no cover - registry construction prevents this
            raise RuntimeError(f"operation '{key}' has no runner")
    except Exception as exc:  # noqa: BLE001 - reported, never propagated
        message = f"{type(exc).__name__}: {exc}"
        out: dict[str, Any] = {
            "ran": False,
            "reason": "failed",
            "key": key,
            "error": message,
            "duration_s": round(time.time() - started, 2),
        }
        hint = _hint_for(message)
        if hint:
            out["hint"] = hint
        logger.warning("data_ops run '%s' failed: %s", key, message)
        return out
    finally:
        task_lock.release(op.key)

    return {
        "ran": True,
        "reason": "ok",
        "key": key,
        "duration_s": round(time.time() - started, 2),
        "result": result,
    }


# ── Presentation ─────────────────────────────────────────────────────────────


def _fmt_operation(op: DataOp) -> str:
    """Capability flags only; availability is the caller's to show."""
    flags = []
    if op.writes:
        flags.append("writes")
    if op.network:
        flags.append("network")
    if op.heavy:
        flags.append("slow")
    suffix = f"  [{', '.join(flags)}]" if flags else "  [read-only]"
    return f"  {op.key:<26}{op.title}{suffix}"


def print_status(as_json: bool = False) -> None:
    report = status()
    if as_json:
        print(json.dumps(report, indent=2, default=str))
        return

    print("MYRA data operations")
    print(f"  generated:  {report['generated_at']}")

    scheduler = report["scheduler"]
    print(
        "  scheduler:  "
        f"{scheduler.get('state')} "
        f"(lock={scheduler.get('lock')}, owner_pid={scheduler.get('owner_pid')})"
    )
    if scheduler.get("reason"):
        print(f"              {scheduler['reason']}")

    readiness = report["readiness"]
    print(f"  readiness:  {readiness.get('state')} - {readiness.get('label')}")

    modes = report.get("screening_modes") or {}
    if modes:
        print(f"  screening:  {modes.get('state')} - {modes.get('label')}")

    print("  operations:")
    for op in OPERATIONS.values():
        state = report["operations"][op.key]
        marker = "" if state["state"] == "idle" else f"  ({state['state']})"
        print(f"{_fmt_operation(op)}{marker}")


def print_recommendations(as_json: bool = False) -> None:
    found = recommend()
    if as_json:
        print(json.dumps([r.as_dict() for r in found], indent=2, default=str))
        return

    print("Recommended next steps (nothing is run automatically):")
    for rec in found:
        print(f"\n  [{rec.category}] {rec.title}")
        print(f"    {rec.detail}")
        if rec.fixable:
            confirm = " --yes" if rec.requires_confirmation else ""
            print(f"    -> python -m myra_app.data_ops run {rec.operation}{confirm}")
        elif rec.operation is None:
            print("    -> no operation can resolve this")


def print_operations() -> None:
    print("Available operations:")
    for op in OPERATIONS.values():
        print(_fmt_operation(op))
        print(f"      {op.summary}")
        if not op.enabled:
            print(f"      unavailable: {op.disabled_reason}")
        if op.note:
            print(f"      note: {op.note}")
        if op.cannot_produce:
            print(f"      cannot produce: {', '.join(op.cannot_produce)}")


_USAGE = """MYRA data operations

  python -m myra_app.data_ops status [--json]
      One screen: scheduler, readiness, screening modes, operations.

  python -m myra_app.data_ops recommend [--json]
      Evidence-backed next steps. Nothing is run automatically.

  python -m myra_app.data_ops list
      Every operation, what it changes, and what it cannot do.

  python -m myra_app.data_ops run <operation> [--yes] [--limit N] [--force]
                                              [--symbols A,B] [--acknowledge-scheduler]
      Run one operation. Operations that write require --yes.

  python -m myra_app.data_ops
      Interactive menu (when attached to a terminal).
"""


def _prompt(question: str) -> str:
    try:
        return input(question).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _menu() -> int:
    print("MYRA data operations - interactive menu")
    print(
        "Read-only operations are safe to run. Anything that writes will ask again.\n"
    )
    keys = [k for k, op in OPERATIONS.items() if op.enabled]
    for index, key in enumerate(keys, start=1):
        print(f"  {index:>2}. {_fmt_operation(OPERATIONS[key]).strip()}")

    choice = _prompt("\nSelect an operation number (blank to quit): ")
    if not choice:
        return 0
    try:
        key = keys[int(choice) - 1]
    except (ValueError, IndexError):
        print("Not a valid selection.")
        return 2

    op = OPERATIONS[key]
    print(f"\n{op.title}\n  {op.summary}")
    if op.note:
        print(f"  note: {op.note}")
    if op.cannot_produce:
        print(f"  cannot produce: {', '.join(op.cannot_produce)}")

    confirm = False
    if op.writes:
        answer = _prompt(f"\n'{key}' writes data. Type 'yes' to proceed: ")
        confirm = answer == "yes"
        if not confirm:
            print("Cancelled. Nothing was changed.")
            return 0

    outcome = run(key, confirm=confirm)
    print(json.dumps(outcome, indent=2, default=str))
    return 0 if outcome.get("ran") else 1


def _parse_options(argv: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if "--limit" in argv:
        index = argv.index("--limit")
        if index + 1 < len(argv):
            try:
                options["limit"] = int(argv[index + 1])
            except ValueError:
                pass
    if "--symbols" in argv:
        index = argv.index("--symbols")
        if index + 1 < len(argv):
            options["symbols"] = argv[index + 1]
    if "--force" in argv:
        options["force"] = True
    return options


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    positional = [a for a in argv if not a.startswith("-")]

    if not argv and sys.stdin.isatty():
        return _menu()

    command = positional[0] if positional else "help"

    if command == "status":
        print_status(as_json=as_json)
        return 0
    if command == "recommend":
        print_recommendations(as_json=as_json)
        return 0
    if command in ("list", "operations"):
        print_operations()
        return 0
    if command == "run":
        if len(positional) < 2:
            print("Usage: run <operation> [--yes]", file=sys.stderr)
            return 2
        key = positional[1]
        outcome = run(
            key,
            confirm="--yes" in argv,
            options=_parse_options(argv),
            acknowledge_scheduler="--acknowledge-scheduler" in argv,
        )
        print(json.dumps(outcome, indent=2, default=str))
        return 0 if outcome.get("ran") else 1
    if command == "menu":
        return _menu()

    print(_USAGE)
    return 0


if __name__ == "__main__":  # pragma: no cover - thin entry point
    raise SystemExit(main())
