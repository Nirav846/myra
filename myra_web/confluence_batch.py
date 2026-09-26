"""
Confluence batch runner — one same-date snapshot across every confluence scanner.

Why this exists
---------------
Historically ``build_confluence_report()`` read each scanner's *independently
written* per-scanner cache file. Those files are written whenever a user
happens to click Scan on that scanner's page, so their ``last_scan``
timestamps routinely span weeks (observed: 2026-06-03 → 2026-09-24).
``scanner_count`` was then computed by matching bare symbol strings across
those differently-dated result sets with no date comparison at all — so
"flagged by 6 scanners" could mean "each scanner flagged it at some point in
a 16-week window", not "6 scanners agree today". That is not a meaningful
claim and it is not backtestable.

The fix is a real batch run: every scanner is executed against the *same*
``as_on_date`` and the results are persisted as a single snapshot, which is
the only thing confluence reports from.

Design notes
------------
* Scanner classes are resolved from a ``module:Class`` string *inside* the
  per-scanner try/except rather than imported at module scope. A top-level
  import would mean one broken scanner module breaks the whole batch at
  import time, defeating the "one bad scanner must not abort the batch"
  requirement. Resolution is therefore lazy and isolated.
* ``default_kwargs`` is empty for every scanner: each class is constructed
  with its own ``__init__`` defaults. No thresholds are invented here.
* The run is intentionally sequential, mirroring ``scan_all`` in
  ``screener.py``. Parallelising is out of scope; SQLite readers plus the
  shared DuckDB engine make it a non-trivial change, not a free win.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import date as _date
from datetime import datetime
from typing import Any, NamedTuple

from myra_app.constants import DB_DIR, MODELS_DIR
from myra_web.utils import (
    CONFLUENCE_SNAPSHOT_FILENAME,
    _get_latest_trading_day_before,
    compute_scanner_breadth,
)

logger = logging.getLogger(__name__)


class ScannerSpec(NamedTuple):
    """One confluence scanner.

    ``target`` is a lazy ``"module.path:ClassName"`` import spec (see module
    docstring for why it is not a real imported class object). ``custom`` is
    an optional runner for scanners that do not expose the uniform
    ``scan(as_on_date)`` interface; when absent the standard path is used.
    """

    name: str
    target: str
    kwargs: dict[str, Any] = {}
    custom: Any = None


def _resolve(target: str):
    """Import a ``"module.path:ClassName"`` spec and return the class."""
    module_path, _, cls_name = target.partition(":")
    import importlib

    return getattr(importlib.import_module(module_path), cls_name)


def _json_safe(obj):
    """Recursively coerce a payload into strict-JSON-safe primitives.

    Scanners can hand back numpy scalars, NaN/Inf, Timestamps and nested
    containers; json.dump would otherwise emit non-standard NaN/Infinity
    tokens that a strict JSON parser rejects on read.
    """
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (str, bool, int)) or obj is None:
        return obj
    if hasattr(obj, "item"):
        try:
            return _json_safe(obj.item())
        except Exception:
            return str(obj)
    return str(obj)


def _normalize(result: Any) -> list[dict]:
    """Coerce a scanner return value into a plain list of dicts.

    Most scanners return ``list[dict]``; several return a pandas DataFrame.
    NaN/inf floats are mapped to None so the snapshot stays strict JSON.
    """
    if result is None:
        return []
    if hasattr(result, "to_dict") and hasattr(result, "columns"):
        result = result.to_dict("records")
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return []
    out: list[dict] = []
    for row in result:
        if not isinstance(row, dict):
            continue
        clean: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif hasattr(v, "item") and not isinstance(v, (str, bytes)):
                # numpy scalars
                try:
                    clean[k] = v.item()
                except Exception:
                    clean[k] = str(v)
            else:
                clean[k] = v
        out.append(clean)
    return out


def _multibagger_universe(val_conn, min_mcap: int, max_mcap: int, as_on: str):
    """Market-cap filtered universe, point-in-time bounded by ``as_on``.

    Mirrors _multibagger_universe in routes/scanners.py but adds the
    ``date <= as_on`` bound: the original always takes MAX(date), which is a
    look-ahead whenever as_on is a historical date.
    """
    rows = val_conn.execute(
        """
        SELECT f.symbol
        FROM fundamentals f
        INNER JOIN (
            SELECT symbol, MAX(date) AS max_date
            FROM fundamentals
            WHERE COALESCE(market_cap, 0) > 0
              AND date <= ?
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
        WHERE COALESCE(f.market_cap, 0) / 10000000.0 BETWEEN ? AND ?
        ORDER BY f.symbol
        """,
        (as_on, min_mcap, max_mcap),
    ).fetchall()
    return [r[0] for r in rows]


def _run_multibagger(scanner_cls, as_on: str, **kwargs) -> list[dict]:
    """Multibagger has no ``scan(as_on_date)``; it needs a prebuilt frame.

    The upstream route (routes/scanners.py) builds the universe and per-symbol
    frames itself and calls ``Strategy.run(df, funda)``. This mirrors that
    logic, except the technical window and fundamentals lookup are bounded by
    ``as_on`` so the result is genuinely point-in-time.
    """
    import pandas as pd

    lookback = 42
    min_mcap, max_mcap = 200, 50000

    val_path = os.path.join(DB_DIR, "myra_valuation.db")
    tech_path = os.path.join(DB_DIR, "myra_technical.db")

    val_conn = sqlite3.connect(val_path)
    try:
        symbols = _multibagger_universe(val_conn, min_mcap, max_mcap, as_on)
    finally:
        val_conn.close()
    if not symbols:
        return []

    scanner = scanner_cls()
    candidates: list[dict] = []
    tech_conn = sqlite3.connect(tech_path)
    val_conn2 = sqlite3.connect(val_path)
    try:
        funda_cols = [
            c[0]
            for c in val_conn2.execute("PRAGMA table_info(fundamentals)").fetchall()
        ]
        for sym in symbols:
            df = pd.read_sql(
                "SELECT date, open, high, low, close, volume FROM technical_data "
                "WHERE symbol=? AND date<=? "
                "ORDER BY date DESC LIMIT ?",
                tech_conn,
                params=(sym, as_on, lookback + 30),
            )
            if df.empty or len(df) < 30:
                continue
            df = df.iloc[::-1].reset_index(drop=True)

            row = val_conn2.execute(
                "SELECT * FROM fundamentals WHERE symbol=? AND date<=? "
                "ORDER BY date DESC LIMIT 1",
                (sym, as_on),
            ).fetchone()
            funda = dict(zip(funda_cols, row)) if row else {}

            try:
                result = scanner.run(df, funda)
                if result and result.get("signal"):
                    result["symbol"] = sym
                    candidates.append(result)
            except Exception:
                # per-symbol isolation, matching the upstream route
                continue
    finally:
        tech_conn.close()
        val_conn2.close()
    return candidates


# Display names and classes mirror _SCANNER_CACHE_MAP in myra_web/utils.py.
# Launchpad is intentionally absent: its results are in-memory only and the
# on-disk launchpad_scan_cache.json is a frozen orphan that no code path
# writes, so it can never produce a same-date result.
CONFLUENCE_SCANNERS: list[ScannerSpec] = [
    ScannerSpec("The Trigger", "myra_app.strategies.trigger_scanner:TriggerScanner"),
    ScannerSpec("Bottom Hunter", "myra_app.strategies.bottom_hunter:BottomHunter"),
    ScannerSpec(
        "Recovery Ladder",
        "myra_app.strategies.bottom_hunter_m1_scanner:BottomHunterM1Scanner",
    ),
    ScannerSpec(
        "Super Breakout",
        "myra_app.strategies.super_breakout_scanner:SuperBreakoutScanner",
    ),
    ScannerSpec(
        "Invisible Hand",
        "myra_app.strategies.invisible_hand_scanner:InvisibleHandScanner",
    ),
    ScannerSpec(
        "Wyckoff Automaton", "myra_app.strategies.wyckoff_automaton:WyckoffAutomaton"
    ),
    ScannerSpec(
        "Liquidity Flip",
        "myra_app.strategies.liquidity_flip_detector:LiquidityFlipDetector",
    ),
    ScannerSpec(
        "Operator Fingerprint",
        "myra_app.strategies.operator_fingerprint_scanner:OperatorFingerprintScanner",
    ),
    ScannerSpec(
        "Float Exhaustion",
        "myra_app.strategies.float_exhaustion_scanner:FloatExhaustionScanner",
    ),
    ScannerSpec(
        "Seasonal Delivery",
        "myra_app.strategies.seasonal_delivery_harvester:SeasonalDeliveryHarvester",
    ),
    ScannerSpec(
        "Darvas Box Pro", "myra_app.strategies.darvas_box_scanner:DarvasBoxScanner"
    ),
    ScannerSpec(
        "Climax Accumulation",
        "myra_app.strategies.climax_accumulation:ClimaxAccumulationScanner",
    ),
    ScannerSpec(
        "Multibagger Pro",
        "myra_app.strategies.multibagger_early_detection:Strategy",
        {},
        _run_multibagger,
    ),
]


def run_confluence_batch(as_on_date: str | None = None) -> dict:
    """Run every confluence scanner against the SAME as_on_date and persist one
    combined snapshot to ``MODELS_DIR/confluence_snapshot.json``.

    Each scanner is wrapped in its own try/except: a failure records
    ``{"candidates": [], "error": "<message>"}`` for that scanner and the
    batch continues, so the snapshot always completes.

    Returns a summary: ``as_on_date``, ``generated_at``, and a per-scanner
    ``{status, count, error}`` breakdown.
    """
    if as_on_date is None:
        as_on_date = _date.today().strftime("%Y-%m-%d")
    as_on = _get_latest_trading_day_before(as_on_date)

    snapshot: dict[str, dict] = {}
    summary: dict[str, dict] = {}
    started = time.time()

    for spec in CONFLUENCE_SCANNERS:
        t0 = time.time()
        try:
            scanner_cls = _resolve(spec.target)
            if spec.custom is not None:
                cands = spec.custom(scanner_cls, as_on, **spec.kwargs)
            else:
                scanner = scanner_cls(**spec.kwargs)
                scan_fn = getattr(scanner, "scan", None)
                if not callable(scan_fn):
                    raise AttributeError(
                        f"{spec.name} ({spec.target}) has no callable scan()"
                    )
                cands = _normalize(scan_fn(as_on_date=as_on))
            cands = _normalize(cands)
            snapshot[spec.name] = {"candidates": cands, "error": None}
            summary[spec.name] = {
                "status": "ok",
                "count": len(cands),
                "error": None,
                "seconds": round(time.time() - t0, 1),
            }
            logger.info(
                "confluence batch: %s -> %d candidates (%.1fs)",
                spec.name,
                len(cands),
                time.time() - t0,
            )
        except Exception as e:  # noqa: BLE001 - one scanner must not kill the batch
            err = f"{type(e).__name__}: {e}"
            logger.error("confluence batch: %s FAILED — %s", spec.name, err)
            snapshot[spec.name] = {"candidates": [], "error": err}
            summary[spec.name] = {
                "status": "error",
                "count": 0,
                "error": err,
                "seconds": round(time.time() - t0, 1),
            }

    # Persist each scanner's coverage so the report can split selective from
    # broad signals without recomputing. Needs every scanner's candidate list,
    # so it can only be done once the whole loop has finished.
    breadth = compute_scanner_breadth(snapshot)
    for name, entry in snapshot.items():
        entry["pct_of_universe"] = breadth.get(name, 0.0)

    payload = {
        "as_on_date": as_on,
        "generated_at": datetime.now().isoformat(),
        "scanners": snapshot,
    }

    out_path = os.path.join(MODELS_DIR, CONFLUENCE_SNAPSHOT_FILENAME)
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_json_safe(payload), f, indent=2)
    os.replace(tmp_path, out_path)

    ok = sum(1 for v in summary.values() if v["status"] == "ok")
    return {
        "as_on_date": as_on,
        "generated_at": payload["generated_at"],
        "scanners_ok": ok,
        "scanners_failed": len(summary) - ok,
        "total_seconds": round(time.time() - started, 1),
        "scanners": summary,
        "snapshot_path": out_path,
    }


# --- refresh lock, mirroring the (lock + scan_status == "scanning") pattern
# --- used by register_scanner's _scan_handler in routes/scanners.py
_confluence_lock = threading.Lock()
_confluence_state: dict = {"scan_status": "idle", "message": "", "last_result": None}
