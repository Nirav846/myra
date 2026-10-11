"""Operator-editable configuration for the fundamentals data sources.

This module is the single source of truth for *what* each source is (label,
what it provides, how costly it is, and whether disabling it is risky) and for
the bits an operator may change from the UI: the ``enabled`` flag plus tuning
(``rate_per_sec``, ``ttl_s``, ``timeout_s``, ``priority``).

The editable half is persisted as JSON under the ``metadata`` key
``enrichment_source_config`` in ``myra_metadata.db`` — additive, no schema
change, and it survives restarts.  The catalogue half is code, so a new source
is added by editing :data:`CATALOG`, never by mutating the database.

Consumers
---------
* Fetch paths call :func:`is_enabled` before touching a source.  Reads are
  cached in-process, so a per-symbol call in a hot loop is a dict lookup, not a
  DB round-trip.
* ``myra_app.enrichment_sources.default_registry`` builds its specs from
  :func:`build_specs` and applies the enabled flags, so the cost-aware resolver
  never tries a disabled source.

Failure policy: if the metadata DB cannot be read, every source is treated as
enabled.  A transient DB hiccup must never silently stop all enrichment.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from myra_app.data_sources.registry import SourceSpec

logger = logging.getLogger(__name__)

#: Key in the ``metadata`` table of ``myra_metadata.db``.
CONFIG_META_KEY = "enrichment_source_config"

#: Bumped when the persisted shape changes; readers tolerate older blobs.
SCHEMA_VERSION = 1

#: Symbol used by the router's "test" action.
SAMPLE_SYMBOL = "RELIANCE"

#: Tuning field -> (min, max) inclusive bounds. ``enabled`` is validated apart.
_NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "rate_per_sec": (0.01, 100.0),
    "ttl_s": (0, 365 * 24 * 3600),
    "timeout_s": (1.0, 600.0),
    "priority": (0, 1000),
}


@dataclass(frozen=True)
class SourceInfo:
    """Static description of a fundamentals source (the non-editable half)."""

    name: str
    label: str
    description: str
    tier: str  # "primary" | "fallback" | "bulk"
    cost: str  # "fast" | "medium" | "slow"
    capabilities: tuple[str, ...]
    #: True when the source is wired into a live fetch path. A source can be
    #: declared in config/myra_sources.json and still be unimplemented; the UI
    #: surfaces that rather than pretending enabling it does anything.
    implemented: bool = True
    #: True when the cost-aware resolver's registry owns this source.
    in_registry: bool = False
    default_rate_per_sec: float = 1.0
    default_ttl_s: int = 86_400
    default_timeout_s: float = 20.0
    default_priority: int = 0
    is_rate_limit: bool = False
    #: Non-empty warning string when disabling this source is risky, e.g. it is
    #: the only provider of a metric. The UI shows this before disabling.
    disable_warning: Optional[str] = None


#: The six fundamentals sources the panel manages. Order is display order.
CATALOG: dict[str, SourceInfo] = {
    "screener_in": SourceInfo(
        name="screener_in",
        label="Screener.in",
        description=(
            "Scrapes the company page for the quarterly P&L, top ratios and the "
            "shareholding pattern in one request. The authoritative source for "
            "ROE, ROCE and the promoter pledge ratio."
        ),
        tier="primary",
        cost="slow",
        capabilities=(
            "pe",
            "roe",
            "roce",
            "book_value",
            "market_cap",
            "sector",
            "promoter_holding_pct",
        ),
        implemented=True,
        in_registry=True,
        default_rate_per_sec=0.5,
        default_ttl_s=7 * 86_400,
        default_timeout_s=40.0,
        disable_warning=(
            "Screener.in is the only source for ROE, ROCE and the promoter "
            "pledge ratio. Disabling it leaves those metrics stale and stops "
            "the main fundamentals refresh."
        ),
    ),
    "google_finance": SourceInfo(
        name="google_finance",
        label="Google Finance",
        description=(
            "First fallback when Screener.in fails. Provides a limited ratio "
            "set (P/E, book value, market cap) with no quarterly history."
        ),
        tier="fallback",
        cost="medium",
        capabilities=("pe", "book_value", "market_cap"),
        implemented=True,
        in_registry=False,
        default_rate_per_sec=1.0,
        default_ttl_s=86_400,
        default_timeout_s=20.0,
    ),
    "finology": SourceInfo(
        name="finology",
        label="Finology Ticker",
        description=(
            "Declared as the second fallback in config/myra_sources.json but "
            "not wired into the fetch chain yet."
        ),
        tier="fallback",
        cost="slow",
        capabilities=("pe", "book_value", "roe"),
        implemented=False,
        in_registry=False,
        default_rate_per_sec=1.0,
        default_ttl_s=86_400,
        default_timeout_s=20.0,
        disable_warning=(
            "Finology is declared as a fallback but is not implemented, so this "
            "toggle has no effect on fetching today."
        ),
    ),
    "yfinance": SourceInfo(
        name="yfinance",
        label="yfinance",
        description=(
            "Yahoo Finance via yfinance. Cheap per-symbol pull of market cap, "
            "shares outstanding, sector, P/E, ROE and book value."
        ),
        tier="fallback",
        cost="fast",
        capabilities=(
            "market_cap",
            "shares_outstanding",
            "free_float_shares",
            "sector",
            "industry",
            "pe",
            "roe",
            "book_value",
            "dividend_yield",
            "eps",
            "insider_holding_pct",
        ),
        implemented=True,
        in_registry=True,
        default_rate_per_sec=3.0,
        default_ttl_s=86_400,
        default_timeout_s=20.0,
    ),
    "nse_shareholding": SourceInfo(
        name="nse_shareholding",
        label="NSE Shareholding",
        description=(
            "NSE quarterly shareholding pattern. Fills the promoter / public "
            "split when Screener.in does not."
        ),
        tier="fallback",
        cost="medium",
        capabilities=("promoter_holding_pct", "public_holding_pct"),
        implemented=True,
        in_registry=True,
        default_rate_per_sec=1.0,
        default_ttl_s=7 * 86_400,
        default_timeout_s=20.0,
        is_rate_limit=True,
    ),
    "morningstar": SourceInfo(
        name="morningstar",
        label="Morningstar (bulk)",
        description=(
            "Bulk screener API used by the weekly fundamentals sync for sector, "
            "net margin, ROE and dividend yield across the whole market."
        ),
        tier="bulk",
        cost="medium",
        capabilities=(
            "sector",
            "net_margin",
            "roe",
            "dividend_yield",
            "pe",
            "book_value",
            "market_cap",
            "debt_to_equity",
        ),
        implemented=True,
        in_registry=False,
        default_rate_per_sec=1.0,
        default_ttl_s=86_400,
        default_timeout_s=60.0,
    ),
}

#: Cached effective config plus a short-lived guard so a *failed* read is not
#: remembered for the whole process. A transient DB hiccup should recover by the
#: next minute rather than pin every source to "enabled" until restart.
_CACHE: Optional[dict[str, dict]] = None
_CACHE_AT: float = 0.0
_CACHE_FAILED: bool = False
_CACHE_LOCK = threading.RLock()

#: How long a failed read may be reused before retrying the DB.
_FAILURE_TTL_S = 30.0


def _defaults(name: str) -> dict:
    info = CATALOG[name]
    return {
        "enabled": True,
        "rate_per_sec": info.default_rate_per_sec,
        "ttl_s": info.default_ttl_s,
        "timeout_s": info.default_timeout_s,
        "priority": info.default_priority,
    }


def _read_raw_ok() -> tuple[dict, bool]:
    """Read the persisted overrides blob as ``(data, ok)``.

    ``ok`` is False when the read *failed* (DB error, or a present-but-malformed
    blob). An absent blob is a success with no overrides: ``({}, True)``. Readers
    fail open on ``ok=False``; writers must refuse to overwrite.
    """
    try:
        from myra_app.librarian_core import LibrarianCore

        lib = LibrarianCore(read_only=True)
        try:
            raw = lib.get_metadata(CONFIG_META_KEY)
        finally:
            lib.close()
        if not raw:
            return {}, True
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("sources"), dict):
            return data, True
        logger.warning("source_config: ignoring unrecognised config blob")
        return {}, False
    except Exception as exc:  # noqa: BLE001 - config must fail open
        logger.debug("source_config: could not read config: %s", exc)
        return {}, False


def _read_raw(*, raise_on_error: bool = False) -> dict:
    """Read the persisted overrides blob.

    Returns ``{}`` on a read failure by default (fail-open for readers). With
    ``raise_on_error`` it raises instead, so a writer never mistakes an
    unreadable blob for "no overrides" and clobbers the other sources.
    """
    data, ok = _read_raw_ok()
    if not ok and raise_on_error:
        raise RuntimeError("source configuration is unreadable")
    return data


def _merge(raw: dict) -> dict[str, dict]:
    """Fold persisted overrides over the catalogue defaults."""
    stored = raw.get("sources", {}) if isinstance(raw, dict) else {}
    merged: dict[str, dict] = {}
    for name in CATALOG:
        entry = dict(_defaults(name))
        override = stored.get(name)
        if isinstance(override, dict):
            for key in ("enabled", "rate_per_sec", "ttl_s", "timeout_s", "priority"):
                if key in override and override[key] is not None:
                    entry[key] = override[key]
        merged[name] = entry
    return merged


def _effective() -> dict[str, dict]:
    """Cached effective config (defaults + overrides).

    A successful read is cached for the process. A failed read is served from
    defaults but only reused for :data:`_FAILURE_TTL_S`, so a transient DB error
    neither stops enrichment nor becomes a permanent revert.
    """
    global _CACHE, _CACHE_AT, _CACHE_FAILED
    with _CACHE_LOCK:
        if _CACHE is not None and (
            not _CACHE_FAILED or (time.monotonic() - _CACHE_AT) < _FAILURE_TTL_S
        ):
            return _CACHE

    raw, ok = _read_raw_ok()
    merged = _merge(raw)
    with _CACHE_LOCK:
        _CACHE = merged
        _CACHE_AT = time.monotonic()
        _CACHE_FAILED = not ok
        return _CACHE


def invalidate_cache() -> None:
    """Drop the cached config so the next read hits the DB."""
    global _CACHE, _CACHE_FAILED
    with _CACHE_LOCK:
        _CACHE = None
        _CACHE_FAILED = False


# --------------------------------------------------------------------------- #
# Read API
# --------------------------------------------------------------------------- #
def is_enabled(name: str) -> bool:
    """Whether ``name`` may be fetched. Unknown names default to enabled."""
    entry = _effective().get(name)
    if entry is None:
        return True
    return bool(entry.get("enabled", True))


def disabled_names() -> set[str]:
    return {name for name in CATALOG if not is_enabled(name)}


def get_source(name: str) -> dict:
    """Effective config + catalogue metadata for one source."""
    _require_known(name)
    info = CATALOG[name]
    out = {
        "name": info.name,
        "label": info.label,
        "description": info.description,
        "tier": info.tier,
        "cost": info.cost,
        "capabilities": list(info.capabilities),
        "implemented": info.implemented,
        "in_registry": info.in_registry,
        "disable_warning": info.disable_warning,
    }
    out.update(_effective()[name])
    return out


def list_sources() -> list[dict]:
    return [get_source(name) for name in CATALOG]


def build_specs() -> list[SourceSpec]:
    """Registry ``SourceSpec`` list built from the catalogue + current tuning.

    Only sources the cost-aware resolver owns (``in_registry``) are included;
    the rest are enforced at their own fetch paths.
    """
    cfg = _effective()
    return [
        SourceSpec(
            name=name,
            capabilities=frozenset(info.capabilities),
            cost=info.cost,
            priority=int(cfg[name]["priority"]),
            timeout_s=float(cfg[name]["timeout_s"]),
            rate_per_sec=float(cfg[name]["rate_per_sec"]),
            ttl_s=int(cfg[name]["ttl_s"]),
            is_rate_limit=info.is_rate_limit,
        )
        for name, info in CATALOG.items()
        if info.in_registry
    ]


# --------------------------------------------------------------------------- #
# Write API
# --------------------------------------------------------------------------- #
def _require_known(name: str) -> None:
    if name not in CATALOG:
        raise KeyError(name)


def _validate(patch: dict) -> dict:
    """Validate and coerce an operator patch. Raises ValueError on bad input."""
    clean: dict = {}
    if "enabled" in patch:
        value = patch["enabled"]
        if not isinstance(value, bool):
            raise ValueError("enabled must be a boolean")
        clean["enabled"] = value
    for field_name, (lo, hi) in _NUMERIC_BOUNDS.items():
        if field_name not in patch or patch[field_name] is None:
            continue
        try:
            num = float(patch[field_name])
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} must be a number")
        if not (lo <= num <= hi):
            raise ValueError(f"{field_name} must be between {lo} and {hi}")
        clean[field_name] = int(num) if field_name in ("ttl_s", "priority") else num
    if not clean:
        raise ValueError("no recognised fields to update")
    return clean


def _persist(sources: dict[str, dict]) -> bool:
    """Write the override blob. Returns True on success. Never raises."""
    blob = json.dumps({"version": SCHEMA_VERSION, "sources": sources})
    try:
        from myra_app.librarian_core import LibrarianCore

        lib = LibrarianCore(read_only=False)
        try:
            lib.set_metadata(CONFIG_META_KEY, blob)
        finally:
            lib.close()
        return True
    except Exception as exc:  # noqa: BLE001 - caller decides how to surface
        logger.warning("source_config: could not save config: %s", exc)
        return False


def update_source(name: str, patch: dict) -> dict:
    """Apply a validated ``patch`` to ``name`` and persist it.

    Raises ``KeyError`` for an unknown source and ``ValueError`` for a bad
    value; the router maps those to 404/400. Raises ``RuntimeError`` if the
    stored blob cannot be read (so the write cannot clobber other sources) or
    cannot be saved (500 either way). Returns the new effective entry.
    """
    _require_known(name)
    clean = _validate(patch)
    stored = dict(_read_raw(raise_on_error=True).get("sources", {}))
    entry = dict(stored.get(name, {}))
    entry.update(clean)
    stored[name] = entry
    if not _persist(stored):
        raise RuntimeError("could not persist source configuration")
    invalidate_cache()
    return get_source(name)


def reset_source(name: str) -> dict:
    """Drop any overrides for ``name`` (back to catalogue defaults).

    Raises ``RuntimeError`` if the stored blob cannot be read or saved, so a
    failed read never wipes every other source's overrides.
    """
    _require_known(name)
    stored = dict(_read_raw(raise_on_error=True).get("sources", {}))
    stored.pop(name, None)
    if not _persist(stored):
        raise RuntimeError("could not persist source configuration")
    invalidate_cache()
    return get_source(name)


def apply_to_registry(registry) -> None:
    """Push current tuning + enabled flags onto a ``SourceRegistry``."""
    for spec in build_specs():
        registry.register(spec)
    for name in registry.names():
        registry.set_enabled(name, is_enabled(name))
