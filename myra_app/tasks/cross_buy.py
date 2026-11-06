"""Task 11: Cross-buy Sync — monthly mutual-fund cross-buy analysis refresh.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
A failed CSV download returns quietly; the executor's mark_on_failure=True
policy prevents retry storms (next attempt next 30-day cycle).
"""

import logging
import subprocess
import sys
from pathlib import Path

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)

# Local sibling project holding raw RupeeVest MF holdings CSVs + downloader.
CROSS_FUND_REPO = Path(r"D:\01screener\Myra\cross-fund-holdings-traction")
CROSS_BUY_OUT_DIR = CROSS_FUND_REPO / "temp_holdings"
CROSS_BUY_FUNDS_FILE = CROSS_FUND_REPO / "config" / "rupeevest_funds.example.txt"
CROSS_BUY_FUNDS_LOCAL = CROSS_FUND_REPO / "config" / "rupeevest_funds.txt"
CROSS_BUY_DOWNLOAD_SCRIPT = CROSS_FUND_REPO / "scripts" / "download_rupeevest_funds.py"
CROSS_BUY_DOWNLOAD_TIMEOUT_S = 300  # 5 min — 62 funds x ~1s delay each
# Mirrors the sibling workflow's policy: one renamed/transient fund must not
# abort the whole month. Anything above this still fails closed.
CROSS_BUY_MAX_FAILURES = 3


def _funds_file() -> Path:
    """The operator's real fund list, else the shipped template.

    ``rupeevest_funds.txt`` is the local (gitignored) list; the example file is
    the checked-in template this task has always passed. Prefer the real list
    when present so local edits take effect.
    """
    if CROSS_BUY_FUNDS_LOCAL.exists():
        return CROSS_BUY_FUNDS_LOCAL
    return CROSS_BUY_FUNDS_FILE


def _download_failures(stderr: str) -> tuple[str, list[str]]:
    """Split downloader stderr into its summary line and ``FAIL`` entries.

    The previous implementation logged only the last 500 characters, which
    truncated exactly the lines that identify the failing fund.
    """
    lines = (stderr or "").splitlines()
    done_line = next(
        (line.strip() for line in reversed(lines) if line.startswith("Done:")),
        "",
    )
    failures = [line.strip() for line in lines if line.startswith("FAIL ")]
    return done_line, failures


def _download_cross_buy_csvs() -> bool:
    """Refresh raw RupeeVest holdings CSVs via the sibling downloader script.

    Returns True when the download completed successfully. Never raises —
    all failure modes are logged and reported as False so the orchestrator
    task can skip the sync cleanly.
    """
    if not CROSS_BUY_DOWNLOAD_SCRIPT.exists():
        logger.error(
            "[MYRA BG] Cross-buy downloader script not found: %s",
            CROSS_BUY_DOWNLOAD_SCRIPT,
        )
        return False

    logger.info("[MYRA BG] Cross-buy CSV download starting...")
    funds_file = _funds_file()
    if not funds_file.exists():
        logger.error("[MYRA BG] Cross-buy funds file not found: %s", funds_file)
        return False
    logger.info("[MYRA BG] Cross-buy funds file: %s", funds_file)
    cmd = [
        sys.executable,
        str(CROSS_BUY_DOWNLOAD_SCRIPT),
        "--funds-file",
        str(funds_file),
        "--out-dir",
        str(CROSS_BUY_OUT_DIR),
        "--overwrite",
        "--max-failures",
        str(CROSS_BUY_MAX_FAILURES),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CROSS_BUY_DOWNLOAD_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[MYRA BG] Cross-buy CSV download timed out after %ss — skipping sync",
            CROSS_BUY_DOWNLOAD_TIMEOUT_S,
        )
        return False
    except FileNotFoundError as e:
        logger.error("[MYRA BG] Cross-buy download could not start: %s", e)
        return False
    except Exception as e:
        logger.exception("[MYRA BG] Cross-buy CSV download failed unexpectedly: %s", e)
        return False

    done_line, failures = _download_failures(result.stderr or "")

    if result.returncode != 0:
        logger.warning(
            "[MYRA BG] Cross-buy CSV download failed (rc=%s): %s | failed funds: %s",
            result.returncode,
            done_line or (result.stderr or "").strip()[-500:],
            "; ".join(failures[:5]) or "none parsed",
        )
        return False

    if failures:
        # Within the tolerated budget: the month still processes, but the miss
        # stays visible in the log instead of reading as a clean run.
        logger.warning(
            "[MYRA BG] Cross-buy CSV download completed with %d failed fund(s) "
            "(budget %d): %s",
            len(failures),
            CROSS_BUY_MAX_FAILURES,
            "; ".join(failures[:5]),
        )

    try:
        csv_count = sum(1 for p in CROSS_BUY_OUT_DIR.glob("*.csv") if p.is_file())
    except OSError:
        csv_count = -1
    logger.info(
        "[MYRA BG] Cross-buy CSV download finished OK (%s CSVs in %s)",
        csv_count,
        CROSS_BUY_OUT_DIR.name,
    )
    return True


class CrossBuyDownloadFailed(RuntimeError):
    """Raised so the executor applies mark_on_failure (no retry storms)."""


def run(ctx: TaskContext):
    """Runs one mutual-fund cross-buy sync. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Cross-buy sync", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Cross-buy sync running...")
        if not _download_cross_buy_csvs():
            # Signal failure to the executor — with mark_on_failure=True the
            # task still gets marked, so the next attempt is the next cycle.
            raise CrossBuyDownloadFailed("CSV download failed")

        from myra_app.cross_buy_processor import (
            detect_available_months,
            process_month,
        )

        months = detect_available_months()
        if months:
            result = process_month(months[-1])  # latest month
            # Audit fix: process_month returns success=False on parser/no-CSV
            # errors WITHOUT raising. Report explicitly so the dashboard doesn't
            # show a phantom success.
            if result.get("success") is False:
                _mark_task_run(
                    "cross_buy_sync",
                    status="failed",
                    error_message=(
                        result.get("error")
                        or result.get("reason")
                        or "process_month reported failure"
                    )[:500],
                )
            else:
                _mark_task_run("cross_buy_sync")
            logger.info(f"[MYRA BG] Cross-buy sync complete: {result}")
        else:
            logger.info(
                "[MYRA BG] Cross-buy sync: no raw holdings months available, skipping"
            )
    finally:
        unregister(tid)
