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
