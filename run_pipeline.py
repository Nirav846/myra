# run_pipeline.py – stand‑alone MYRA data pipeline (fail‑proof, graceful shutdown)
import os
import sys
import signal
import time
import logging
import asyncio
from myra_app.db.enrichers.corporate_actions_enricher import enrich_corporate_actions
from myra_app.db.enrichers.screener_enricher import enrich_screener_fundamentals


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
        logger.info("Running fund traction sync (manual)...")
        from myra_app.fund_traction_sync import sync_fund_traction

        result = sync_fund_traction(force=True)
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

    orch.start()  # launches all daemon threads (ingest, syncs, watchdog)

    # Access the shutdown event that the orchestrator uses internally
    shutdown_event = orch._shutdown_event

    # ---------- Graceful shutdown handler ----------
    def handle_exit(signum=None, frame=None):
        logger.info("Received shutdown signal – stopping threads…")
        shutdown_event.set()
        # Give threads a moment to finish their current operation
        time.sleep(2)
        logger.info("Pipeline stopped cleanly.")
        sys.exit(0)

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
