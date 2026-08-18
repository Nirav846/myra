"""
MYRA Launcher — one-click start for all three services.

Starts FastAPI backend, Vite frontend, and background pipeline.
Writes logs to logs/launcher/, waits for backend health, opens browser,
and handles Ctrl+C by killing all child processes cleanly.

Usage:
    python launch_myra.py              # start all services + open browser
    python launch_myra.py --no-browser # start all services, no browser
"""

import json
import logging
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
MYRA_WEB_DIR = PROJECT_ROOT / "myra_web"
LOG_DIR = PROJECT_ROOT / "logs" / "launcher"
PID_FILE = LOG_DIR / "pids.json"
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Use venv Python directly — run_fastapi.py calls os.execv() which kills
# the original process and breaks PID tracking. By passing the venv
# executable, we skip that re-launch.
VENV_PYTHON = PROJECT_ROOT / "pkscreener_env" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "launcher.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("launcher")


# ── Helpers ──────────────────────────────────────────────────────────────────
def is_port_in_use(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=1):
        return True
    return False


def start_process(cmd, cwd: Path, log_name: str) -> subprocess.Popen:
    log_file = open(LOG_DIR / log_name, "a", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    return proc


def save_pids(pids: dict) -> None:
    PID_FILE.write_text(json.dumps(pids), encoding="utf-8")


def load_pids() -> dict:
    if PID_FILE.exists():
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    return {}


def cleanup(processes: dict) -> None:
    pids = load_pids()
    for name, pid in pids.items():
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
            log.info(f"Killed {name} (PID {pid})")
        except Exception as exc:
            log.warning(f"Failed to kill {name} (PID {pid}): {exc}")
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def wait_for_backend_health(max_wait: int = 60) -> bool:
    url = "http://localhost:8000/api/health"
    elapsed = 0
    while elapsed < max_wait:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                log.info(f"Backend healthy after {elapsed}s")
                return True
        except Exception:
            pass
        if elapsed % 10 == 0 and elapsed > 0:
            log.info(f"Waiting for backend... ({elapsed}s)")
        time.sleep(2)
        elapsed += 2
    return False


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    no_browser = "--no-browser" in sys.argv

    # 1. Dependency checks
    npm_path = shutil.which("npm")
    if npm_path is None:
        log.error("npm not found on PATH. Install Node.js first.")
        sys.exit(1)
    log.info(f"npm: {npm_path}")

    # 2. Port checks
    for port in (8000, 3000):
        try:
            if is_port_in_use(port):
                log.error(
                    f"Port {port} already in use. "
                    "Run 'python stop_myra.py' first or stop the existing process."
                )
                sys.exit(1)
        except Exception:
            pass  # port is free (connection refused)

    log.info("Starting MYRA services...")

    # 3. Start backend
    backend = start_process(
        [PYTHON_EXE, "run_fastapi.py"],
        PROJECT_ROOT,
        "backend.log",
    )
    log.info(f"Backend started (PID {backend.pid})")

    # 4. Wait for backend health
    if not wait_for_backend_health():
        log.error("Backend failed to become healthy within 60s. Aborting.")
        cleanup({"backend": backend.pid})
        sys.exit(1)

    # 5. Start pipeline
    pipeline = start_process(
        [PYTHON_EXE, "run_pipeline.py"],
        PROJECT_ROOT,
        "pipeline.log",
    )
    log.info(f"Pipeline started (PID {pipeline.pid})")

    # 6. Start frontend
    frontend = start_process(
        [npm_path, "run", "dev"],
        MYRA_WEB_DIR,
        "frontend.log",
    )
    log.info(f"Frontend started (PID {frontend.pid})")

    # 7. Save PIDs
    processes = {
        "backend": backend,
        "frontend": frontend,
        "pipeline": pipeline,
    }
    save_pids({name: proc.pid for name, proc in processes.items()})

    # 8. Open browser
    if not no_browser:
        time.sleep(3)
        webbrowser.open("http://localhost:3000")

    # 9. Print status
    print()
    print("=" * 50)
    print("  MYRA is running")
    print()
    print("  Backend  : http://localhost:8000")
    print("  Frontend : http://localhost:3000")
    print("  API Docs : http://localhost:8000/docs")
    print()
    print("  Press Ctrl+C to stop all services.")
    print("=" * 50)
    print()

    # 10. Wait and watch
    try:
        while True:
            for name, proc in processes.items():
                if proc.poll() is not None:
                    log.error(
                        f"{name} exited unexpectedly with code {proc.returncode}"
                    )
                    cleanup({n: p.pid for n, p in processes.items()})
                    sys.exit(1)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nShutting down MYRA...")
        cleanup({n: p.pid for n, p in processes.items()})
        print("MYRA stopped.")


if __name__ == "__main__":
    main()
