# run_pipeline.py – headless MYRA data pipeline + manual CLI enrichers.
#
# The web flow no longer needs this process: run_fastapi.py owns the scheduler
# (see myra_web/myra_fastapi_server.py lifespan). This script remains for
# headless use and one-shot manual commands (--enrich-ca, --sync-fund-traction,
# ...). It refuses to start a *second* scheduler when one already runs.
import os
import sys

# ---- auto-activate virtual environment (must precede myra_app imports) ----
VENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pkscreener_env")
VENV_PYTHON = os.path.join(VENV_PATH, "Scripts", "python.exe")
if os.path.exists(VENV_PYTHON) and sys.executable.lower() != VENV_PYTHON.lower():
    print(f"[venv] Re-launching with {VENV_PYTHON}")
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)
# ---------------------------------------------------------------------------

import logging  # noqa: E402
import signal  # noqa: E402
import time  # noqa: E402
import asyncio  # noqa: E402
from myra_app.db.enrichers.corporate_actions_enricher import (  # noqa: E402
    enrich_corporate_actions,
)
from myra_app.db.enrichers.screener_enricher import (  # noqa: E402
    enrich_screener_fundamentals,
)


def main():
    # Anchor project root
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-18s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("pipeline")
    if "--enrich-ca" in sys.argv:
        logger.info("Running corporate actions enricher for manual backfill...")
        enrich_corporate_actions(force=True, days_back=365)
        logger.info("Corporate actions enricher completed.")

    if "--enrich-screener" in sys.argv:
        logger.info("Running Screener.in fundamentals enricher...")
        enrich_screener_fundamentals(force=True)
        logger.info("Screener.in fundamentals enricher completed.")

    if "--sync-fund-traction" in sys.argv:
        # Smart by default: only sync when a newly-published month is available
        # upstream but missing locally (cheap HEAD-only probe). Pass --force to
        # re-download everything regardless.
        force = "--force" in sys.argv
        from myra_app.fund_traction_sync import has_unsynced_month, sync_fund_traction

        if force:
            logger.info("Running fund traction sync (manual, force)...")
        else:
            pending = has_unsynced_month()
            if not pending:
                logger.info(
                    "Fund traction sync skipped: no new month upstream yet "
                    "(use --force to re-sync anyway)."
                )
                return  # Exit after manual sync, don't start daemon
            logger.info(
                f"Running fund traction sync (manual) — new month(s): {pending}..."
            )
        result = sync_fund_traction(force=force)
        logger.info(f"Fund traction sync complete: {result}")
        return  # Exit after manual sync, don't start daemon

    if "--sync-cross-buy" in sys.argv:
        logger.info("Running cross-buy sync (manual)...")
        from myra_app.cross_buy_processor import backfill_months

        result = backfill_months()
        logger.info(f"Cross-buy sync complete: {result}")
        return  # Exit after manual sync

    if "--backfill-bse" in sys.argv:
        logger.info("Running BSE shareholding backfill...")
        from myra_app.utils.bse_shareholding import backfill_shareholding

        # Parse optional --limit argument: python run_pipeline.py --backfill-bse --limit 100
        limit = None
        if "--limit" in sys.argv:
            idx = sys.argv.index("--limit")
            if idx + 1 < len(sys.argv):
                try:
                    limit = int(sys.argv[idx + 1])
                    logger.info("Backfill limited to %d symbols", limit)
                except ValueError:
                    logger.warning(
                        "Invalid --limit value, ignoring. Backfilling all symbols."
                    )

        asyncio.run(backfill_shareholding(max_symbols=limit))
        logger.info("BSE shareholding backfill complete.")

        # Also sync free-float + shares_outstanding via yfinance so that
        # free_float_pct / free_float_market_cap are populated alongside
        # promoter/public holding.
        logger.info("Running shareholding + free-float sync (yfinance)...")
        from tools.sync_market_cap import sync_shareholding_and_float

        sync_shareholding_and_float(limit=limit)
        logger.info("Shareholding + free-float sync complete.")
        return  # Exit after manual backfill

    logger.info("Starting MYRA data pipeline (headless, crash‑safe)…")

    # Import the orchestrator module and start all background tasks
    import myra_app.background_orchestrator as orch

    # Single-scheduler guard: if the web/API process (or another copy of this
    # script) already owns the scheduler, decline rather than run a duplicate.
    if not orch.start():  # launches all daemon threads (ingest, syncs, watchdog)
        logger.warning(
            "Another MYRA scheduler is already running — exiting to avoid a "
            "duplicate. (The web flow is owned by run_fastapi.py.)"
        )
        return

    # Access the shutdown event that the orchestrator uses internally
    shutdown_event = orch._shutdown_event

    # ---------- Graceful shutdown handler ----------
    def handle_exit(signum=None, frame=None):
        # Delegate to the orchestrator so threads are *joined* and the WAL is
        # checkpointed before exit (a bare sys.exit here would skip both).
        logger.info("Received shutdown signal – stopping threads…")
        orch.request_shutdown()
        logger.info("Pipeline stopped cleanly.")

    signal.signal(signal.SIGINT, handle_exit)  # Ctrl+C
    signal.signal(signal.SIGTERM, handle_exit)  # kill (non‑forced)

    # Keep alive until shutdown event is set
    try:
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        handle_exit()


if __name__ == "__main__":
    main()
