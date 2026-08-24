"""Task 11: Cross-buy Sync — monthly mutual-fund cross-buy analysis refresh."""

import logging
import subprocess
import sys
from pathlib import Path

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _is_task_due, _is_task_overdue, _mark_task_run

logger = logging.getLogger(__name__)

# Local sibling project holding raw RupeeVest MF holdings CSVs + downloader.
CROSS_FUND_REPO = Path(r"D:\01screener\Myra\cross-fund-holdings-traction")
CROSS_BUY_OUT_DIR = CROSS_FUND_REPO / "temp_holdings"
CROSS_BUY_FUNDS_FILE = CROSS_FUND_REPO / "config" / "rupeevest_funds.example.txt"
CROSS_BUY_DOWNLOAD_SCRIPT = CROSS_FUND_REPO / "scripts" / "download_rupeevest_funds.py"
CROSS_BUY_DOWNLOAD_TIMEOUT_S = 300  # 5 min — 62 funds x ~1s delay each


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
    cmd = [
        sys.executable,
        str(CROSS_BUY_DOWNLOAD_SCRIPT),
        "--funds-file",
        str(CROSS_BUY_FUNDS_FILE),
        "--out-dir",
        str(CROSS_BUY_OUT_DIR),
        "--overwrite",
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

    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip()[-500:]
        logger.warning(
            "[MYRA BG] Cross-buy CSV download failed (rc=%s): %s",
            result.returncode,
            stderr_tail,
        )
        return False

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


def run(ctx: TaskContext):
    """Monthly mutual-fund cross-buy sync. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    if _is_task_overdue("cross_buy_sync", days=30):
        tid = register("Cross-buy sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Cross-buy sync overdue – running now...")
            if not _download_cross_buy_csvs():
                logger.warning(
                    "[MYRA BG] Cross-buy sync skipped this run (download failed); "
                    "will retry on next orchestrator start"
                )
                return
            from myra_app.cross_buy_processor import (
                detect_available_months,
                process_month,
            )

            months = detect_available_months()
            if months:
                result = process_month(months[-1])  # latest month
                _mark_task_run("cross_buy_sync")
                logger.info(f"[MYRA BG] Cross-buy sync complete: {result}")
            else:
                logger.info(
                    "[MYRA BG] Cross-buy sync: no raw holdings months available, skipping"
                )
        except Exception as e:
            logger.error(f"[MYRA BG] Cross-buy sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if ctx.shutdown_event.is_set():
        return

    tid = register("Cross-buy sync", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("cross_buy_sync", interval_days=30):
                    logger.info("[MYRA BG] Cross-buy sync due – running...")
                    if not _download_cross_buy_csvs():
                        # Mark anyway so a dead downloader doesn't retry-storm
                        # every poll cycle; next attempt is the next 30-day cycle.
                        _mark_task_run("cross_buy_sync")
                        logger.warning(
                            "[MYRA BG] Cross-buy sync skipped (download failed); "
                            "next attempt in 30 days"
                        )
                        continue
                    from myra_app.cross_buy_processor import (
                        detect_available_months,
                        process_month,
                    )

                    months = detect_available_months()
                    if months:
                        result = process_month(months[-1])  # latest month
                        _mark_task_run("cross_buy_sync")
                        logger.info(f"[MYRA BG] Cross-buy sync complete: {result}")
                    else:
                        logger.info(
                            "[MYRA BG] Cross-buy sync: no raw holdings months available, skipping"
                        )
            except Exception as e:
                logger.error(f"[MYRA BG] Cross-buy sync failed: {e}")
            for _ in range(60):
                if ctx.shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)
