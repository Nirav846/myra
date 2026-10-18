import sys, os

# ---- auto-activate virtual environment ----
VENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pkscreener_env")
VENV_PYTHON = os.path.join(VENV_PATH, "Scripts", "python.exe")
if os.path.exists(VENV_PYTHON) and sys.executable.lower() != VENV_PYTHON.lower():
    print(f"[venv] Re-launching with {VENV_PYTHON}")
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)
# -------------------------------------------

# run_fastapi.py – Clean shutdown launcher for MYRA FastAPI
import uvicorn
import signal
import sys
import threading
import time

from myra_web.access_log_filter import install_access_log_filter


def _signal_pipeline_shutdown_on_exit(server: "uvicorn.Server") -> None:
    """End long-lived pipeline SSE streams as soon as shutdown begins.

    uvicorn starts graceful shutdown by waiting for existing connections to
    finish *before* it runs the app lifespan teardown. The
    ``/api/pipeline/events`` SSE response streams indefinitely by design, so it
    never finishes on its own and uvicorn stalls on "Waiting for connections to
    close" (CTRL+C to force quit). Flipping the pipeline control plane the
    moment ``should_exit`` is set lets the SSE generator observe
    ``shutting_down()`` and return promptly.
    """

    def _watch() -> None:
        while not getattr(server, "should_exit", False):
            time.sleep(0.1)
        try:
            from myra_app.pipeline_control import control

            control.shutdown()
        except Exception as exc:  # noqa: BLE001 - shutdown must never block
            print(f"[FastAPI] Pipeline shutdown signal failed: {exc}")

    threading.Thread(target=_watch, name="myra-shutdown-watch", daemon=True).start()


def run():
    # This process owns the one-and-only background scheduler for the web flow,
    # so the frontend's start/pause/cancel controls actually reach it.
    os.environ.setdefault("MYRA_EMBED_SCHEDULER", "1")
    # Install before uvicorn builds its handlers so the readiness poll's ~400-char
    # `since` cursor never floods the access log. Idempotent, and the app module
    # installs it too -- whichever runs first wins.
    install_access_log_filter()
    # Disable reloader to avoid double-process issues on Windows
    config = uvicorn.Config(
        "myra_web.myra_fastapi_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # reload is unstable on Windows with signals
        log_level="info",
        # Safety net: never let a stuck connection block shutdown forever.
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)

    def handle_exit(signum, frame):
        print("\n[FastAPI] Shutting down gracefully...")
        server.should_exit = True

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    _signal_pipeline_shutdown_on_exit(server)
    server.run()


if __name__ == "__main__":
    run()
