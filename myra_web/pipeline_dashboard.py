"""
MYRA Pipeline Dashboard — manual sync control with SSE status updates.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from myra_app.constants import DB_DIR, DATA_DIR, CACHE_DIR, LOGS_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger("pipeline_dashboard")
IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter(prefix="/api/pipeline")


class PipelineManager:
    _instance = None
    _instance_lock = threading.Lock()

    TASKS = {
        "ingest": {
            "name": "Daily Ingest",
            "task_key": "daily_ingest",
            "func": None,
            "estimated_duration": "2-5 min",
        },
        "enrichment": {
            "name": "Feature Enrichment",
            "task_key": "enrichment",
            "func": None,
            "estimated_duration": "5-10 min",
        },
        "etf_sync": {
            "name": "ETF Sync",
            "task_key": "etf_sync",
            "func": None,
            "estimated_duration": "1-2 min",
        },
        "index_sync": {
            "name": "Index Sync",
            "task_key": "index_sync",
            "func": None,
            "estimated_duration": "1-3 min",
        },
        "fundamentals_sync": {
            "name": "Fundamentals Sync",
            "task_key": "fundamentals_sync",
            "func": None,
            "estimated_duration": "10-20 min",
        },
        "market_cap_sync": {
            "name": "Market Cap Sync",
            "task_key": "market_cap_sync",
            "func": None,
            "estimated_duration": "3-5 min",
        },
        "institutional_sync": {
            "name": "Institutional Sync",
            "task_key": "institutional_sync",
            "func": None,
            "estimated_duration": "1-3 min",
        },
    }

    ORDER = ["ingest", "enrichment", "etf_sync", "index_sync", "fundamentals_sync", "market_cap_sync", "institutional_sync"]

    TASK_TIMEOUTS = {
        "ingest": 600,
        "enrichment": 600,
        "etf_sync": 120,
        "index_sync": 300,
        "fundamentals_sync": 1800,
        "market_cap_sync": 600,
        "institutional_sync": 600,
    }

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._lock = threading.Lock()
        self._state = {
            "active_task_id": None,
            "started_at": None,
            "message": "Idle",
            "progress_pct": 0.0,
            "status": "idle",
            "task_states": {},
            "run_type": None,
        }
        self._cancel_event = threading.Event()
        self._scheduler_shutdown = threading.Event()
        self._server_shutdown = threading.Event()
        self._schedule_paused = False
        self._sse_queues: list[deque] = []
        self._sse_lock = threading.Lock()
        self._ensure_schema()
        self._resolve_task_funcs()
        self._reset_stale_run()
        self._load_schedule_pause()
        self._start_scheduler()

    def _resolve_task_funcs(self):
        import myra_app.background_orchestrator as bg

        self.TASKS["ingest"]["func"] = bg._task_daily_ingest
        self.TASKS["etf_sync"]["func"] = bg._task_etf_sync
        self.TASKS["index_sync"]["func"] = bg._task_index_sync
        self.TASKS["fundamentals_sync"]["func"] = self._run_fundamentals_sync
        self.TASKS["enrichment"]["func"] = self._run_enrichment
        self.TASKS["market_cap_sync"]["func"] = self._run_market_cap_sync
        self.TASKS["institutional_sync"]["func"] = self._run_institutional_sync

    def _run_enrichment(self):
        from myra_app.feature_enrichment import process_enrichment_pipeline
        import sqlite3

        lib = LibrarianCore(read_only=False)
        tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        conn = sqlite3.connect(tech_db)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            process_enrichment_pipeline(lib, conn, target_date=None)
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.error(f"[ENRICHMENT] Pipeline failed: {e}")
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

    def _run_market_cap_sync(self):
        from myra_app.utils.fundamentals_sync import sync_fundamentals

        sync_fundamentals(force=True)

    def _run_fundamentals_sync(self):
        from myra_app.fundamental_sync import FundamentalSync

        with self._lock:
            self._state["progress_pct"] = 10
            self._state["message"] = "Starting fundamentals sync..."
        self._push_event({"type": "progress", "task_id": "fundamentals_sync", "progress_pct": 10})

        sync = FundamentalSync()

        with self._lock:
            self._state["progress_pct"] = 30
            self._state["message"] = "Fetching Morningstar bulk data..."
        self._push_event({"type": "progress", "task_id": "fundamentals_sync", "progress_pct": 30})
        ms_data = sync._fetch_morningstar_bulk()

        with self._lock:
            self._state["progress_pct"] = 50
            self._state["message"] = "Getting NIFTY 500 symbols..."
        self._push_event({"type": "progress", "task_id": "fundamentals_sync", "progress_pct": 50})
        nifty_symbols = sync._get_nifty_500_symbols()

        with self._lock:
            self._state["progress_pct"] = 60
            self._state["message"] = f"Fetching NSE data for {len(nifty_symbols) if nifty_symbols else 0} symbols..."
        self._push_event({"type": "progress", "task_id": "fundamentals_sync", "progress_pct": 60})
        if nifty_symbols:
            nse_data = sync._fetch_nse_all(nifty_symbols)
        else:
            nse_data = {}

        with self._lock:
            self._state["progress_pct"] = 85
            self._state["message"] = "Merging and inserting data..."
        self._push_event({"type": "progress", "task_id": "fundamentals_sync", "progress_pct": 85})
        today = datetime.now(IST).date().isoformat()
        sync._merge_and_insert(ms_data, nse_data, today)
        sync._log_summary()

        with self._lock:
            self._state["progress_pct"] = 100
            self._state["message"] = "Fundamentals sync complete"
        self._push_event({"type": "progress", "task_id": "fundamentals_sync", "progress_pct": 100})

    def _run_institutional_sync(self):
        from myra_app.utils.institutional_sync import InstitutionalSync

        with self._lock:
            self._state["progress_pct"] = 10
            self._state["message"] = "Starting institutional data sync..."
        self._push_event({"type": "progress", "task_id": "institutional_sync", "progress_pct": 10})

        syncer = InstitutionalSync()

        with self._lock:
            self._state["progress_pct"] = 30
            self._state["message"] = "Syncing insider trades..."
        self._push_event({"type": "progress", "task_id": "institutional_sync", "progress_pct": 30})
        syncer.sync_insider_trades()

        with self._lock:
            self._state["progress_pct"] = 60
            self._state["message"] = "Syncing bulk deals..."
        self._push_event({"type": "progress", "task_id": "institutional_sync", "progress_pct": 60})
        syncer.sync_bulk_deals()

        with self._lock:
            self._state["progress_pct"] = 80
            self._state["message"] = "Syncing block deals..."
        self._push_event({"type": "progress", "task_id": "institutional_sync", "progress_pct": 80})
        syncer.sync_block_deals()

        with self._lock:
            self._state["progress_pct"] = 100
            self._state["message"] = "Institutional sync complete"
        self._push_event({"type": "progress", "task_id": "institutional_sync", "progress_pct": 100})

    def _start_scheduler(self):
        """Background daemon thread that checks schedule config every 60s and triggers due tasks."""
        def _scheduler_loop():
            while not self._scheduler_shutdown.is_set():
                try:
                    self._check_schedules()
                except Exception as e:
                    logger.warning(f"[SCHEDULER] Error in scheduler loop: {e}")
                # Sleep in 1-second chunks so shutdown is responsive
                for _ in range(60):
                    if self._scheduler_shutdown.is_set():
                        break
                    time.sleep(1)

        t = threading.Thread(target=_scheduler_loop, daemon=True, name="pipeline-scheduler")
        t.start()

    def _check_schedules(self):
        """Check if any scheduled tasks are due to run."""
        if self._schedule_paused:
            return
        config = self.get_schedule_config()
        if not config:
            return

        ist_now = datetime.now(IST)
        today_str = ist_now.date().isoformat()

        with self._lock:
            if self._state["status"] != "idle":
                return

        for task_key, cfg in config.items():
            if not cfg.get("enabled"):
                continue
            scheduled_time = cfg.get("time", "18:00")

            # Only trigger if we've reached or passed the scheduled time
            if ist_now.strftime("%H:%M") != scheduled_time:
                continue

            # Check if already ran today
            try:
                lib = LibrarianCore(read_only=True)
                row = lib._meta_conn.execute(
                    "SELECT last_run FROM sync_log WHERE task_name = ?", (task_key,)
                ).fetchone()
                lib.close()
                if row and row[0]:
                    try:
                        run_date = datetime.fromisoformat(row[0]).date().isoformat()
                        if run_date == today_str:
                            continue  # already ran today
                    except Exception:
                        pass
            except Exception:
                pass

            logger.info(
                "[SCHEDULER] Triggering scheduled task '%s' at %s IST",
                task_key, ist_now.strftime("%H:%M"),
            )
            self.run_task(next(
                (tid for tid, t in self.TASKS.items() if t["task_key"] == task_key),
                task_key,
            ), stop_on_fail=True)

    def _ensure_schema(self):
        try:
            lib = LibrarianCore(read_only=False)
            try:
                for col_sql in [
                    "ALTER TABLE sync_log ADD COLUMN last_status TEXT DEFAULT 'unknown'",
                    "ALTER TABLE sync_log ADD COLUMN error_message TEXT DEFAULT NULL",
                    "ALTER TABLE sync_log ADD COLUMN progress_pct REAL DEFAULT 0",
                ]:
                    try:
                        lib._meta_conn.execute(col_sql)
                        lib._meta_conn.commit()
                    except Exception:
                        pass
            except Exception:
                pass
            lib.close()
        except Exception as e:
            logger.warning(f"Schema migration failed: {e}")

    def _reset_stale_run(self):
        try:
            lib = LibrarianCore(read_only=False)
            row = lib._meta_conn.execute(
                "SELECT value FROM metadata WHERE key='pipeline_run_state'"
            ).fetchone()
            if row and row[0]:
                data = json.loads(row[0])
                if data.get("status") == "running":
                    crashed_task = data.get("active_task_id")
                    if crashed_task:
                        self._write_sync_log(
                            crashed_task,
                            status="crashed",
                            error="Server stopped unexpectedly \u2013 data may be incomplete. Re-run this task.",
                        )
                        with self._lock:
                            self._state["task_states"][crashed_task] = {
                                "current_status": "crashed",
                                "error_message": "Server stopped unexpectedly \u2013 data may be incomplete. Re-run this task.",
                                "progress_pct": 0,
                            }
                    data["status"] = "idle"
                    data["active_task_id"] = None
                    data["task_states"] = self._state["task_states"]
                    lib._meta_conn.execute(
                        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('pipeline_run_state', ?)",
                        (json.dumps(data),),
                    )
                    lib._meta_conn.commit()
            lib.close()
        except Exception:
            pass

    def _persist_state(self):
        try:
            lib = LibrarianCore(read_only=False)
            lib._meta_conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('pipeline_run_state', ?)",
                (json.dumps(self._state),),
            )
            lib._meta_conn.commit()
            lib.close()
        except Exception as e:
            logger.warning(f"Failed to persist state: {e}")

    def _write_sync_log(self, task_key: str, status: str, error: str = None, progress: float = 0):
        try:
            ist_now = datetime.now(IST).isoformat()
            lib = LibrarianCore(read_only=False)
            lib._meta_conn.execute(
                """INSERT OR REPLACE INTO sync_log
                   (task_name, last_run, last_status, error_message, progress_pct)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_key, ist_now, status, error, progress),
            )
            lib._meta_conn.commit()
            lib.close()
        except Exception as e:
            logger.warning(f"Failed to write sync_log: {e}")

    def _push_event(self, event_data: dict):
        with self._sse_lock:
            dead = []
            for q in self._sse_queues:
                q.append(event_data)
                if len(q) > 100:
                    dead.append(q)
            for q in dead:
                self._sse_queues.remove(q)

    def signal_shutdown(self):
        self._server_shutdown.set()

    def register_sse_client(self) -> deque:
        q: deque = deque(maxlen=100)
        with self._sse_lock:
            self._sse_queues.append(q)
        return q

    def unregister_sse_client(self, q: deque):
        with self._sse_lock:
            if q in self._sse_queues:
                self._sse_queues.remove(q)

    def get_status(self) -> dict:
        tasks = {}
        try:
            lib = LibrarianCore(read_only=True)
            rows = lib._meta_conn.execute(
                "SELECT task_name, last_run, last_status, error_message, progress_pct FROM sync_log"
            ).fetchall()
            for row in rows:
                tasks[row[0]] = {
                    "last_run": row[1],
                    "last_status": row[2],
                    "error_message": row[3],
                    "progress_pct": row[4],
                }
            lib.close()
        except Exception:
            pass

        for tid, cfg in self.TASKS.items():
            key = cfg["task_key"]
            if key not in tasks:
                tasks[key] = {
                    "last_run": None,
                    "last_status": "never",
                    "error_message": None,
                    "progress_pct": 0,
                }
            with self._lock:
                ts = self._state["task_states"].get(key, {})
                if ts:
                    tasks[key].update(ts)

        with self._lock:
            overall = {
                "status": self._state["status"],
                "active_task_id": self._state["active_task_id"],
                "started_at": self._state["started_at"],
                "message": self._state["message"],
                "progress_pct": self._state["progress_pct"],
                "run_type": self._state["run_type"],
            }

        return {"overall": overall, "tasks": tasks}

    def get_checks(self) -> dict:
        checks = {}

        dirs = {"DB_DIR": DB_DIR, "DATA_DIR": DATA_DIR, "CACHE_DIR": CACHE_DIR, "LOGS_DIR": LOGS_DIR}
        for name, path in dirs.items():
            checks[name] = {"exists": os.path.isdir(path), "path": path}

        db_checks = {}
        for key, fname in LibrarianCore.DB_MAP.items():
            path = os.path.join(DB_DIR, fname)
            ok = False
            if os.path.exists(path):
                try:
                    import sqlite3
                    c = sqlite3.connect(path)
                    c.execute("SELECT 1")
                    c.close()
                    ok = True
                except Exception:
                    pass
            db_checks[key] = {"exists": os.path.exists(path), "reachable": ok}
        checks["databases"] = db_checks

        api_keys = {}
        for k in ["MORNINGSTAR_API_KEY"]:
            api_keys[k] = os.environ.get(k) is not None
        checks["api_keys"] = api_keys

        return checks

    def run_task(self, task_id: str, stop_on_fail: bool = True):
        if task_id not in self.TASKS and task_id != "all":
            raise ValueError(f"Unknown task: {task_id}")

        def _run():
            self._cancel_event.clear()
            self._timeout_occurred = False
            with self._lock:
                self._state["run_type"] = "all" if task_id == "all" else "single"
            if task_id == "all":
                self._run_all(stop_on_fail)
            else:
                self._execute_task(task_id)
            with self._lock:
                self._state["status"] = "idle"
                self._state["active_task_id"] = None
                self._state["started_at"] = None
                self._state["message"] = "Idle"
                self._state["progress_pct"] = 0
                self._state["run_type"] = None
            self._persist_state()
            self._push_event({"type": "state_change", "state": self._state})

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _run_all(self, stop_on_fail: bool):
        # ── WARNING: When stop_on_fail=False, downstream tasks may receive
        #    stale or partial data if a prerequisite (e.g. ingest, enrichment)
        #    has failed. The user explicitly opted to continue; no auto‑skip
        #    is applied beyond what each task internally handles. ──
        for tid in self.ORDER:
            if self._cancel_event.is_set():
                self._push_event({"type": "all_cancelled", "task": tid})
                break
            result = self._execute_task(tid)
            if stop_on_fail and result.get("status") in ("failed", "timeout"):
                self._push_event({
                    "type": "all_stopped",
                    "task": tid,
                    "reason": result.get("error", "Unknown error"),
                })
                break

    def _on_timeout(self):
        self._timeout_occurred = True
        self._cancel_event.set()

    def _execute_task(self, task_id: str) -> dict:
        cfg = self.TASKS[task_id]
        task_key = cfg["task_key"]

        with self._lock:
            self._state["active_task_id"] = task_key
            self._state["status"] = "running"
            self._state["started_at"] = datetime.now(IST).isoformat()
            self._state["message"] = f"Running {cfg['name']}..."
            self._state["progress_pct"] = 0
        self._persist_state()
        self._push_event({"type": "task_started", "task_id": task_key, "task_name": cfg["name"]})

        timeout_sec = self.TASK_TIMEOUTS.get(task_id, 1800)
        timer = threading.Timer(timeout_sec, self._on_timeout)
        timer.daemon = True
        timer.start()

        error = None
        status = "completed"
        try:
            cfg["func"]()
        except Exception as e:
            error = str(e)
            status = "failed"
            logger.error(f"Task {task_id} failed: {e}")
        finally:
            timer.cancel()

        if self._timeout_occurred:
            status = "timeout"
            error = f"Task timed out after {timeout_sec}s"

        self._write_sync_log(task_key, status, error)

        with self._lock:
            self._state["task_states"][task_key] = {
                "current_status": status,
                "error_message": error,
                "progress_pct": 100 if status == "completed" else 0,
            }

        self._push_event({
            "type": "task_completed",
            "task_id": task_key,
            "status": status,
            "error": error,
        })

        return {"status": status, "error": error}

    def cancel(self):
        self._cancel_event.set()
        with self._lock:
            self._state["message"] = "Cancellation requested..."
        self._push_event({"type": "cancellation_requested"})

    def get_run_status(self) -> dict:
        with self._lock:
            return {
                "status": self._state["status"],
                "active_task_id": self._state["active_task_id"],
                "started_at": self._state["started_at"],
                "message": self._state["message"],
                "progress_pct": self._state["progress_pct"],
                "cancel_requested": self._cancel_event.is_set(),
                "run_type": self._state["run_type"],
            }

    def get_schedule_config(self) -> dict:
        try:
            lib = LibrarianCore(read_only=True)
            row = lib._meta_conn.execute(
                "SELECT value FROM metadata WHERE key='pipeline_schedule_config'"
            ).fetchone()
            lib.close()
            if row and row[0]:
                return json.loads(row[0])
        except Exception:
            pass
        return {}

    def set_schedule_config(self, task_key: str, enabled: bool):
        config = self.get_schedule_config()
        config[task_key] = {"enabled": enabled, "time": "18:00"}
        try:
            lib = LibrarianCore(read_only=False)
            lib._meta_conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('pipeline_schedule_config', ?)",
                (json.dumps(config),),
            )
            lib._meta_conn.commit()
            lib.close()
        except Exception as e:
            logger.warning(f"Failed to save schedule config: {e}")
        self._push_event({"type": "schedule_updated", "config": config})

    def _load_schedule_pause(self):
        try:
            lib = LibrarianCore(read_only=True)
            row = lib._meta_conn.execute(
                "SELECT value FROM metadata WHERE key='pipeline_schedule_paused'"
            ).fetchone()
            lib.close()
            if row and row[0]:
                self._schedule_paused = json.loads(row[0])
        except Exception:
            pass

    def _persist_schedule_pause(self):
        try:
            lib = LibrarianCore(read_only=False)
            lib._meta_conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('pipeline_schedule_paused', ?)",
                (json.dumps(self._schedule_paused),),
            )
            lib._meta_conn.commit()
            lib.close()
        except Exception as e:
            logger.warning(f"Failed to persist schedule pause: {e}")

    def get_schedule_paused(self) -> bool:
        return self._schedule_paused

    def toggle_schedule_pause(self) -> bool:
        self._schedule_paused = not self._schedule_paused
        self._persist_schedule_pause()
        return self._schedule_paused


