"""Daily automated fund_traction symbol resolution (Phase 2.7).

Resolves each ``fund_traction.symbol`` (often a Trendlyne-style long name) to its
canonical NSE ticker and writes it back into ``fund_traction.nse`` so the Traction
Board can join price / market-cap data.  Local sources first (``symbol_alias``,
``symbols_master``, ``name_to_nse.csv``); a bounded, retry-limited yfinance
fallback covers the residual.  It never deletes or hides a stock -- an
unresolvable name simply keeps an empty ``nse`` and shows without market data.

See ``myra_app.traction_symbols`` for the resolution chain and safety guarantees.
Scheduling is owned by ``myra_app.tasks.executor``.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)

SYNC_LABEL = "traction_symbols"


def run(ctx: TaskContext) -> None:
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Traction symbols", task_type="one-shot")
    try:
        from myra_app.traction_symbols import backfill_traction_nse

        # Bound network work per run so a large unresolved backlog can never make
        # the task hang; the remainder is picked up on the next scheduled run.
        report = backfill_traction_nse(dry_run=False, max_probes=150)
        logger.info(
            "[traction_symbols] resolved=%s no_data=%s already=%s "
            "unresolved=%s parked=%s written=%s",
            report.get("resolved"),
            report.get("resolved_no_data"),
            report.get("already"),
            report.get("unresolved"),
            report.get("parked"),
            report.get("written"),
        )
        _mark_task_run(SYNC_LABEL)
    except Exception as exc:
        logger.error("[traction_symbols] failed: %s", exc)
        raise
    finally:
        unregister(tid)
