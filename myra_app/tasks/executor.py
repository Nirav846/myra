"""Generic periodic task executor (Phase 3 refactor).

Owns ALL scheduling scaffolding that used to be copy-pasted across task
modules: startup catch-up, due-check polling loop, shutdown-aware sleeps,
sync_log marking, and failure handling. Task modules now contain a single
unit of work behind ``run(ctx)``.
"""

import importlib
import logging

from myra_app.tasks.context import TaskContext
from myra_app.tasks.registry import TaskSpec
from myra_app.utils.task_utils import (
    _is_task_due,
    _is_task_overdue,
    _mark_task_run,
)

logger = logging.getLogger(__name__)

# How often each executor thread re-checks its due condition.
POLL_SECONDS = 60

# Pause between staggered thread launches (orchestrator launch loop).
STAGGER_SECONDS = 30


def resolve_entrypoint(spec: TaskSpec):
    """Import the task module and return its entrypoint, or None on failure."""
    try:
        module = importlib.import_module(spec.module)
        fn = getattr(module, spec.entrypoint, None)
    except Exception as e:  # graceful: log and skip this task forever
        logger.error(f"[MYRA BG] Cannot load {spec.module}.{spec.entrypoint}: {e}")
        return None
    if fn is None:
        logger.error(
            f"[MYRA BG] Module {spec.module} has no entrypoint '{spec.entrypoint}'"
        )
    return fn


def _execute_once(name: str, spec: TaskSpec, fn, ctx: TaskContext) -> None:
    """Run one unit of work and update sync_log per the spec's failure policy."""
    if ctx.shutdown_event.is_set():
        return
    try:
        fn(ctx)
    except Exception as e:
        logger.error(f"[MYRA BG] Task {name} failed: {e}")
        if spec.mark_on_failure:
            logger.warning(
                f"[MYRA BG] Task {name} marked as run despite failure "
                "(mark_on_failure=True); next attempt next cycle."
            )
            _mark_task_run(spec.label)
    else:
        if spec.mark_on_success and not ctx.shutdown_event.is_set():
            logger.info(f"[MYRA BG] Task {name}: marking as run (mark_on_success=True)")
            _mark_task_run(spec.label)
        else:
            logger.debug(
                f"[MYRA BG] Task {name}: not marking "
                f"(mark_on_success={spec.mark_on_success}, "
                f"shutdown={ctx.shutdown_event.is_set()})"
            )


def run_periodic(task_name: str, spec: TaskSpec, ctx: TaskContext) -> None:
    """
    Executor body for one background task thread.

    Behaviour:
      - disabled tasks return immediately (no thread work);
      - ``self_loop`` tasks (watchdog) are called once — they own their loop;
      - optional startup catch-up when overdue;
      - infinite due-check poll loop with shutdown-aware waits;
      - sync_log marking governed by mark_on_success / mark_on_failure.
    """
    if not spec.enabled:
        logger.debug(f"[MYRA BG] Task {task_name} disabled – not running.")
        return
    if ctx.shutdown_event.is_set():
        return

    fn = resolve_entrypoint(spec)
    if fn is None:
        return

    # Self-managing loops (watchdog): hand the thread over entirely.
    if spec.self_loop:
        logger.info(f"[MYRA BG] Task {task_name} starting (self-managed loop).")
        try:
            fn(ctx)
        except Exception as e:
            logger.error(f"[MYRA BG] Task {task_name} (self-loop) crashed: {e}")
        return

    # Startup catch-up: run immediately when overdue.
    if spec.catchup and _is_task_overdue(spec.label, days=spec.interval_days):
        logger.info(f"[MYRA BG] Task {task_name} overdue – running catch-up now...")
        _execute_once(task_name, spec, fn, ctx)

    # Due-check poll loop.
    while not ctx.shutdown_event.is_set():
        try:
            if _is_task_due(spec.label, interval_days=spec.interval_days):
                logger.info(f"[MYRA BG] Task {task_name} due – running...")
                _execute_once(task_name, spec, fn, ctx)
        except Exception as e:
            # Never let scheduling bookkeeping kill the thread.
            logger.error(f"[MYRA BG] Task {task_name} scheduling error: {e}")
        if ctx.shutdown_event.wait(POLL_SECONDS):
            return
