"""
MYRA Scanners Router — generated via a factory pattern.

Extracted from myra_fastapi_server.py (Phase 7 of monolith refactor).

The repetitive per-scanner machinery (state dict, lock, JSON cache, status and
scan endpoints with progress tracking) is implemented ONCE in
``register_scanner`` and driven by per-scanner config. Launchpad and
Multibagger do not fit the standard pattern (in-memory predictions/global
result, no cache file) and are registered explicitly.
"""

import copy
import json
import logging
import os
import threading
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException

from myra_app.constants import MODELS_DIR
from myra_web.utils import _apply_tier_rank, _df_to_safe_records, _get_latest_trading_day_before

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["scanners"])

# Kebab-case route name -> (state dict, lock) for cache-delete resets.
_RESET_STATES: dict[str, tuple[dict, threading.Lock]] = {}


def _save_cache(state: dict, lock: threading.Lock, cache_path: str, label: str):
    """Persist scanner state to its JSON cache file (mirrors original)."""
    try:
        os.makedirs("models", exist_ok=True)
        with lock:
            data = {
                "last_scan": state["last_scan"],
                "candidates": state["candidates"],
                "message": state["message"],
            }
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"{label} cache save failed: {e}")


def _load_cache(cache_path: str, label: str) -> dict | None:
    """Load cached scanner results, or None if unavailable (mirrors original)."""
    try:
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"{label} cache load failed: {e}")
    return None


def register_scanner(
    name: str,
    *,
    state_template: dict,
    cache_file: str | None,
    parse_payload,
    build_scanner,
    scan_as_of: bool = True,
    result_mode: str = "df",  # "df" -> _df_to_safe_records | "list" -> inline NaN sanitize
    progress_attr: str = "_get_tech_data",
    tracked_kwargs: bool = False,
    status_extra: str | None = None,  # "bear_market" | "scanned_date" | None
    status_post_process=None,  # applied to cached candidates before status response (e.g. tier rank)
    post_process=None,
    tier_rank: bool = False,
    init_message: str = "Initialising scanner...",
    label: str | None = None,
):
    """Create state/lock/cache and register status + scan endpoints for a
    standard scanner following the shared state-machine pattern."""
    label = label or name
    state: dict = copy.deepcopy(state_template)
    lock = threading.Lock()
    cache_path = os.path.join(MODELS_DIR, cache_file) if cache_file else None
    _RESET_STATES[name] = (state, lock)

    def _status_handler():
        with lock:
            snapshot = copy.deepcopy(state)
        if snapshot["scan_status"] == "idle":
            cache = _load_cache(cache_path, label) if cache_path else None
            if cache and cache.get("candidates") is not None:
                resp = {
                    "scan_status": "idle",
                    "last_scan": cache.get("last_scan"),
                    "progress": 100,
                    "message": cache.get(
                        "message", f"Found {len(cache['candidates'])} candidates."
                    ),
                    "candidates": list(cache["candidates"]),
                }
                if status_post_process:
                    resp["candidates"] = status_post_process(resp["candidates"])
                if status_extra == "bear_market":
                    resp["bear_market"] = snapshot.get("bear_market", False)
                elif status_extra == "scanned_date":
                    resp["scanned_date"] = None
                return resp
        return snapshot

    async def _scan_handler(payload: dict = Body(default={})):
        scanner_kwargs, scan_date = parse_payload(payload)

        with lock:
            if state["scan_status"] == "scanning":
                return {"detail": "Scan already in progress"}, 409
            state.update(
                {
                    "scan_status": "scanning",
                    "progress": 0,
                    "message": init_message,
                    "candidates": [],
                    "scanned_date": scan_date,
                }
            )

        def _run():
            try:
                scanner = build_scanner(scanner_kwargs, scan_date)

                state["message"] = "Loading universe..."
                state["progress"] = 5
                universe = scanner._get_universe()
                total = max(len(universe), 1)
                state["message"] = f"Scanning {total} symbols..."
                state["progress"] = 10

                original_get_tech = getattr(scanner, progress_attr)
                processed = [0]

                def _tracked_get_tech(symbol, min_date=None, max_date=None):
                    processed[0] += 1
                    if processed[0] % 25 == 0:
                        pct = 10 + int((processed[0] / total) * 82)
                        state["progress"] = min(pct, 92)
                        state["message"] = f"Scanning {processed[0]}/{total} symbols..."
                    if tracked_kwargs:
                        kwargs = {}
                        if min_date is not None:
                            kwargs["min_date"] = min_date
                        if max_date is not None:
                            kwargs["max_date"] = max_date
                        return original_get_tech(symbol, **kwargs)
                    return original_get_tech(symbol, min_date, max_date)

                setattr(scanner, progress_attr, _tracked_get_tech)

                if scan_as_of:
                    result = scanner.scan(as_on_date=scan_date)
                else:
                    result = scanner.scan()

                state["progress"] = 95
                state["message"] = "Finalising results..."

                if result_mode == "df":
                    candidates = _df_to_safe_records(result)
                else:
                    import math as _math

                    candidates = result
                    for rec in candidates:
                        for key, val in list(rec.items()):
                            if isinstance(val, float) and (
                                _math.isnan(val) or _math.isinf(val)
                            ):
                                rec[key] = None

                if post_process:
                    candidates = post_process(candidates, scanner, payload)
                if tier_rank:
                    _apply_tier_rank(candidates)

                completed = {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "scanned_date": scan_date,
                }
                if "bear_market" in state_template:
                    completed["bear_market"] = (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    )
                with lock:
                    state.update(completed)
                if cache_path:
                    _save_cache(state, lock, cache_path, label)
            except Exception as e:
                logger.error("%s scan failed: %s", label, e, exc_info=True)
                with lock:
                    state.update(
                        {
                            "scan_status": "error",
                            "progress": 0,
                            "message": str(e),
                        }
                    )

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "started"}

    router.add_api_route(
        f"/{name}/status", _status_handler, methods=["GET"], name=f"{name}_status"
    )
    router.add_api_route(
        f"/{name}/scan", _scan_handler, methods=["POST"], name=f"{name}_scan"
    )


