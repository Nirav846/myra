"""
Health and system endpoints.

Extracted from myra_fastapi_server.py (Phase 6 of monolith refactor).
Read-only endpoints for system status, health checks, and metadata.
"""

import datetime
import json
import logging
import os
import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from myra_app.constants import DB_DIR, MODELS_DIR
from myra_app.librarian_core import LibrarianCore
from myra_web.utils import _get_latest_trading_day_before

logger = logging.getLogger(__name__)

try:
    from myra_app.background_orchestrator import _get_last_run
except ImportError:
    def _get_last_run(task_name):
        return "Never"

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check():
    """
    Checks if the databases in myra_app/db exist and can be connected to.
    The React UI polls this endpoint to update the green/yellow status lights in the sidebar.
    """
    canonical_to_frontend = {
        "technical": "_tech_conn",
        "meta": "_meta_conn",
        "valuation": "_val_conn",
        "institutional": "_inst_conn",
        "governance": "_gov_conn",
        "network_cache": "_cache_conn",
        "scoring": "_scoring_conn",
        "calendar": "_cal_conn",
    }
    health = {}
    for canonical_key, frontend_key in canonical_to_frontend.items():
        db_file = LibrarianCore.DB_MAP.get(canonical_key)
        if db_file:
            db_path = os.path.join(DB_DIR, db_file)
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    conn.execute("SELECT 1")
                    conn.close()
                    health[frontend_key] = {"connected": True, "path": db_path}
                except Exception:
                    health[frontend_key] = {"connected": False, "path": db_path}
            else:
                health[frontend_key] = {"connected": False, "path": None}
        else:
            health[frontend_key] = {"connected": False, "path": None}

    # Data coverage metrics for fundamentals table
    val_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    coverage = {"error": "not available"}
    if os.path.exists(val_path):
        try:
            conn = sqlite3.connect(val_path)
            total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
            coverage = {
                "total_symbols": total,
                "shares_outstanding": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE shares_outstanding IS NOT NULL"
                ).fetchone()[0],
                "insider_holding_pct": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE insider_holding_pct IS NOT NULL"
                ).fetchone()[0],
                "promoter_holding_pct": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE promoter_holding_pct IS NOT NULL"
                ).fetchone()[0],
                "industry": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE industry IS NOT NULL"
                ).fetchone()[0],
                "free_float_pct": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE free_float_pct IS NOT NULL"
                ).fetchone()[0],
            }
            conn.close()
        except Exception:
            coverage = {"error": "query failed"}
    return {"health": health, "coverage": coverage}


@router.get("/data-health")
async def data_health():
    import glob
    from datetime import timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))
    result = {
        "latest_ohlcv_date": None,
        "days_behind": None,
        "ohlcv_symbols_today": None,
        "enrichment_complete": None,
        "fundamentals_total": None,
        "fundamentals_with_promoter": None,
        "fundamentals_with_free_float": None,
        "nifty_benchmark_latest": None,
        "last_backup_date": "unknown",
        "scanner_cache_counts": {},
    }

    try:
        tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        if os.path.exists(tech_db):
            conn = sqlite3.connect(tech_db)
            try:
                row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
                result["latest_ohlcv_date"] = row[0] if row else None
                if result["latest_ohlcv_date"]:
                    latest = result["latest_ohlcv_date"]
                    ist_now = datetime.now(IST).date()
                    try:
                        latest_date = datetime.strptime(latest, "%Y-%m-%d").date()
                        result["days_behind"] = (ist_now - latest_date).days
                    except ValueError:
                        result["days_behind"] = None
                    cnt = conn.execute(
                        "SELECT COUNT(*) FROM technical_data WHERE date = ?",
                        (latest,),
                    ).fetchone()
                    result["ohlcv_symbols_today"] = cnt[0] if cnt else 0
                    enriched = conn.execute(
                        """SELECT COUNT(*) FROM technical_data
                           WHERE date = ?
                             AND delivery_divergence_score IS NOT NULL""",
                        (latest,),
                    ).fetchone()
                    total = result["ohlcv_symbols_today"] or 1

                    result["enrichment_complete"] = (
                        enriched[0] == total if enriched else False
                    )
                else:
                    result["ohlcv_symbols_today"] = 0
                    result["enrichment_complete"] = False
            except Exception:
                pass
            finally:
                conn.close()

        val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
        if os.path.exists(val_db):
            conn = sqlite3.connect(val_db)
            try:
                total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()
                result["fundamentals_total"] = total[0] if total else 0
                prom = conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE promoter_holding_pct > 0"
                ).fetchone()
                result["fundamentals_with_promoter"] = prom[0] if prom else 0
                ff = conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE free_float_pct > 0"
                ).fetchone()
                result["fundamentals_with_free_float"] = ff[0] if ff else 0
            except Exception:
                pass
            finally:
                conn.close()

        meta_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
        if os.path.exists(meta_db):
            conn = sqlite3.connect(meta_db)
            try:
                bm = conn.execute("SELECT MAX(date) FROM benchmarks").fetchone()
                result["nifty_benchmark_latest"] = bm[0] if bm else None
            except Exception:
                pass
            finally:
                conn.close()

        backup_dir = os.path.join(DB_DIR, "backups")
        if os.path.exists(backup_dir):
            try:
                files = [
                    f
                    for f in os.listdir(backup_dir)
                    if f.startswith("technical_") and f.endswith(".db")
                ]
                if files:
                    files.sort(reverse=True)
                    date_part = files[0].replace("technical_", "").replace(".db", "")
                    result["last_backup_date"] = date_part
            except Exception:
                pass

        scanner_names = [
            "invisible_hand",
            "trigger",
            "liquidity_flip",
            "operator_fingerprint",
            "float_exhaustion",
            "seasonal_delivery",
            "wyckoff",
        ]
        models_dir = MODELS_DIR
        for name in scanner_names:
            try:
                cache_path = os.path.join(models_dir, f"{name}_cache.json")
                if os.path.exists(cache_path):
                    with open(cache_path, encoding="utf-8") as f:
                        data = json.load(f)
                    candidates = data.get("candidates", [])
                    result["scanner_cache_counts"][name] = len(candidates)
                else:
                    result["scanner_cache_counts"][name] = 0
            except Exception:
                result["scanner_cache_counts"][name] = 0

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    return result


