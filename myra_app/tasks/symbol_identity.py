"""Weekly symbol-identity bridge (Phase 2.5).

Populates ``symbol_alias`` (alias -> canonical NSE ticker) in
``myra_metadata.db`` from a deterministic, local source of truth: an exact
normalised-name match between ``fund_traction.symbol`` and ``symbols_master.name``.
See ``myra_app.symbol_identity`` for the matching rules and safety guarantees.

Single unit of work; scheduling is owned by ``myra_app.tasks.executor``.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)

SYNC_LABEL = "symbol_identity"


def run(ctx: TaskContext) -> None:
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Symbol identity", task_type="one-shot")
    try:
        from myra_app.symbol_identity import backfill_symbol_alias

        report = backfill_symbol_alias()
        logger.info(
            "[symbol_identity] aliases=%s written=%s ambiguous=%s (of %s fund_traction symbols)",
            report.get("aliases"),
            report.get("written"),
            report.get("ambiguous"),
            report.get("ft_symbols"),
        )
        _mark_task_run(SYNC_LABEL)
    except Exception as exc:
        logger.error("[symbol_identity] failed: %s", exc)
        raise
    finally:
        unregister(tid)