# ---------------------------------------------------------------------------
# Standard scanner registrations
# ---------------------------------------------------------------------------

# --- Invisible Hand ---
def _ih_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    window = int(payload.get("window", 20))
    hist_window = int(payload.get("hist_window", 60))
    min_ih_score = int(payload.get("min_ih_score", 35))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        effective_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        effective_date = _get_latest_trading_day_before(
            datetime.now().strftime("%Y-%m-%d")
        )
    return (
        {
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "window": window,
            "hist_window": hist_window,
            "min_ih_score": min_ih_score,
            "target_date": effective_date,
        },
        effective_date,
    )


def _ih_build(kwargs, scan_date):
    from myra_app.strategies.invisible_hand_scanner import InvisibleHandScanner

    return InvisibleHandScanner(**kwargs)


register_scanner(
    "invisible-hand",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="invisible_hand_cache.json",
    parse_payload=_ih_parse,
    build_scanner=_ih_build,
    scan_as_of=False,
    result_mode="df",
    status_extra="bear_market",
    label="Invisible Hand",
)

# --- The Trigger ---
def _trigger_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    min_float_util_pct = float(payload.get("min_float_util_pct", 8.0))
    vol_pinch_ratio = float(payload.get("vol_pinch_ratio", 0.75))
    price_range_max_pct = float(payload.get("price_range_max_pct", 10.0))
    min_smart_float_ratio = float(payload.get("min_smart_float_ratio", 0.55))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return (
        {
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "min_float_util_pct": min_float_util_pct,
            "vol_pinch_ratio": vol_pinch_ratio,
            "price_range_max_pct": price_range_max_pct,
            "min_smart_float_ratio": min_smart_float_ratio,
        },
        scan_date,
    )


def _trigger_build(kwargs, scan_date):
    from myra_app.strategies.trigger_scanner import TriggerScanner

    return TriggerScanner(**kwargs)


