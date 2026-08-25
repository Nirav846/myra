"""Declarative background task registry (Phase 3 refactor).

Single source of truth for every periodic background task: which module
implements it, how often it runs, and how the executor treats it.

Dict keys are the historical thread names ("etf-sync"); ``TaskSpec.label``
is the sync_log key ("etf_sync") consumed by data-health — both MUST stay
stable.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class TaskSpec:
    """Scheduling + failure semantics for one background task."""

    # Import path of the task module (resolved lazily via importlib).
    module: str
    # sync_log primary key — used by data-health. DO NOT RENAME.
    label: str
    # Minimum days between runs.
    interval_days: int
    # Run immediately on startup when overdue.
    catchup: bool = True
    # Pause 30s after launching this thread (stagger DB-heavy syncs).
    stagger: bool = True
    # Mark the task as run even when it raises (prevents retry storms).
    mark_on_failure: bool = False
    # Mark the task as run after an unexceptional return. Disable for tasks
    # with internal time gates that may legitimately no-op (e.g. daily ingest
    # refuses weekends / pre-18:00) so the executor keeps retrying.
    mark_on_success: bool = True
    # Thread is launched at all when False.
    enabled: bool = True
    # Function name on the module to call with ctx (default "run"; the
    # fundamentals module also exposes "run_daily").
    entrypoint: str = "run"
    # Task manages its own loop (watchdog); executor calls it once.
    self_loop: bool = False


TASKS: dict[str, TaskSpec] = {
    # One-shot at startup (gated internally by hour/weekend); watchdog
    # re-triggers it through the day. Executor loop is a safety net.
    "daily-ingest": TaskSpec(
        module="myra_app.tasks.ingest",
        label="daily_ingest",
        interval_days=1,
        catchup=True,
        stagger=True,
        mark_on_success=False,
    ),
    # Special: self-managing 60s poll loop, no catch-up, no stagger.
    "watchdog": TaskSpec(
        module="myra_app.tasks.watchdog",
        label="watchdog",
        interval_days=1,
        catchup=False,
        stagger=False,
        self_loop=True,
    ),
    "etf-sync": TaskSpec(
        module="myra_app.tasks.etf_sync",
        label="etf_sync",
        interval_days=7,
    ),
    "index-sync": TaskSpec(
        module="myra_app.tasks.index_sync",
        label="index_sync",
        interval_days=7,
    ),
    "fundamentals-sync": TaskSpec(
        module="myra_app.tasks.fundamentals",
        label="fundamentals_sync",
        interval_days=7,
    ),
    "fundamentals-daily": TaskSpec(
        module="myra_app.tasks.fundamentals",
        label="fundamentals_daily",
        interval_days=1,
        entrypoint="run_daily",
        # Weekday/18:00 gate lives inside run_daily; only mark after a real sync.
        mark_on_success=False,
    ),
    "institutional-sync": TaskSpec(
        module="myra_app.tasks.institutional",
        label="institutional_sync",
        interval_days=7,
    ),
    "db-backup": TaskSpec(
        module="myra_app.tasks.db_backup",
        label="db_backup",
        interval_days=1,  # nightly backup (docstring + prior cadence)
    ),
    # Auto-run disabled (SCREENER_ENRICH_AUTO_ENABLED); manual backfill only.
    "screener-enrich": TaskSpec(
        module="myra_app.tasks.screener_enrich",
        label="screener_enrich",
        interval_days=7,
        catchup=False,
        enabled=False,
    ),
    "fund-traction-sync": TaskSpec(
        module="myra_app.tasks.fund_traction",
        label="fund_traction_sync",
        interval_days=30,  # monthly per docstring/intent
    ),
    "cross-buy-sync": TaskSpec(
        module="myra_app.tasks.cross_buy",
        label="cross_buy_sync",
        interval_days=30,
        # Dead downloader / processor errors must not retry-storm each poll.
        mark_on_failure=True,
    ),
    "traction-sma-update": TaskSpec(
        module="myra_app.tasks.traction_sma",
        label="traction_sma_update",
        interval_days=1,
    ),
}
