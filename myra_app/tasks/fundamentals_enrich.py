"""Daily post-close fundamentals enrichment (Phase 2.3).

Fills gaps in the ``fundamentals`` table (ROE, free float, promoter holding,
shares, market cap, ...) using the resilient, cost-aware source layer in
``myra_app.enrichment_sources`` and writes only through ``safe_write`` (fill-only,
zero-guarded), so a failed/partial fetch can never erase a valid metric.

Scheduling is owned by ``myra_app.tasks.executor`` (registry entry
``fundamentals-enrich``).  The task keeps an internal weekday/18:00-IST gate:
before the gate it returns **without marking**, so the executor simply retries
later the same day rather than consuming the day's only attempt.

Bounded and idempotent: at most ``FUNDAMENTALS_ENRICH_BATCH_LIMIT`` symbols per
run, highest market cap first, so the most visible names fill first and already
complete rows drop out on the next run.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Iterable, Optional

from myra_app.constants import DB_DIR, DISABLE_FUNDAMENTAL_WRITERS
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _is_task_overdue, _mark_task_run, now_ist

logger = logging.getLogger(__name__)

SYNC_LABEL = "fundamentals_enrich"
ALIAS_LABEL = "symbol_alias_backfill"

#: Post-close gate (IST): weekdays at/after 18:00.
MIN_HOUR_IST = 18


def _batch_limit() -> int:
    try:
        return max(1, int(os.environ.get("FUNDAMENTALS_ENRICH_BATCH_LIMIT", "200")))
    except (TypeError, ValueError):
        return 200


def _select_symbols(conn: sqlite3.Connection, limit: int) -> list[str]:
    """Symbols with a gap in a core metric, largest market cap first.

    ``ORDER BY (market_cap IS NULL), market_cap DESC`` keeps rows that *have* a
    market cap first (so the visible names are enriched first) and only then the
    null-cap rows, instead of letting NULLs push them to the back silently.
    """
    from myra_app.symbols import NON_EQUITY_SQL

    rows = conn.execute(
        f"""
        SELECT symbol FROM fundamentals
        WHERE ( roe IS NULL
             OR free_float_pct IS NULL
             OR promoter_holding_pct IS NULL
             OR shares_outstanding IS NULL
             OR market_cap IS NULL )
          AND NOT {NON_EQUITY_SQL}
        ORDER BY (market_cap IS NULL), market_cap DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def enrich_symbols(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    ctx: Optional[TaskContext] = None,
    *,
    registry=None,
    cache=None,
    rate_limiters=None,
    with_health: bool = True,
) -> dict:
    """Run the enrichment batch against ``conn``. Split out for unit testing."""
    from myra_app import enrichment_sources as es
    from myra_app.data_sources.cache import TtlCache

    registry = registry or es.default_registry()
    if with_health:
        es.load_health(registry)
    cache = cache or TtlCache()
    rate_limiters = rate_limiters or es.default_rate_limiters()
    shutdown = ctx.shutdown_event if ctx is not None else None
    pause = ctx.pause_event if ctx is not None else None

    symbols = list(symbols)
    total = len(symbols)

    def _progress(done: int, tot: int, report: dict) -> None:
        if done % 25 == 0 or done == tot:
            logger.info(
                "[fundamentals_enrich] %d/%d enriched (last=%s)",
                done,
                tot,
                report.get("symbol"),
            )

    try:
        summary = es.enrich_batch(
            symbols,
            conn,
            dry_run=False,
            cancel_event=shutdown,
            pause_event=pause,
            progress_cb=_progress,
            registry=registry,
            cache=cache,
            rate_limiters=rate_limiters,
        )
    finally:
        if with_health:
            es.persist_health(registry)
    summary["selected"] = total
    return summary


def run(ctx: TaskContext) -> None:
    """Scheduled entrypoint — one bounded, post-close enrichment pass."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    # DISABLE_FUNDAMENTAL_WRITERS: out-of-repo writer owns the fundamentals table.
    if DISABLE_FUNDAMENTAL_WRITERS:
        logger.info(
            "[fundamentals_enrich] skipped: DISABLE_FUNDAMENTAL_WRITERS=True "
            "(external writer owns fundamentals)"
        )
        return

    ist_now = now_ist()
    if ist_now.weekday() >= 5 or ist_now.hour < MIN_HOUR_IST:
        # Deliberately NOT marked: the executor retries later the same day until
        # the gate opens (registry mark_on_success=False).
        logger.debug(
            "[fundamentals_enrich] off-hours (weekday=%s, hour=%s) — waiting",
            ist_now.weekday(),
            ist_now.hour,
        )
        return

    tid = register("Fundamentals enrich", task_type="one-shot")
    try:
        conn = sqlite3.connect(os.path.join(DB_DIR, "myra_valuation.db"), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            symbols = _select_symbols(conn, _batch_limit())
            if not symbols:
                logger.info("[fundamentals_enrich] nothing to enrich")
                _mark_task_run(SYNC_LABEL)
                return
            logger.info("[fundamentals_enrich] enriching %d symbols", len(symbols))
            summary = enrich_symbols(conn, symbols, ctx)
            logger.info(
                "[fundamentals_enrich] done: selected=%s written=%s resolved=%s "
                "failed=%s",
                summary.get("selected"),
                summary.get("written"),
                summary.get("resolved"),
                summary.get("failed"),
            )
            _mark_task_run(SYNC_LABEL)

            # Symbol identity is cheap, idempotent and unrelated to the daily
            # cadence — run it at most weekly.
            if _is_task_overdue(ALIAS_LABEL, days=7):
                _run_alias_backfill(conn)
        finally:
            conn.close()
    except Exception as exc:
        logger.error("[fundamentals_enrich] failed: %s", exc)
        raise
    finally:
        unregister(tid)


def _run_alias_backfill(valuation_conn: sqlite3.Connection) -> None:
    """Populate ``symbol_alias`` from local name matching (best-effort)."""
    from myra_app.symbol_identity import backfill_symbol_alias

    try:
        report = backfill_symbol_alias()
        logger.info(
            "[symbol_alias_backfill] aliases=%s written=%s ambiguous=%s",
            report.get("aliases"),
            report.get("written"),
            report.get("ambiguous"),
        )
        _mark_task_run(ALIAS_LABEL)
    except Exception as exc:  # noqa: BLE001 - identity is a nice-to-have
        logger.warning("[symbol_alias_backfill] skipped: %s", exc)