register_scanner(
    "trigger",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="trigger_cache.json",
    parse_payload=_trigger_parse,
    build_scanner=_trigger_build,
    result_mode="list",
    status_extra="bear_market",
    label="Trigger",
)

# --- Liquidity Flip Detector ---
def _lf_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    prior_window = int(payload.get("prior_window", 120))
    recent_window = int(payload.get("recent_window", 30))
    lookback_days = int(payload.get("lookback_days", 150))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return (
        {
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "prior_window": prior_window,
            "recent_window": recent_window,
            "lookback_days": lookback_days,
        },
        scan_date,
    )


def _lf_build(kwargs, scan_date):
    from myra_app.strategies.liquidity_flip_detector import LiquidityFlipDetector

    return LiquidityFlipDetector(**kwargs)


register_scanner(
    "liquidity-flip",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="liquidity_flip_cache.json",
    parse_payload=_lf_parse,
    build_scanner=_lf_build,
    result_mode="df",
    status_extra="bear_market",
    label="Liquidity Flip",
)

# --- DCB Bargain ---
def _dcb_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    dcb_window = int(payload.get("dcb_window", 120))
    min_discount_pct = float(payload.get("min_discount_pct", 15.0))
    max_discount_pct = float(payload.get("max_discount_pct", 60.0))
    min_del_abs = float(payload.get("min_del_abs", -2.0))
    min_adtv_cr = float(payload.get("min_adtv_cr", 1.0))
    min_high_del_days = int(payload.get("min_high_del_days", 10))
    sanity_mult = float(payload.get("sanity_mult", 5.0))
    timeframe = str(payload.get("timeframe", "daily"))
    if timeframe not in ("daily", "weekly"):
        raise HTTPException(
            status_code=400, detail="timeframe must be 'daily' or 'weekly'"
        )
    min_ff_mcap = float(payload.get("min_ff_mcap", 600.0))
    exclude_circuits = bool(payload.get("exclude_circuits", True))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return (
        {
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "dcb_window": dcb_window,
            "min_discount_pct": min_discount_pct,
            "max_discount_pct": max_discount_pct,
            "min_del_abs": min_del_abs,
            "min_adtv_cr": min_adtv_cr,
            "min_high_del_days": min_high_del_days,
            "sanity_mult": sanity_mult,
            "timeframe": timeframe,
            "min_ff_mcap": min_ff_mcap,
            "exclude_circuits": exclude_circuits,
        },
        scan_date,
    )


def _dcb_build(kwargs, scan_date):
    from myra_app.strategies.dcb_bargain import DCBBargainScanner

    exclude_circuits = kwargs.pop("exclude_circuits")
    scanner = DCBBargainScanner(**kwargs)
    scanner._exclude_circuits = exclude_circuits
    return scanner


def _dcb_post(records, scanner, payload):
    if getattr(scanner, "_exclude_circuits", True):
        # Mirror original: pick the circuit column that exists, keep rows where
        # it is falsy/NaN (converted to None by _df_to_safe_records).
        col = (
            "is_circuit_lock"
            if any("is_circuit_lock" in r for r in records)
            else "is_lower_circuit"
        )
        if records and col in records[0]:
            return [r for r in records if not r.get(col)]
    return records


register_scanner(
    "dcb-bargain",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="dcb_bargain_cache.json",
    parse_payload=_dcb_parse,
    build_scanner=_dcb_build,
    result_mode="df",
    status_post_process=_apply_tier_rank,
    post_process=_dcb_post,
    tier_rank=True,
    status_extra="bear_market",
    label="DCB Bargain",
)


@router.get("/dcb-bargain/defaults")
async def dcb_bargain_defaults():
    """Return backend default parameter values for the DCB Bargain scanner."""
    return {
        "min_mcap": 200,
        "max_mcap": 50000,
        "dcb_window": 120,
        "min_discount_pct": 15.0,
        "max_discount_pct": 60.0,
        "min_del_abs": -2.0,
        "min_adtv_cr": 1.0,
        "min_high_del_days": 10,
        "sanity_mult": 5.0,
        "timeframe": "daily",
        "min_ff_mcap": 600.0,
        "exclude_circuits": True,
    }