manager = PipelineManager()


class RunRequest(BaseModel):
    task: str = "all"
    stop_on_fail: bool = True


class ScheduleToggleRequest(BaseModel):
    task_key: str
    enabled: bool


@router.get("/status")
def get_pipeline_status():
    return manager.get_status()


@router.get("/check")
def get_pipeline_checks():
    return manager.get_checks()


@router.post("/run")
def run_pipeline(req: RunRequest):
    # Auto-reset if the pipeline has been "running" for > 5 minutes (likely a crashed thread)
    if manager._state["status"] == "running" and manager._state.get("started_at"):
        from datetime import datetime
        started = datetime.fromisoformat(manager._state["started_at"])
        elapsed = (datetime.now(IST) - started).total_seconds()
        if elapsed > 300:  # 5 minutes
            manager._state["status"] = "idle"
            manager._state["active_task_id"] = None
            manager._state["message"] = "Previous run timed out — auto-reset"
            manager._state["progress_pct"] = 0
            manager._persist_state()
    if manager.get_run_status()["status"] == "running":
        raise HTTPException(400, "Pipeline is already running")

    key_to_id = {cfg["task_key"]: tid for tid, cfg in PipelineManager.TASKS.items()}
    if req.task in key_to_id:
        task_id = key_to_id[req.task]
    elif req.task in PipelineManager.TASKS:
        task_id = req.task
    elif req.task == "all":
        task_id = "all"
    else:
        raise HTTPException(400, f"Unknown task: {req.task}")

    try:
        manager.run_task(task_id, req.stop_on_fail)
        return {"success": True, "message": f"Task '{req.task}' started"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/run/status")
def get_run_status():
    return manager.get_run_status()


@router.post("/cancel")
def cancel_pipeline():
    manager.cancel()
    return {"success": True, "message": "Cancellation requested"}


@router.post("/force-reset")
def force_reset():
    manager._cancel_event.set()
    manager._state["status"] = "idle"
    manager._state["active_task_id"] = None
    manager._state["message"] = "Force reset by user"
    manager._state["progress_pct"] = 0
    manager._timeout_occurred = False
    manager._persist_state()
    return {"success": True, "message": "Pipeline force-reset to idle"}


@router.get("/events")
async def sse_events():
    q = manager.register_sse_client()

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'state': manager.get_status()})}\n\n"
            while not manager._server_shutdown.is_set():
                data = None
                with manager._sse_lock:
                    if q:
                        data = q.popleft()
                if data:
                    yield f"data: {json.dumps(data)}\n\n"
                else:
                    await asyncio.sleep(0.5)
            yield f"data: {json.dumps({'type': 'shutdown'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            manager.unregister_sse_client(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/schedule")
def get_schedule():
    return manager.get_schedule_config()


@router.post("/toggle-schedule")
def toggle_schedule(req: ScheduleToggleRequest):
    manager.set_schedule_config(req.task_key, req.enabled)
    return {"success": True, "config": manager.get_schedule_config()}


@router.get("/schedule/paused")
def get_schedule_paused():
    return {"paused": manager.get_schedule_paused()}


@router.post("/schedule/pause")
def toggle_schedule_pause():
    paused = manager.toggle_schedule_pause()
    return {"paused": paused, "success": True}
