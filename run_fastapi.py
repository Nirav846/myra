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

def run():
    # Disable reloader to avoid double-process issues on Windows
    config = uvicorn.Config(
        "myra_web.myra_fastapi_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,       # reload is unstable on Windows with signals
        log_level="info",
    )
    server = uvicorn.Server(config)

    def handle_exit(signum, frame):
        print("\n[FastAPI] Shutting down gracefully...")
        server.should_exit = True

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    server.run()

if __name__ == "__main__":
    run()
