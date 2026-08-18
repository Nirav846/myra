"""
MYRA Stop Script — cleanly stop all MYRA services.

Reads PID files written by launch_myra.py and kills each process tree.
Handles stale PIDs gracefully.

Usage:
    python stop_myra.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PID_FILE = PROJECT_ROOT / "logs" / "launcher" / "pids.json"


def is_alive(pid: int) -> bool:
    """Check if a process is alive (Windows-safe)."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def main() -> None:
    if not PID_FILE.exists():
        print("MYRA is not running (no PID file found).")
        return

    try:
        pids = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading PID file: {exc}")
        return

    if not pids:
        print("MYRA is not running (empty PID file).")
        PID_FILE.unlink(missing_ok=True)
        return

    killed = 0
    for name, pid in pids.items():
        if is_alive(pid):
            try:
                result = subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    print(f"  Stopped {name} (PID {pid})")
                    killed += 1
                else:
                    print(f"  Failed to stop {name} (PID {pid}): {result.stderr.strip()}")
            except Exception as exc:
                print(f"  Error stopping {name} (PID {pid}): {exc}")
        else:
            print(f"  {name} (PID {pid}) is already stopped (stale)")

    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    if killed > 0:
        print(f"\nMYRA stopped ({killed} service{'s' if killed > 1 else ''} terminated).")
    else:
        print("\nMYRA was not running (all PIDs were stale).")


if __name__ == "__main__":
    main()
