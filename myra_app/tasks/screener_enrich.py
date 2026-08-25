"""Task 9: Weekly Screener.in Fundamentals Enrichment (PBV, ROCE).

Single unit of work: scheduling is owned by myra_app.tasks.executor.
Auto-run is disabled via the registry (enabled=False); manual backfill
remains available with `--enrich-screener`.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Marks the Screener.in enrichment cadence (auto-run intentionally off)."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Screener enrich", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Screener.in fundamentals enrichment tick (no-op)...")
        # Auto-run disabled via SCREENER_ENRICH_AUTO_ENABLED=False;
        # manual backfill still available with `--enrich-screener`.
        # from myra_app.db.enrichers.screener_enricher import (
        #     enrich_screener_fundamentals,
        # )
        # enrich_screener_fundamentals(force=False)
        _mark_task_run("screener_enrich")
        logger.info("[MYRA BG] Screener.in enrichment tick complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] Weekly Screener enrichment failed: {e}")
        raise
    finally:
        unregister(tid)