@router.get("/market-breadth")
async def get_market_breadth():
    """
    Return advances / declines for the latest trading date in technical_data.
    An advance = close > previous close; decline = close < previous close.
    """
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    try:
        with sqlite3.connect(tech_db) as conn:
            # Find latest date
            latest_row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
            latest = latest_row[0] if latest_row else None
            if not latest:
                return {"advances": 0, "declines": 0, "total": 0, "date": None}

            # We'll use a simple approach: fetch all symbols with their close on latest date
            # and the previous close from the previous trading day.
            prev_row = conn.execute(
                "SELECT MAX(date) FROM technical_data WHERE date < ?", (latest,)
            ).fetchone()
            prev_date = prev_row[0] if prev_row else None

            if not prev_date:
                return {"advances": 0, "declines": 0, "total": 0, "date": latest}

            # For each symbol, get close on latest date and prev close on prev_date
            rows = conn.execute(
                """
                SELECT a.symbol, a.close as close_today, b.close as close_prev
                FROM technical_data a
                JOIN technical_data b ON a.symbol = b.symbol AND b.date = ?
                WHERE a.date = ?
                  AND a.close > 0 AND b.close > 0
                """,
                (prev_date, latest),
            ).fetchall()

            advances = sum(1 for r in rows if r[1] > r[2])
            declines = sum(1 for r in rows if r[1] < r[2])
            total = advances + declines

            return {
                "advances": advances,
                "declines": declines,
                "total": total,
                "date": latest,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/db-size")
async def get_db_size():
    """Return size of the main technical database."""
    try:
        tech_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        size_mb = os.path.getsize(tech_path) / (1024 * 1024)
        return {"size_mb": round(size_mb, 1)}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Cannot read DB size")


@router.get("/system-info")
async def get_system_info():
    """Return CPU and memory usage (simple psutil)."""
    try:
        import psutil

        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        }
    except ImportError:
        return {"error": "psutil not installed"}


@router.get("/logs/recent")
async def get_recent_logs():
    """Return last 5 lines of pipeline.log or a placeholder."""
    log_path = os.path.join(os.path.dirname(DB_DIR), "pipeline.log")
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()[-5:]
        return {"logs": [l.strip() for l in lines]}
    except Exception:
        return {"logs": ["No log file found. Start the pipeline to populate."]}


@router.get("/latest-trading-day")
async def latest_trading_day():
    """Return today's date adjusted to the most recent available trading day."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return {"date": _get_latest_trading_day_before(today)}


@router.get("/tools/status")
async def get_pipeline_status():
    """Return last run times of all background tasks."""
    return {
        "fundamentals": (
            _get_last_run("fundamentals_sync")
            if "_get_last_run" in globals()
            else "Never"
        ),
        "etf": _get_last_run("etf_sync") if "_get_last_run" in globals() else "Never",
        "index": (
            _get_last_run("index_sync") if "_get_last_run" in globals() else "Never"
        ),
        "ingest": (
            _get_last_run("daily_ingest") if "_get_last_run" in globals() else "Never"
        ),
        "db_doctor": (
            _get_last_run("db_doctor") if "_get_last_run" in globals() else "Never"
        ),
    }


@router.get("/pcr/status")
async def pcr_status():
    """Read-only status of PCR snapshots stored in myra_options.db."""
    try:
        from myra_app.options_chain import get_all_pcr_snapshots

        snapshots = get_all_pcr_snapshots()
        if not snapshots:
            return {"status": "ok", "snapshots": [], "message": "no snapshots yet"}
        return {"status": "ok", "snapshots": snapshots}
    except Exception as exc:
        logger.warning("pcr_status failed: %s", exc)
        return {"status": "error", "snapshots": [], "message": str(exc)}