# --- Operator Fingerprint ---
def _of_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    # prior_window/recent_window/lookback_days parsed but unused by constructor
    int(payload.get("prior_window", 120))
    int(payload.get("recent_window", 30))
    int(payload.get("lookback_days", 150))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return {"min_mcap": min_mcap, "max_mcap": max_mcap}, scan_date


def _of_build(kwargs, scan_date):
    from myra_app.strategies.operator_fingerprint_scanner import (
        OperatorFingerprintScanner,
    )

    return OperatorFingerprintScanner(**kwargs)


register_scanner(
    "operator-fingerprint",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="operator_fingerprint_cache.json",
    parse_payload=_of_parse,
    build_scanner=_of_build,
    result_mode="df",
    status_extra="bear_market",
    label="Operator Fingerprint",
)

# --- Float Exhaustion ---
def _fe_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    int(payload.get("prior_window", 120))
    int(payload.get("recent_window", 30))
    int(payload.get("lookback_days", 150))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return {"min_mcap": min_mcap, "max_mcap": max_mcap}, scan_date


def _fe_build(kwargs, scan_date):
    from myra_app.strategies.float_exhaustion_scanner import FloatExhaustionScanner

    return FloatExhaustionScanner(**kwargs)


register_scanner(
    "float-exhaustion",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="float_exhaustion_cache.json",
    parse_payload=_fe_parse,
    build_scanner=_fe_build,
    result_mode="list",
    status_extra="bear_market",
    label="Float Exhaustion",
)

# --- Seasonal Delivery Harvester ---
def _sd_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    target_month = payload.get("target_month")
    if target_month is not None:
        target_month = int(target_month)
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return (
        {
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "target_month": target_month,
        },
        scan_date,
    )


def _sd_build(kwargs, scan_date):
    from myra_app.strategies.seasonal_delivery_harvester import (
        SeasonalDeliveryHarvester,
    )

    return SeasonalDeliveryHarvester(**kwargs)


register_scanner(
    "seasonal-delivery",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "bear_market": False,
        "scanned_date": None,
    },
    cache_file="seasonal_delivery_cache.json",
    parse_payload=_sd_parse,
    build_scanner=_sd_build,
    result_mode="df",
    progress_attr="_get_all_tech_data",
    tracked_kwargs=True,
    status_extra="bear_market",
    label="Seasonal Delivery",
)

# --- Darvas Box Pro ---
def _darvas_parse(payload: dict):
    lookback = int(payload.get("lookback", 120))
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    int(payload.get("prior_window", 120))
    int(payload.get("recent_window", 30))
    int(payload.get("lookback_days", 150))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return {
        "base_days": lookback,
        "min_mcap": min_mcap,
        "max_mcap": max_mcap,
    }, scan_date


def _darvas_build(kwargs, scan_date):
    from myra_app.strategies.darvas_box_scanner import DarvasBoxScanner

    return DarvasBoxScanner(**kwargs)


register_scanner(
    "darvas",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "scanned_date": None,
    },
    cache_file="darvas_cache.json",
    parse_payload=_darvas_parse,
    build_scanner=_darvas_build,
    result_mode="df",
    label="Darvas Box",
)

# --- Wyckoff Automaton ---
def _wy_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    int(payload.get("prior_window", 120))
    int(payload.get("recent_window", 30))
    int(payload.get("lookback_days", 150))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return {"min_mcap": min_mcap, "max_mcap": max_mcap}, scan_date


def _wy_build(kwargs, scan_date):
    from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton

    return WyckoffAutomaton(**kwargs)


register_scanner(
    "wyckoff",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "scanned_date": None,
    },
    cache_file="wyckoff_cache.json",
    parse_payload=_wy_parse,
    build_scanner=_wy_build,
    result_mode="df",
    label="Wyckoff",
)

