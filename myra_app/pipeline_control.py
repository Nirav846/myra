"""Authoritative manual pipeline control for MYRA.

This module is the ONE source of truth for:

  * task definitions and ordering (:data:`PIPELINE_TASKS` / :data:`PIPELINE_ORDER`)
  * current task and overall run state (:meth:`PipelineControl.get_status`)
  * per-task status / stage / progress
  * last-run history and error details (persisted to the existing ``sync_log``
    table via ``myra_app.utils.task_utils._mark_task_run``)
  * cancellation state

It deliberately contains NO scheduler. Periodic/scheduled execution belongs to
``myra_app.background_orchestrator`` driven by ``myra_app.tasks.registry``.
This module only *invokes the same task modules* for an operator-triggered run, so
there is exactly one implementation of every task and one place status is written.

Status vocabulary (normalised; the legacy mixed vocabularies are gone):

    run    : idle | running | cancelling | completed | failed | cancelled
    task   : never | queued | running | completed | failed | cancelled
             | timed_out | skipped

Progress honesty: no task in this pipeline exposes a real ``processed/total``
count, so ``progress`` is reported as a *named stage* with ``progress_pct=None``
(indeterminate). A percentage is only ever published when a task genuinely
produces one, and 100% is only ever written after a successful completion.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from myra_app.constants import DB_DIR, DISABLE_FUNDAMENTAL_WRITERS
from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

MAX_EVENTS = 200

# Terminal task states — nothing may overwrite these with "running" unless the
# task is genuinely being re-executed.
TERMINAL_TASK_STATES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "skipped"}
)


def _now() -> str:
    return datetime.now(IST).isoformat()


# --------------------------------------------------------------------------
# Task definitions
# --------------------------------------------------------------------------


class PipelineTask:
    """One operator-triggerable pipeline step."""

    def __init__(
        self,
        key: str,
        label: str,
        timeout: int,
        run: Callable[["TaskRun"], None],
        depends_on: tuple[str, ...] = (),
    ) -> None:
        self.key = key
        self.label = label
        self.timeout = timeout
        self._run = run
        self.depends_on = depends_on

    def __call__(self, tr: "TaskRun") -> None:
        self._run(tr)


def _run_daily_ingest(tr: "TaskRun") -> None:
    from myra_app.tasks.ingest import run

    tr.stage("Downloading bhavcopy and writing technical_data")
    run(tr.ctx)


def _run_etf_sync(tr: "TaskRun") -> None:
    from myra_app.tasks.etf_sync import run

    tr.stage("Syncing ETF blocklist")
    run(tr.ctx)


def _run_index_sync(tr: "TaskRun") -> None:
    from myra_app.tasks.index_sync import run

    tr.stage("Syncing NIFTY index constituents")
    run(tr.ctx)


def _run_enrichment(tr: "TaskRun") -> None:
    from myra_app.feature_enrichment import process_enrichment_pipeline

    tr.stage("Opening technical_data for enrichment")
    lib = LibrarianCore(read_only=False)
    conn = sqlite3.connect(os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"]))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        tr.stage("Computing features")
        process_enrichment_pipeline(lib, conn, target_date=None)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            lib.close()
        except Exception:
            pass
    tr.stage("Enrichment committed")


def _run_fundamentals_sync(tr: "TaskRun") -> None:
    from myra_app.fundamental_sync import FundamentalSync

    sync = FundamentalSync()
    tr.stage("Fetching Morningstar bulk data")
    ms_data = sync._fetch_morningstar_bulk()
    tr.stage("Resolving NIFTY 500 symbols")
    nifty_symbols = sync._get_nifty_500_symbols()
    tr.stage(
        f"Fetching NSE data for {len(nifty_symbols) if nifty_symbols else 0} symbols"
    )
    nse_data = (
        sync._fetch_nse_all(nifty_symbols, tr.ctx.shutdown_event)
        if nifty_symbols
        else {}
    )
    tr.stage("Merging and inserting")
    today = datetime.now(IST).date().isoformat()
    sync._merge_and_insert(ms_data, nse_data, today)
    sync._log_summary()
    tr.stage("Fundamentals sync complete")


def _run_market_cap_sync(tr: "TaskRun") -> None:
    from myra_app.utils.fundamentals_sync import sync_fundamentals

    tr.stage("Refreshing market capitalisation")
    sync_fundamentals(force=True)
    tr.stage("Market cap sync complete")


def _run_shares_outstanding_sync(tr: "TaskRun") -> None:
    from myra_app.fundamental_sync import FundamentalSync

    tr.stage("Scanning for stale shares_outstanding")
    result = FundamentalSync()._refresh_stale_shares_outstanding(
        cancel_event=tr.ctx.shutdown_event
    )
    tr.stage(
        f"Shares refresh complete: {result.get('updated', 0)}/{result.get('total', 0)} updated"
    )

    # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher owns the fundamentals table
    if DISABLE_FUNDAMENTAL_WRITERS:
        tr.note("DISABLE_FUNDAMENTAL_WRITERS=True: last_fundamental_update skipped")
        return
    try:
        conn = sqlite3.connect(os.path.join(DB_DIR, "myra_valuation.db"))
        try:
            conn.execute(
                "UPDATE fundamentals SET last_fundamental_update = date('now') "
                "WHERE shares_outstanding > 0"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # non-fatal, matches prior behaviour
        tr.note(f"Could not stamp last_fundamental_update: {exc}")


def _run_institutional_sync(tr: "TaskRun") -> None:
    from myra_app.utils.institutional_sync import InstitutionalSync

    syncer = InstitutionalSync()
    tr.stage("Syncing insider trades")
    syncer.sync_insider_trades()
    tr.stage("Syncing bulk deals")
    syncer.sync_bulk_deals()
    tr.stage("Syncing block deals")
    syncer.sync_block_deals()
    tr.stage("Institutional sync complete")


# Ordered pipeline. Order 1 -> 2 is a genuine dependency: enrichment reads symbols
# produced by daily ingest, so running it standalone against a stale DB silently
# produces wrong features.
PIPELINE_TASKS: dict[str, PipelineTask] = {
    "daily_ingest": PipelineTask(
        "daily_ingest", "Daily Ingest", 900, _run_daily_ingest
    ),
    "enrichment": PipelineTask(
        "enrichment",
        "Feature Enrichment",
        1800,
        _run_enrichment,
        depends_on=("daily_ingest",),
    ),
    "etf_sync": PipelineTask("etf_sync", "ETF Sync", 300, _run_etf_sync),
    "index_sync": PipelineTask("index_sync", "Index Sync", 600, _run_index_sync),
    "fundamentals_sync": PipelineTask(
        "fundamentals_sync", "Fundamentals Sync", 3600, _run_fundamentals_sync
    ),
    "market_cap_sync": PipelineTask(
        "market_cap_sync", "Market Cap Sync", 900, _run_market_cap_sync
    ),
    "shares_outstanding_sync": PipelineTask(
        "shares_outstanding_sync", "Shares Refresh", 900, _run_shares_outstanding_sync
    ),
    "institutional_sync": PipelineTask(
        "institutional_sync", "Institutional Sync", 900, _run_institutional_sync
    ),
}

PIPELINE_ORDER: list[str] = list(PIPELINE_TASKS)


# --------------------------------------------------------------------------
# Per-execution context handed to task bodies
# --------------------------------------------------------------------------


class TaskRun:
    """Progress/stage reporting handle for one running task.

    ``stage()`` publishes a *named stage*. It never invents a percentage: callers
    that have a real count pass it via ``progress_pct``.
    """

    def __init__(self, control: "PipelineControl", ctx: TaskContext) -> None:
        self._control = control
        self.ctx = ctx

    def stage(self, message: str) -> None:
        self._control._publish_stage(self._control._active_key, message)

    def progress(self, pct: float) -> None:
        self._control._publish_progress(self._control._active_key, pct)

    def note(self, message: str) -> None:
        self._control._publish_stage(self._control._active_key, message, note=True)


class PipelineBusyError(RuntimeError):
    """Raised when a second run is requested while one is in flight."""


class PipelineControl:
    """Singleton owning manual-run state, history and the SSE event bus."""

    _instance: Optional["PipelineControl"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "PipelineControl":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialised", False):
            return
        self._initialised = True

        self._lock = threading.RLock()
        # Guards the busy-check + state transition together so two concurrent
        # POST /run calls cannot both observe "idle" and both start.
        self._start_lock = threading.Lock()

        self._worker: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._timeout_timer: Optional[threading.Timer] = None

        self._state: dict[str, Any] = {
            "status": "idle",
            "active_task_id": None,
            "started_at": None,
            "finished_at": None,
            "message": "Idle",
            "run_type": None,
            "stop_on_fail": True,
        }

        # task_key -> live/last state. Seeded to "never" so a cold backend still
        # renders the full pipeline.
        self._tasks: dict[str, dict[str, Any]] = {
            key: {
                "current_status": "never",
                "stage": None,
                "progress_pct": None,
                "error_message": None,
                "last_run": None,
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
            }
            for key in PIPELINE_TASKS
        }

        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._subscribers: list[deque] = []
        self._active_key: Optional[str] = None

        self._shutdown = threading.Event()
        self._load_history()

    # -- schema -------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Additive only: sync_log gains last_status/error_message if absent."""
        try:
            lib = LibrarianCore(read_only=False)
            try:
                for col_sql in (
                    "ALTER TABLE sync_log ADD COLUMN last_status TEXT DEFAULT 'unknown'",
                    "ALTER TABLE sync_log ADD COLUMN error_message TEXT DEFAULT NULL",
                ):
                    try:
                        lib._meta_conn.execute(col_sql)
                        lib._meta_conn.commit()
                    except sqlite3.OperationalError:
                        pass  # already present — expected on a healthy DB
            finally:
                lib.close()
        except Exception as exc:
            logger.warning("PipelineControl: sync_log migration skipped: %s", exc)

    # -- persistence (existing tables only; no new DB) ----------------------

    def _load_history(self) -> None:
        """Seed per-task last-run state from sync_log and the persisted run state."""
        self._ensure_schema()
        try:
            lib = LibrarianCore(read_only=True)
            try:
                rows = lib._meta_conn.execute(
                    "SELECT task_name, last_run, last_status, error_message FROM sync_log"
                ).fetchall()
                row = lib._meta_conn.execute(
                    "SELECT value FROM metadata WHERE key='pipeline_run_state'"
                ).fetchone()
            finally:
                lib.close()
        except Exception as exc:
            logger.warning("PipelineControl: could not load history: %s", exc)
            return

        for task_name, last_run, last_status, error_message in rows:
            state = self._tasks.get(task_name)
            if not state:
                continue
            state["last_run"] = last_run
            state["current_status"] = self._normalise_sync_status(last_status, last_run)
            state["error_message"] = error_message

        # A run persisted as "running" means the server died mid-task. Surface it
        # as a failure rather than pretending it is idle or still active.
        if row and row[0]:
            import json

            try:
                data = json.loads(row[0])
            except Exception:
                data = None
            if data:
                for field in (
                    "status",
                    "active_task_id",
                    "started_at",
                    "finished_at",
                    "message",
                    "run_type",
                    "stop_on_fail",
                ):
                    if field in data:
                        self._state[field] = data[field]
                if data.get("task_states"):
                    for key, ts in data["task_states"].items():
                        if key not in self._tasks or not isinstance(ts, dict):
                            continue
                        # Persisted task_states were written by the old pipeline
                        # dashboard with its own vocabulary ("timeout",
                        # "crashed"), so they must be normalised too — otherwise
                        # a restored run leaks statuses the UI cannot render.
                        if "current_status" in ts:
                            ts["current_status"] = self._normalise_sync_status(
                                ts["current_status"], ts.get("started_at")
                            )
                        self._tasks[key].update(ts)
                if self._state.get("status") in ("running", "cancelling"):
                    crashed = self._state.get("active_task_id")
                    msg = "Server stopped unexpectedly — data may be incomplete. Re-run this task."
                    if crashed in self._tasks:
                        self._tasks[crashed].update(
                            {
                                "current_status": "failed",
                                "error_message": msg,
                                "progress_pct": None,
                            }
                        )
                    self._state.update(
                        {"status": "failed", "message": msg, "active_task_id": None}
                    )

    @staticmethod
    def _normalise_sync_status(last_status: Any, last_run: Any) -> str:
        """Map sync_log's legacy values onto the normalised vocabulary.

        Historical rows were written by several different writers, so the same
        table contains `success`/`failed` (task_utils), `completed`/`timeout`/
        `crashed` (the old pipeline dashboard) and the bare `unknown` column
        default. All of them must land on the vocabulary the UI renders.
        """
        value = (last_status or "").strip().lower()
        if value in ("success", "completed", "ok"):
            return "completed"
        if value in ("failed", "error", "crashed"):
            # A crashed run produced no trustworthy result — surface it as a
            # failure rather than letting it fall through to "never run".
            return "failed"
        if value == "timeout":
            return "timed_out"
        # An ingestion attempt that ran but ingested nothing. Must stay its own
        # state: falling through to "completed" would report a day-stale
        # database as healthy.
        if value in ("no_new_data", "nonewdata"):
            return "no_new_data"
        # An ingestion attempt that started (internal cooldown bookkeeping row).
        # Must never read as a completed ingestion.
        if value in ("attempted", "attempt"):
            return "no_new_data"
        if value in ("cancelled", "timed_out", "skipped"):
            return value
        # 'unknown' is the column default for rows predating last_status.
        return "completed" if last_run else "never"

    def _persist_run_state(self) -> None:
        try:
            import json

            payload = dict(self._state)
            payload["task_states"] = {
                key: {
                    "current_status": ts["current_status"],
                    "error_message": ts["error_message"],
                    "progress_pct": ts["progress_pct"],
                }
                for key, ts in self._tasks.items()
            }
            lib = LibrarianCore(read_only=False)
            try:
                lib._meta_conn.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES "
                    "('pipeline_run_state', ?)",
                    (json.dumps(payload),),
                )
                lib._meta_conn.commit()
            finally:
                lib.close()
        except Exception as exc:
            logger.warning("PipelineControl: could not persist run state: %s", exc)

    @staticmethod
    def _mark_run(task_key: str, status: str, error: Optional[str]) -> None:
        """Write the authoritative last-run record to sync_log."""
        sync_status = {"completed": "success"}.get(status, status)
        _mark_task_run(task_key, status=sync_status, error_message=error)

    # -- event bus ----------------------------------------------------------

    def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("time", _now())
        self._events.append(event)
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.append(event)

    def subscribe(self) -> deque:
        queue: deque = deque(maxlen=MAX_EVENTS)
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: deque) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._events)[-limit:]

    def _publish_stage(
        self, task_key: Optional[str], message: str, note: bool = False
    ) -> None:
        if not task_key:
            return
        with self._lock:
            state = self._tasks.get(task_key)
            if state is None:
                return
            state["stage"] = message
            if self._state["active_task_id"] == task_key:
                self._state["message"] = message
        self._emit(
            {
                "type": "note" if note else "stage",
                "task_id": task_key,
                "message": message,
            }
        )

    def _publish_progress(self, task_key: Optional[str], pct: float) -> None:
        if not task_key:
            return
        # Never let a task report out of range or reach 100 before success.
        value = max(0.0, min(99.0, float(pct)))
        with self._lock:
            state = self._tasks.get(task_key)
            if state is None:
                return
            state["progress_pct"] = value
            if self._state["active_task_id"] == task_key:
                self._state["progress_pct"] = value
        self._emit({"type": "progress", "task_id": task_key, "progress_pct": value})

    # -- run control --------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            overall = {
                "status": self._state["status"],
                "active_task_id": self._state["active_task_id"],
                "started_at": self._state["started_at"],
                "finished_at": self._state["finished_at"],
                "message": self._state["message"],
                "progress_pct": self._state.get("progress_pct"),
                "run_type": self._state["run_type"],
                "stop_on_fail": self._state["stop_on_fail"],
                "cancel_requested": self._cancel_event.is_set(),
                "busy": self._worker is not None and self._worker.is_alive(),
            }
            tasks = {key: dict(state) for key, state in self._tasks.items()}
        return {
            "overall": overall,
            "tasks": tasks,
            "order": list(PIPELINE_ORDER),
            "events": self.recent_events(50),
        }

    def _unknown_tasks(self) -> list[str]:
        return [k for k in PIPELINE_ORDER if k not in self._tasks]

    def start_run(self, task: str, stop_on_fail: bool = True) -> dict[str, Any]:
        """Start ``task`` (or ``all``). Raises :class:`PipelineBusyError` if busy."""
        run_all = task == "all"
        keys = list(PIPELINE_ORDER) if run_all else self._resolve_single(task)

        # Busy check and state transition under one lock — race-safe.
        with self._start_lock:
            if self._worker is not None and self._worker.is_alive():
                raise PipelineBusyError("A pipeline run is already in progress")
            self._cancel_event.clear()

            # Mark queued vs running immediately: a task must never display its
            # previous status while it is being started.
            with self._lock:
                self._state.update(
                    {
                        "status": "running",
                        "active_task_id": None,
                        "started_at": _now(),
                        "finished_at": None,
                        "message": (
                            "Starting full pipeline"
                            if run_all
                            else f"Starting {PIPELINE_TASKS[keys[0]].label}"
                        ),
                        "progress_pct": None,
                        "run_type": "all" if run_all else "single",
                        "stop_on_fail": stop_on_fail,
                    }
                )
                if run_all:
                    for key in keys:
                        self._tasks[key].update(
                            {
                                "current_status": "queued",
                                "stage": "Waiting for its turn",
                                "progress_pct": None,
                                "error_message": None,
                                "started_at": None,
                                "finished_at": None,
                                "duration_seconds": None,
                            }
                        )
                else:
                    key = keys[0]
                    self._tasks[key].update(
                        {
                            "current_status": "running",
                            "stage": "Starting",
                            "progress_pct": None,
                            "error_message": None,
                            "started_at": _now(),
                            "finished_at": None,
                            "duration_seconds": None,
                        }
                    )
            self._persist_run_state()

        self._emit(
            {
                "type": "run_started",
                "task": task,
                "run_type": "all" if run_all else "single",
            }
        )

        worker = threading.Thread(
            target=self._run_sequence,
            args=(keys, run_all, stop_on_fail),
            daemon=True,
            name="pipeline-manual-run",
        )
        with self._start_lock:
            self._worker = worker
        worker.start()
        return {"success": True, "task": task, "tasks": keys}

    def _resolve_single(self, task: str) -> list[str]:
        if task in PIPELINE_TASKS:
            return [task]
        # Accept the human label too, so the UI can pass either.
        for key, spec in PIPELINE_TASKS.items():
            if spec.label.lower() == task.lower():
                return [key]
        raise KeyError(task)

    def cancel(self) -> dict[str, Any]:
        """Request cancellation.

        This does NOT report the run as stopped: the worker keeps the run in
        ``cancelling`` until the underlying task actually returns.
        """
        with self._lock:
            busy = self._state["status"] in ("running", "cancelling")
            if busy:
                self._state["status"] = "cancelling"
                self._state[
                    "message"
                ] = "Cancellation requested — waiting for the task to stop"
        if busy:
            self._cancel_event.set()
            self._emit({"type": "cancellation_requested"})
        self._persist_run_state()
        return {"success": True, "cancel_requested": busy}

    def is_busy(self) -> bool:
        with self._lock:
            return self._state["status"] in ("running", "cancelling")

    def shutting_down(self) -> bool:
        """True once :meth:`shutdown` has been called (lets SSE streams close)."""
        return self._shutdown.is_set()

    def shutdown(self) -> None:
        """Stop accepting work and let open event streams terminate."""
        self._shutdown.set()
        self._cancel_event.set()

    # -- worker -------------------------------------------------------------

    def _run_sequence(self, keys: list[str], run_all: bool, stop_on_fail: bool) -> None:
        overall_status = "completed"
        try:
            for key in keys:
                if self._cancel_event.is_set():
                    self._mark_remaining_skipped(keys, key, "cancelled")
                    overall_status = "cancelled"
                    break
                outcome = self._execute_task(key)
                if outcome in ("failed", "timed_out"):
                    if run_all and stop_on_fail:
                        self._mark_remaining_skipped(keys, key, "skipped")
                        self._emit(
                            {
                                "type": "all_stopped",
                                "task": key,
                                "reason": self._tasks[key]["error_message"] or outcome,
                            }
                        )
                        overall_status = "failed" if outcome == "failed" else "failed"
                        break
                    overall_status = (
                        "failed" if overall_status != "cancelled" else overall_status
                    )
                if self._cancel_event.is_set():
                    self._mark_remaining_skipped(keys, key, "cancelled")
                    overall_status = "cancelled"
                    break
            else:
                if self._cancel_event.is_set():
                    overall_status = "cancelled"
        finally:
            with self._lock:
                # Retain the final result until the next run starts — do not
                # blank the status back to "Idle".
                self._state.update(
                    {
                        "status": overall_status,
                        "active_task_id": None,
                        "finished_at": _now(),
                        "progress_pct": None,
                        "run_type": None,
                        "message": {
                            "completed": "Pipeline completed",
                            "failed": "Pipeline failed",
                            "cancelled": "Pipeline cancelled",
                        }.get(overall_status, "Pipeline finished"),
                    }
                )
            self._persist_run_state()
            self._emit(
                {
                    "type": "run_finished",
                    "status": overall_status,
                    "finished_at": self._state["finished_at"],
                }
            )
            with self._start_lock:
                self._worker = None

    def _mark_remaining_skipped(
        self, keys: list[str], from_key: str, reason: str
    ) -> None:
        started = False
        for key in keys:
            if key == from_key:
                started = True
                continue
            if not started:
                continue
            with self._lock:
                state = self._tasks[key]
                if state["current_status"] == "queued":
                    state["current_status"] = (
                        "cancelled" if reason == "cancelled" else "skipped"
                    )
                    state["stage"] = (
                        "Not started — run cancelled"
                        if reason == "cancelled"
                        else "Skipped — an earlier task failed"
                    )
        self._emit({"type": "tasks_skipped", "reason": reason})

    def _execute_task(self, key: str) -> str:
        spec = PIPELINE_TASKS[key]
        with self._lock:
            self._active_key = key
            state = self._tasks[key]
            state.update(
                {
                    "current_status": "running",
                    "stage": "Starting",
                    "progress_pct": None,
                    "error_message": None,
                    "started_at": _now(),
                    "finished_at": None,
                    "duration_seconds": None,
                }
            )
            self._state["active_task_id"] = key
            self._state["message"] = f"Running {spec.label}…"
            self._state["started_at"] = state["started_at"]
        self._persist_run_state()
        self._emit({"type": "task_started", "task_id": key, "task_name": spec.label})

        # The task's TaskContext carries the SAME event the cancel endpoint sets,
        # so tasks that poll it stop cooperatively.
        ctx = TaskContext(shutdown_event=self._cancel_event, logger=logger)
        run_handle = TaskRun(self, ctx)

        error: Optional[str] = None
        status = "completed"
        timed_out = threading.Event()

        def _on_timeout() -> None:
            timed_out.set()
            self._cancel_event.set()

        timer = threading.Timer(spec.timeout, _on_timeout)
        timer.daemon = True
        self._timeout_timer = timer
        timer.start()

        t0 = time.perf_counter()
        try:
            spec(run_handle)
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("Pipeline task %s failed", key)
        finally:
            timer.cancel()
            self._timeout_timer = None

        if timed_out.is_set():
            status = "timed_out"
            error = f"Task timed out after {spec.timeout}s"
        elif self._cancel_event.is_set() and status == "completed":
            status = "cancelled"
            error = "Cancelled by operator"

        duration = round(time.perf_counter() - t0, 2)

        if status in ("failed", "timed_out", "cancelled"):
            # Do not leave a success-looking 100% on a non-success outcome.
            with self._lock:
                self._tasks[key]["progress_pct"] = None

        with self._lock:
            self._tasks[key].update(
                {
                    "current_status": status,
                    "stage": self._tasks[key]["stage"]
                    if status != "completed"
                    else None,
                    "error_message": error,
                    "finished_at": _now(),
                    "duration_seconds": duration,
                    # 100% only after genuine success.
                    "progress_pct": 100.0 if status == "completed" else None,
                }
            )
            self._active_key = None

        self._mark_run(key, status, error)
        self._persist_run_state()
        self._emit(
            {
                "type": "task_completed",
                "task_id": key,
                "status": status,
                "error": error,
                "duration_seconds": duration,
            }
        )
        return status

    # -- system checks / schedule (retained for the Data Sync page) ----------

    def get_checks(self) -> dict[str, Any]:
        from myra_app.constants import CACHE_DIR, DATA_DIR, LOGS_DIR

        checks: dict[str, Any] = {
            name: {"exists": os.path.isdir(path), "path": path}
            for name, path in (
                ("DB_DIR", DB_DIR),
                ("DATA_DIR", DATA_DIR),
                ("CACHE_DIR", CACHE_DIR),
                ("LOGS_DIR", LOGS_DIR),
            )
        }
        db_checks: dict[str, Any] = {}
        for key, fname in LibrarianCore.DB_MAP.items():
            path = os.path.join(DB_DIR, fname)
            reachable = False
            if os.path.exists(path):
                try:
                    conn = sqlite3.connect(path)
                    conn.execute("SELECT 1")
                    conn.close()
                    reachable = True
                except Exception:
                    pass
            db_checks[key] = {"exists": os.path.exists(path), "reachable": reachable}
        checks["databases"] = db_checks
        checks["api_keys"] = {
            "MORNINGSTAR_API_KEY": os.environ.get("MORNINGSTAR_API_KEY") is not None
        }
        return checks

    def get_schedule_config(self) -> dict[str, Any]:
        import json

        try:
            lib = LibrarianCore(read_only=True)
            try:
                row = lib._meta_conn.execute(
                    "SELECT value FROM metadata WHERE key='pipeline_schedule_config'"
                ).fetchone()
            finally:
                lib.close()
            if row and row[0]:
                return json.loads(row[0])
        except Exception as exc:
            logger.warning("PipelineControl: could not read schedule: %s", exc)
        return {}

    def set_schedule_config(self, task_key: str, enabled: bool) -> dict[str, Any]:
        import json

        if task_key not in PIPELINE_TASKS:
            raise KeyError(task_key)
        config = self.get_schedule_config()
        config[task_key] = {"enabled": enabled, "time": "18:00"}
        try:
            lib = LibrarianCore(read_only=False)
            try:
                lib._meta_conn.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES "
                    "('pipeline_schedule_config', ?)",
                    (json.dumps(config),),
                )
                lib._meta_conn.commit()
            finally:
                lib.close()
        except Exception as exc:
            logger.warning("PipelineControl: could not save schedule: %s", exc)
        self._emit({"type": "schedule_updated", "config": config})
        return config

    def get_schedule_paused(self) -> bool:
        import json

        try:
            lib = LibrarianCore(read_only=True)
            try:
                row = lib._meta_conn.execute(
                    "SELECT value FROM metadata WHERE key='pipeline_schedule_paused'"
                ).fetchone()
            finally:
                lib.close()
            if row and row[0]:
                return bool(json.loads(row[0]))
        except Exception:
            pass
        return False

    def toggle_schedule_pause(self) -> bool:
        import json

        paused = not self.get_schedule_paused()
        try:
            lib = LibrarianCore(read_only=False)
            try:
                lib._meta_conn.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES "
                    "('pipeline_schedule_paused', ?)",
                    (json.dumps(paused),),
                )
                lib._meta_conn.commit()
            finally:
                lib.close()
        except Exception as exc:
            logger.warning("PipelineControl: could not persist schedule pause: %s", exc)
        self._emit({"type": "schedule_updated", "config": {"paused": paused}})
        return paused


#: Process-wide singleton. Constructing it does NOT start a scheduler — scheduled
#: execution stays with ``myra_app.background_orchestrator``.
control = PipelineControl()
