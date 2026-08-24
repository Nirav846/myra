"""Task 9: Weekly Screener.in Fundamentals Enrichment (PBV, ROCE)."""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _is_task_due, _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs Screener.in fundamentals enrichment weekly (PBV, ROCE)."""
    from myra_app.task_tracker import register, unregister

    tid = register("Screener enrich", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("screener_enrich", interval_days=7):
                    from myra_app.db.enrichers.screener_enricher import (
                        enrich_screener_fundamentals,
                    )

                    logger.info(
                        "[MYRA BG] Running weekly Screener.in fundamentals enrichment..."
                    )
                    # Auto-run disabled via SCREENER_ENRICH_AUTO_ENABLED=False;
                    # manual backfill still available with `--enrich-screener`.
                    # enrich_screener_fundamentals(force=False)
                    _mark_task_run("screener_enrich")
                    logger.info("[MYRA BG] Weekly Screener.in enrichment complete.")

                # Check again in 30 minutes
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if ctx.shutdown_event.wait(60):
                        return
            except Exception as e:
                logger.error(f"[MYRA BG] Weekly Screener enrichment failed: {e}")
                # Check again in 30 minutes
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if ctx.shutdown_event.wait(60):
                        return
    finally:
        unregister(tid)
