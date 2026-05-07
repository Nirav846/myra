# run_pipeline.py – stand‑alone MYRA data pipeline (fail‑proof, graceful shutdown)
import os
import sys
import signal
import time
import logging

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

    logger.info("Starting MYRA data pipeline (headless, crash‑safe)…")

    # Import the orchestrator module and start all background tasks
    import myra_app.background_orchestrator as orch
    orch.start()   # launches all daemon threads (ingest, syncs, watchdog)

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

    signal.signal(signal.SIGINT, handle_exit)   # Ctrl+C
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