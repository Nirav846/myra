"""Task: RupeeVest mutual-fund holdings sync.

Keeps ``mf_fund`` / ``mf_holding`` / ``mf_fund_aum`` (myra_valuation.db) fresh
so the Smart Money scanner can rank the *complete* held universe, including the
no-change holdings the published traction artifact drops.

Smart gate: funds disclose the previous calendar month progressively. Once every
watchlist fund has reported that month, there is nothing new until the next month
end, so the run no-ops (cheap local SQL, no network) and marks itself done.
Scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)

LABEL = "mf_holdings_sync"


def run(ctx: TaskContext):
    """Runs one incremental RupeeVest holdings sync. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    from myra_app.mf_holdings_sync import (
        load_fund_names,
        month_coverage,
        previous_month,
        sync_mf_holdings,
    )

    try:
        names = load_fund_names()
    except Exception as e:  # noqa: BLE001 - surface, never silently skip
        logger.error(f"[MYRA BG] MF holdings: cannot read fund list: {e}")
        raise

    target = previous_month()
    covered = month_coverage(target)
    if names and covered >= len(names):
        logger.info(
            f"[MYRA BG] MF holdings: {target} fully reported "
            f"({covered}/{len(names)} funds) — nothing to sync"
        )
        _mark_task_run(LABEL)
        return

    tid = register("MF holdings sync", task_type="one-shot")
    try:
        logger.info(
            f"[MYRA BG] MF holdings sync running... "
            f"(previous month {target}: {covered}/{len(names)} funds)"
        )
        result = sync_mf_holdings(dry_run=False)
        # sync_mf_holdings returns success=False on partial failure WITHOUT
        # raising; the executor cannot see that, so report it explicitly.
        if result.get("success") is False:
            _mark_task_run(
                LABEL,
                status="failed",
                error_message=(
                    result.get("error") or "sync_mf_holdings reported failure"
                )[:500],
            )
        else:
            _mark_task_run(LABEL)
        logger.info(
            f"[MYRA BG] MF holdings sync complete. "
            f"Funds: {result['funds_synced']}/{result['funds_seen']}, "
            f"Holdings rows: {result['holdings_rows']}, "
            f"AUM rows: {result['aum_rows']}, Months: {result['months']}"
        )
    except Exception as e:
        logger.error(f"[MYRA BG] MF holdings sync failed: {e}")
        raise
    finally:
        unregister(tid)