# --- Bottom Hunter ---
def _bh_parse(payload: dict):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    min_delivery_absorption = float(payload.get("min_delivery_absorption", 5.0))
    adtv_min_cr = float(payload.get("adtv_min_cr", 1.0))
    lookback_days = int(payload.get("lookback_days", 260))
    timeframe = str(payload.get("timeframe", "daily")).strip().lower()
    if timeframe not in ("daily", "weekly"):
        timeframe = "daily"
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return (
        {
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "min_delivery_absorption": min_delivery_absorption,
            "adtv_min_cr": adtv_min_cr,
            "lookback_days": lookback_days,
            "timeframe": timeframe,
        },
        scan_date,
    )


def _bh_build(kwargs, scan_date):
    from myra_app.strategies.bottom_hunter import BottomHunter

    return BottomHunter(**kwargs)


register_scanner(
    "bottom-hunter",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "scanned_date": None,
    },
    cache_file="bottom_hunter_cache.json",
    parse_payload=_bh_parse,
    build_scanner=_bh_build,
    result_mode="df",
    status_extra="scanned_date",
    init_message="Initializing scanner...",
    label="Bottom Hunter",
)

# --- Climax Accumulation ---
def _climax_parse(payload: dict):
    min_adtv_cr = float(payload.get("min_adtv_cr", 1.0))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None
    return {"target_date": scan_date, "min_adtv_cr": min_adtv_cr}, scan_date


def _climax_build(kwargs, scan_date):
    from myra_app.strategies.climax_accumulation import ClimaxAccumulationScanner

    return ClimaxAccumulationScanner(**kwargs)


register_scanner(
    "climax-accumulation",
    state_template={
        "scan_status": "idle",
        "last_scan": None,
        "progress": 0,
        "message": "Idle — click Scan to start",
        "candidates": [],
        "scanned_date": None,
    },
    cache_file="climax_accumulation_cache.json",
    parse_payload=_climax_parse,
    build_scanner=_climax_build,
    result_mode="df",
    init_message="Initialising Climax Accumulation scanner...",
    label="Climax Accumulation",
)


# ---------------------------------------------------------------------------
# Launchpad Scanner (custom: in-memory predictions, no cache file)
# ---------------------------------------------------------------------------

_launchpad_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "predictions": [],
    "message": "",
}
_launchpad_scan_lock = threading.Lock()


@router.get("/launchpad/status")
async def launchpad_scan_status():
    return _launchpad_scan_state


