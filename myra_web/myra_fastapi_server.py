

# --- Invisible Hand Scanner State ---
_ih_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
}
import threading
_ih_scan_lock = threading.Lock()
_IH_SCAN_CACHE = "models/invisible_hand_cache.json"


def _save_ih_cache():
    import json as _json
    import os as _os
    try:
        _os.makedirs("models", exist_ok=True)
        with _ih_scan_lock:
            data = {
                "last_scan": _ih_scan_state["last_scan"],
                "candidates": _ih_scan_state["candidates"],
                "message": _ih_scan_state["message"],
            }
        with open(_IH_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception:
        pass


def _load_ih_cache() -> dict | None:
    import json as _json
    import os as _os
    try:
        if _os.path.exists(_IH_SCAN_CACHE):
            with open(_IH_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception:
        pass
    return None


@app.get("/api/invisible-hand/status")
async def invisible_hand_status():
    import copy
    with _ih_scan_lock:
        state = copy.deepcopy(_ih_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_ih_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get("message", f"Found {len(cache['candidates'])} candidates."),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/invisible-hand/scan")
async def invisible_hand_scan(payload: dict = Body(default={})):
    with _ih_scan_lock:
        if _ih_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _ih_scan_state.update({
            "scan_status": "scanning",
            "progress": 0,
            "message": "Initialising scanner...",
            "candidates": [],
        })

    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    window = int(payload.get("window", 20))
    hist_window = int(payload.get("hist_window", 60))
    min_ih_score = int(payload.get("min_ih_score", 35))

    def _run():
        try:
            from myra_app.strategies.invisible_hand_scanner import InvisibleHandScanner
            import math as _math

            scanner = InvisibleHandScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                window=window,
                hist_window=hist_window,
                min_ih_score=min_ih_score,
            )

            _ih_scan_state["message"] = "Loading universe..."
            _ih_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _ih_scan_state["message"] = f"Scanning {total} symbols..."
            _ih_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _ih_scan_state["progress"] = min(pct, 92)
                    _ih_scan_state["message"] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan()

            _ih_scan_state["progress"] = 95
            _ih_scan_state["message"] = "Finalising results..."

            candidates = []
            if not df.empty:
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    for key, val in list(rec.items()):
                        if isinstance(val, float) and (_math.isnan(val) or _math.isinf(val)):
                            rec[key] = None
                    candidates.append(rec)

            _ih_scan_state.update({
                "scan_status": "completed",
                "last_scan": datetime.now().isoformat(),
                "progress": 100,
                "message": f"Found {len(candidates)} candidates",
                "candidates": candidates,
                "bear_market": scanner.bear_market if hasattr(scanner, 'bear_market') else False,
            })
            _save_ih_cache()

        except Exception as e:
            logger.error("Invisible Hand scan failed: %s", e, exc_info=True)
            _ih_scan_state.update({
                "scan_status": "error",
                "progress": 0,
                "message": str(e),
            })

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Trigger Scanner State ---
_trigger_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
}
_trigger_scan_lock = threading.Lock()
_TRIGGER_SCAN_CACHE = "models/trigger_cache.json"


def _save_trigger_cache():
    import json as _json
    import os as _os
    try:
        _os.makedirs("models", exist_ok=True)
        with _trigger_scan_lock:
            data = {
                "last_scan": _trigger_scan_state["last_scan"],
                "candidates": _trigger_scan_state["candidates"],
                "message": _trigger_scan_state["message"],
            }
        with open(_TRIGGER_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception:
        pass


def _load_trigger_cache() -> dict | None:
    import json as _json
    import os as _os
    try:
        if _os.path.exists(_TRIGGER_SCAN_CACHE):
            with open(_TRIGGER_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception:
        pass
    return None


@app.get("/api/trigger/status")
async def trigger_status():
    import copy
    with _trigger_scan_lock:
        state = copy.deepcopy(_trigger_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_trigger_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get("message", f"Found {len(cache['candidates'])} candidates."),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/trigger/scan")
async def trigger_scan(payload: dict = Body(default={})):
    with _trigger_scan_lock:
        if _trigger_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _trigger_scan_state.update({
            "scan_status": "scanning",
            "progress": 0,
            "message": "Initialising scanner...",
            "candidates": [],
        })

    min_mcap = int(payload.get("min_mcap", 300))
    max_mcap = int(payload.get("max_mcap", 50000))
    min_float_util_pct = float(payload.get("min_float_util_pct", 12.0))
    vol_pinch_ratio = float(payload.get("vol_pinch_ratio", 0.72))
    price_range_max_pct = float(payload.get("price_range_max_pct", 2.8))

    def _run():
        try:
            from myra_app.strategies.trigger_scanner import TriggerScanner
            import math as _math

            scanner = TriggerScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                min_float_util_pct=min_float_util_pct,
                vol_pinch_ratio=vol_pinch_ratio,
                price_range_max_pct=price_range_max_pct,
            )

            _trigger_scan_state["message"] = "Loading universe..."
            _trigger_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _trigger_scan_state["message"] = f"Scanning {total} symbols..."
            _trigger_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _trigger_scan_state["progress"] = min(pct, 92)
                    _trigger_scan_state["message"] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan()

            _trigger_scan_state["progress"] = 95
            _trigger_scan_state["message"] = "Finalising results..."

            candidates = []
            if not df.empty:
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    for key, val in list(rec.items()):
                        if isinstance(val, float) and (_math.isnan(val) or _math.isinf(val)):
                            rec[key] = None
                    candidates.append(rec)

            _trigger_scan_state.update({
                "scan_status": "completed",
                "last_scan": datetime.now().isoformat(),
                "progress": 100,
                "message": f"Found {len(candidates)} candidates",
                "candidates": candidates,
                "bear_market": scanner.bear_market if hasattr(scanner, 'bear_market') else False,
            })
            _save_trigger_cache()

        except Exception as e:
            logger.error("Trigger scan failed: %s", e, exc_info=True)
            _trigger_scan_state.update({
                "scan_status": "error",
                "progress": 0,
                "message": str(e),
            })

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}
