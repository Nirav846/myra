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
    # How often this task's executor thread re-checks its due condition.
    # Defaults to executor.POLL_SECONDS (60s) for ordinary tasks. Raise it for
    # smart-gated tasks whose "not due yet" branch still costs a network probe:
    # with interval_days=1 and a 60s poll, such a task re-probes ~1440x/day even
    # though it can only ever do real work once a month.
    poll_seconds: int | None = None
    # Run immediately on startup when overdue.
    catchup: bool = True
    # Pause 30s after launching this thread (stagger DB-heavy syncs).
    stagger: bool = True
    # Minimum gap between two *attempts* of this task, in minutes. This is
    # deliberately separate from ``interval_days`` (the freshness cadence): a
    # task whose failure path writes no success marker would otherwise be retried
    # on every executor poll. Only enforced when set; defaults to no throttle.
    attempt_cooldown_minutes: int | None = None
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
        # Attempt throttle. The failure path writes no success marker, so without
        # this the executor would re-invoke ingestion on every 60s poll once the
        # after-close guard opens.
        attempt_cooldown_minutes=30,
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
    # Smart-gated: the task probes HEAD-only for a newly-published month and
    # no-ops (without marking) when there is nothing new, so the executor polls
    # and the real sync fires ~within a day of a new month landing rather than on
    # a fixed 30-day timer that always lagged (mark_on_success=False keeps the
    # gate in charge of sync_log marking).
    #
    # Because the no-op branch deliberately does NOT mark, interval_days cannot
    # throttle it — only the poll interval can. The upstream artifact is
    # monthly, so an hourly re-check is ample (~24h detection latency) versus
    # the previous 60s poll, which fired ~1440 wasted HEAD probes per day.
    "fund-traction-sync": TaskSpec(
        module="myra_app.tasks.fund_traction",
        label="fund_traction_sync",
        interval_days=1,
        mark_on_success=False,
        poll_seconds=3600,
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
    # Daily post-close gap-fill of the fundamentals table via the resilient
    # source layer. The weekday/18:00-IST gate lives inside run(); it returns
    # *without* marking before the gate so the executor retries later the same
    # day (mark_on_success=False keeps the gate in charge of sync_log marking).
    # Long poll: the off-hours no-op branch costs no network, and a 30-min
    # re-check is ample to catch the gate opening.
    "fundamentals-enrich": TaskSpec(
        module="myra_app.tasks.fundamentals_enrich",
        label="fundamentals_enrich",
        interval_days=1,
        catchup=True,
        stagger=True,
        mark_on_success=False,
        poll_seconds=1800,
    ),
    # Symbol-identity bridge (alias -> canonical) from local name matching.
    # Cheap and idempotent; weekly is plenty (the upstream artifact is monthly).
    "symbol-identity": TaskSpec(
        module="myra_app.tasks.symbol_identity",
        label="symbol_identity",
        interval_days=7,
    ),
    # Automated fund_traction symbol resolution (name-key -> canonical NSE
    # ticker) so the board can join price / market-cap. Local resolution is
    # cheap; the yfinance fallback is retry-bounded and only touches
    # still-unresolved keys. Hourly poll: nearly free when nothing is pending.
    "traction-symbols": TaskSpec(
        module="myra_app.tasks.traction_symbols",
        label="traction_symbols",
        interval_days=1,
        catchup=True,
        stagger=True,
        poll_seconds=3600,
    ),
    # Append-only point-in-time archive. Weekly is fine: it is a safety net
    # for history that is otherwise overwritten in place (rolling
    # fundamentals snapshot, year-less rolling traction file).
    "point-in-time-archive": TaskSpec(
        module="myra_app.tasks.archive",
        label="point_in_time_archive",
        interval_days=7,
    ),
}