@router.post("/launchpad/scan")
async def launchpad_scan(payload: dict = Body(default={})):
    with _launchpad_scan_lock:
        if _launchpad_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409
        _launchpad_scan_state.update(
            {
                "scan_status": "scanning",
                "predictions": [],
                "message": "Running launchpad predictions...",
            }
        )

    def _run():
        try:
            import os as _os, sqlite3, pandas as pd, numpy as np, joblib
            from myra_app.librarian_core import LibrarianCore

            model_path = "models/launchpad_xgb.joblib"
            if not _os.path.exists(model_path):
                _launchpad_scan_state.update(
                    {
                        "scan_status": "error",
                        "message": "Model not trained. Run Label + Train first.",
                    }
                )
                return

            tech_db = _os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
            val_db = _os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

            with sqlite3.connect(tech_db) as conn:
                events = conn.execute(
                    "SELECT symbol, trigger_date FROM launchpad_events WHERE success = 0 AND trigger_date >= date('now', '-180 days') ORDER BY trigger_date DESC"
                ).fetchall()

            if not events:
                _launchpad_scan_state.update(
                    {
                        "scan_status": "completed",
                        "predictions": [],
                        "message": "No stocks in digestion phase.",
                        "last_scan": datetime.now().isoformat(),
                    }
                )
                return

            model = joblib.load(model_path)
            results = []
            for sym, trig in events:
                try:
                    with sqlite3.connect(tech_db) as conn:
                        row = conn.execute(
                            "SELECT date, close, volume, delivery, high, low FROM technical_data WHERE symbol = ? AND date >= ? ORDER BY date ASC LIMIT 30",
                            (sym, trig),
                        ).fetchall()
                    if len(row) < 2:
                        continue
                    closes = [r[1] for r in row]
                    volumes = [r[2] for r in row]
                    deliveries = [r[3] for r in row]
                    highs = [r[4] for r in row]
                    lows = [r[5] for r in row]
                    first_close = closes[0]
                    last_close = closes[-1]
                    max_dd = (
                        (min(closes) - first_close) / first_close * 100
                        if first_close > 0
                        else 0
                    )
                    avg_vol = np.mean(volumes) if volumes else 1
                    avg_del = np.mean(deliveries) if deliveries else 0
                    avg_range = (
                        np.mean([h - l for h, l in zip(highs, lows)]) if highs else 1
                    )
                    del_vals = deliveries
                    if len(del_vals) > 1:
                        del_mean = np.mean(del_vals)
                        del_std = np.std(del_vals) if len(del_vals) > 1 else 1
                        del_zscores = [
                            (d - del_mean) / (del_std + 1e-9) for d in del_vals
                        ]
                        del_z_min = min(del_zscores)
                        del_z_mean = np.mean(del_zscores)
                    else:
                        del_z_min = 0.0
                        del_z_mean = 0.0
                    features = [
                        del_z_min,
                        del_z_mean,
                        avg_range / (avg_range + 1e-9),
                        volumes[-1] / (avg_vol + 1e-9),
                        len(row),
                        max_dd,
                    ]
                    X = pd.DataFrame(
                        [features],
                        columns=[
                            "del_zscore_min",
                            "del_zscore_mean",
                            "range_atr_min",
                            "vol_ratio_min",
                            "digestion_days",
                            "max_drawdown_pct",
                        ],
                    )
                    preds = model.predict(X)
                    predicted_return_pct = round(float(preds[0, 0]), 2)
                    breakout_probability = round(
                        1 / (1 + np.exp(-predicted_return_pct / 10)), 4
                    )
                    confidence = (
                        "High"
                        if breakout_probability >= 0.7
                        else ("Medium" if breakout_probability >= 0.4 else "Low")
                    )
                    sector = None
                    mcap = None
                    if _os.path.exists(val_db):
                        with sqlite3.connect(val_db) as vconn:
                            vrow = vconn.execute(
                                "SELECT COALESCE(market_cap, 0), sector FROM fundamentals WHERE symbol = ? LIMIT 1",
                                (sym,),
                            ).fetchone()
                            if vrow:
                                mcap = float(vrow[0]) if vrow[0] else None
                                sector = vrow[1]
                    results.append(
                        {
                            "symbol": sym,
                            "trigger_date": trig,
                            "predicted_return_pct": predicted_return_pct,
                            "confidence": confidence,
                            "sector": sector,
                            "market_cap": mcap,
                            "breakout_probability": breakout_probability,
                        }
                    )
                except Exception:
                    continue

            _launchpad_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "predictions": results,
                    "message": f"Found {len(results)} predictions",
                }
            )
        except Exception as e:
            _launchpad_scan_state.update(
                {"scan_status": "error", "message": str(e)}
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# ---------------------------------------------------------------------------
# Multibagger Pro (custom: global result, no lock/cache)
# ---------------------------------------------------------------------------

_multibagger_result = {
    "scan_status": "idle",
    "candidates": [],
    "message": "Use POST /api/multibagger/scan to run",
}


@router.post("/multibagger/scan")
async def multibagger_scan(payload: dict = Body(default={})):
    """Run Multibagger Pro scan and store results for status polling."""
    global _multibagger_result
    _multibagger_result = {
        "scan_status": "scanning",
        "candidates": [],
        "message": "Running...",
    }

    def _run():
        global _multibagger_result
        try:
            from myra_app.strategies.multibagger_early_detection import (
                Strategy as MultibaggerScanner,
            )
            from myra_app.librarian_core import LibrarianCore
            import math as _math, pandas as pd, sqlite3, os
            from myra_app.constants import DB_DIR

            lookback = int(payload.get("lookback", 42))
            min_mcap = int(payload.get("min_mcap", 200))
            max_mcap = int(payload.get("max_mcap", 50000))
            int(payload.get("prior_window", 120))
            int(payload.get("recent_window", 30))
            int(payload.get("lookback_days", 150))

            scanner = MultibaggerScanner()

            val_path = os.path.join(DB_DIR, "myra_valuation.db")
            tech_path = os.path.join(DB_DIR, "myra_technical.db")

            val_conn = sqlite3.connect(val_path)
            symbols = [
                r[0]
                for r in val_conn.execute(
                    "SELECT symbol FROM fundamentals WHERE COALESCE(market_cap,0) BETWEEN ? AND ?",
                    (min_mcap, max_mcap),
                ).fetchall()
            ]
            val_conn.close()

            if not symbols:
                symbols = [
                    r[0]
                    for r in sqlite3.connect(tech_path)
                    .execute(
                        "SELECT DISTINCT symbol FROM technical_data ORDER BY symbol"
                    )
                    .fetchall()
                ][:500]

            candidates = []
            tech_conn = sqlite3.connect(tech_path)
            val_conn2 = sqlite3.connect(val_path)
            funda_cols = [
                c[0]
                for c in val_conn2.execute(
                    "PRAGMA table_info(fundamentals)"
                ).fetchall()
            ]

            for i, sym in enumerate(symbols):
                if i % 50 == 0:
                    _multibagger_result["message"] = (
                        f"Scanning {i+1}/{len(symbols)}..."
                    )

                df = pd.read_sql(
                    f"SELECT date, open, high, low, close, volume FROM technical_data WHERE symbol=? AND date >= date('now','-{lookback+30} days') ORDER BY date",
                    tech_conn,
                    params=(sym,),
                )
                if df.empty or len(df) < 30:
                    continue

                row = val_conn2.execute(
                    "SELECT * FROM fundamentals WHERE symbol=?", (sym,)
                ).fetchone()
                if row:
                    funda = dict(zip(funda_cols, row))
                else:
                    funda = {}

                try:
                    result = scanner.run(df, funda)
                    if result and result.get("signal"):
                        result["symbol"] = sym
                        for k, v in list(result.items()):
                            if isinstance(v, float) and (
                                _math.isnan(v) or _math.isinf(v)
                            ):
                                result[k] = None
                        candidates.append(result)
                except Exception:
                    pass

            tech_conn.close()
            val_conn2.close()

            _multibagger_result = {
                "scan_status": "completed",
                "last_scan": datetime.now().isoformat(),
                "candidates": candidates,
                "message": f"Found {len(candidates)} candidates",
            }
        except Exception as e:
            _multibagger_result = {
                "scan_status": "error",
                "message": str(e),
                "candidates": [],
            }

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@router.get("/multibagger/status")
async def multibagger_status():
    """Return last Multibagger scan results."""
    return _multibagger_result


# ---------------------------------------------------------------------------
# Cache clearing
# ---------------------------------------------------------------------------

_ALLOWED_CACHE_CLEAR = {
    "invisible-hand",
    "trigger",
    "wyckoff",
    "float-exhaustion",
    "liquidity-flip",
    "operator-fingerprint",
    "seasonal-delivery",
    "darvas",
    "multibagger",
    "launchpad",
    "bottom-hunter",
    "climax-accumulation",
    "dcb-bargain",
}


@router.delete("/cache/{scanner_name}")
async def clear_scanner_cache(scanner_name: str):
    """Delete the cached scan results for a given scanner."""
    if scanner_name not in _ALLOWED_CACHE_CLEAR:
        raise HTTPException(status_code=400, detail="Unknown scanner")

    stem = scanner_name.replace("-", "_")
    cache_path = os.path.join(MODELS_DIR, f"{stem}_cache.json")
    existed = os.path.exists(cache_path)
    if existed:
        os.remove(cache_path)

    reset = {
        "scan_status": "idle",
        "candidates": [],
        "message": "Cache cleared",
        "last_scan": None,
    }
    state_info = _RESET_STATES.get(scanner_name)
    if state_info is not None:
        state_info[0].update(reset)

    return {"status": "deleted" if existed else "not_found", "scanner": scanner_name}