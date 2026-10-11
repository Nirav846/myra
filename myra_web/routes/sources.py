"""Data-source management endpoints (``/api/sources``).

Backs the Data Sources panel on Mission Control: list every fundamentals source
with its live health, toggle/tune one, reset it to defaults, and run a one-shot
live test.

Configuration lives in :mod:`myra_app.data_sources.source_config` (persisted in
``myra_metadata.db``); health comes from the persisted source-health snapshot.
This router holds no state of its own.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myra_app.data_sources import source_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    rate_per_sec: Optional[float] = None
    ttl_s: Optional[int] = None
    timeout_s: Optional[float] = None
    priority: Optional[int] = None


def _iso(epoch) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _shape_health(entry: Optional[dict]) -> dict:
    entry = entry or {}
    now = time.time()
    cooldown_until = float(entry.get("cooldown_until") or 0.0)
    last_ok = entry.get("last_ok")
    return {
        "fail_streak": int(entry.get("fail_streak") or 0),
        "empty_streak": int(entry.get("empty_streak") or 0),
        "cooling_down": cooldown_until > now,
        "cooldown_until": _iso(cooldown_until) if cooldown_until else None,
        "cooldown_remaining_s": (
            int(cooldown_until - now) if cooldown_until > now else 0
        ),
        "last_ok": _iso(last_ok) if last_ok else None,
    }


def _with_health(info: dict, health: Optional[dict] = None) -> dict:
    """Attach a health block so every source-returning endpoint has one shape.

    The panel replaces the list entry with whatever PATCH/reset return; omitting
    ``health`` there left the card's health block undefined and crashed the UI.
    """
    return {**info, "health": health or _shape_health(None)}


def _health_map() -> dict[str, dict]:
    """Persisted per-source health, shaped for the UI. Never raises."""
    try:
        from myra_app import enrichment_sources as es

        registry = es.default_registry()
        es.load_health(registry)
        return {
            name: _shape_health(entry) for name, entry in registry.snapshot().items()
        }
    except Exception as exc:  # noqa: BLE001 - health is best-effort
        logger.debug("sources: health unavailable: %s", exc)
        return {}


@router.get("")
def list_sources():
    """Every managed fundamentals source with its effective config + health."""
    health = _health_map()
    return {
        "version": source_config.SCHEMA_VERSION,
        "sources": [
            {
                **info,
                "health": health.get(info["name"], _shape_health(None)),
            }
            for info in source_config.list_sources()
        ],
    }


@router.get("/{name}")
def get_source(name: str):
    try:
        info = source_config.get_source(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown source '{name}'")
    return _with_health(info, _health_map().get(name))


@router.patch("/{name}")
def update_source(name: str, body: SourceUpdate):
    patch = body.model_dump(exclude_none=True)
    try:
        info = source_config.update_source(name, patch)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown source '{name}'")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _with_health(info, _health_map().get(name))


@router.post("/{name}/reset")
def reset_source(name: str):
    try:
        info = source_config.reset_source(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown source '{name}'")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _with_health(info, _health_map().get(name))


@router.post("/{name}/test")
def test_source(name: str):
    """One-shot live fetch for a sample symbol (diagnostic only).

    Only the sources the cost-aware resolver owns have a standalone fetcher to
    invoke. For the others we return ``supported: false`` rather than a false
    failure, so the UI can say so plainly.
    """
    try:
        source_config.get_source(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown source '{name}'")

    from myra_app.enrichment_sources import SOURCE_FETCHERS

    fetcher = SOURCE_FETCHERS.get(name)
    if fetcher is None:
        return {
            "source": name,
            "supported": False,
            "ok": False,
            "detail": "No standalone live test is available for this source.",
        }

    started = time.perf_counter()
    try:
        values = fetcher(source_config.SAMPLE_SYMBOL) or {}
    except Exception as exc:  # noqa: BLE001 - a test must never 500
        logger.warning("sources: test for %s failed: %s", name, exc)
        return {
            "source": name,
            "supported": True,
            "ok": False,
            "symbol": source_config.SAMPLE_SYMBOL,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.perf_counter() - started, 2),
        }
    return {
        "source": name,
        "supported": True,
        "ok": bool(values),
        "symbol": source_config.SAMPLE_SYMBOL,
        "values": values,
        "elapsed_s": round(time.perf_counter() - started, 2),
    }
