"""Shared context object handed to every background task entrypoint."""

import dataclasses
import logging
import threading


@dataclasses.dataclass
class TaskContext:
    """
    Runtime services passed to background task `run()` entrypoints.

    Attributes:
        shutdown_event: Event set by the orchestrator on shutdown; long-running
            tasks poll/wait on it to exit responsively.
        logger: Logger tasks write through.
    """

    shutdown_event: threading.Event
    logger: logging.Logger


_default_context: TaskContext | None = None
_default_context_lock = threading.Lock()


def default_context() -> TaskContext:
    """
    Process-wide fallback context for manual (web-triggered) task runs.

    The orchestrator passes its own context to scheduled threads; routes and
    dashboards that invoke tasks directly use this shared instance. Its
    shutdown event stays unset for the process lifetime — manual one-shot
    runs are short-lived by construction.
    """
    global _default_context
    if _default_context is None:
        with _default_context_lock:
            if _default_context is None:
                _default_context = TaskContext(
                    shutdown_event=threading.Event(),
                    logger=logging.getLogger("myra.tasks"),
                )
    return _default_context
