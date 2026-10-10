"""Cost-aware fallback resolver.

Given a capability (a metric key), a symbol, a mapping of ``source_name -> fetch
callable`` and a :class:`~myra_app.data_sources.registry.SourceRegistry`, the
resolver tries candidate sources cheapest-first, honouring cooldowns and enforcing
a per-source timeout, and returns the first useful result.  When every source fails
it falls back to a *stale* cache entry (if any) so callers can still render data.

This is intentionally transport-agnostic: sources are plain
``callable(symbol) -> dict`` and are isolated from one another.

See ``docs/ENRICHMENT_PIPELINE_PLAN.md`` §4.1.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable, Iterable, Optional

from myra_app.data_sources.base import RateLimiter
from myra_app.data_sources.cache import TtlCache
from myra_app.data_sources.registry import SourceRegistry, SourceResult, SourceSpec

logger = logging.getLogger(__name__)

FetchFn = Callable[[str], dict]

_EXECUTOR: Optional[ThreadPoolExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Lazily create a shared, bounded pool for timeout enforcement.

    A hung request cannot be force-killed in Python, so a timed-out call may keep
    occupying one worker until its underlying socket timeout fires.  The pool is
    sized to tolerate that without stalling the resolver.
    """
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ThreadPoolExecutor(
                    max_workers=8, thread_name_prefix="myra-src"
                )
    return _EXECUTOR


def _call_with_timeout(fn: FetchFn, symbol: str, timeout_s: float) -> dict:
    future = _get_executor().submit(fn, symbol)
    try:
        return future.result(timeout=timeout_s)
    except FuturesTimeout:
        future.cancel()
        raise TimeoutError(f"source call timed out after {timeout_s}s") from None


def _clean(values: Optional[dict]) -> dict:
    if not values:
        return {}
    return {k: v for k, v in values.items() if v is not None}


def _cache_key(base: Optional[str], spec: SourceSpec, capability: str) -> Optional[str]:
    if base is None:
        return None
    return f"{base}|{spec.name}|{capability}"


def resolve(
    capability: str,
    symbol: str,
    fetchers: dict[str, FetchFn],
    registry: SourceRegistry,
    cache: Optional[TtlCache] = None,
    cache_base: Optional[str] = None,
    rate_limiters: Optional[dict[str, RateLimiter]] = None,
    fetch_cache: Optional[dict[str, dict]] = None,
) -> SourceResult:
    """Resolve a single ``capability`` for ``symbol`` with fallback.

    ``fetch_cache`` lets a caller share fetched bundles between capabilities of the
    same symbol so one source is not called twice in a single orchestration pass.
    """
    attempts: list[dict] = []
    last_error: Optional[str] = None

    for spec in registry.candidates(capability):
        key = _cache_key(cache_base, spec, capability)

        # 1) fresh cache
        if cache is not None and key is not None:
            cached = _clean(cache.get(key, ttl_seconds=spec.ttl_s))
            if cached.get(capability) is not None:
                registry.record_success(spec.name)
                return SourceResult(
                    ok=True,
                    source=spec.name,
                    values=cached,
                    from_cache=True,
                )

        fetcher = fetchers.get(spec.name)
        if fetcher is None:
            continue

        # 2) shared per-symbol bundle (already fetched for another capability)
        if fetch_cache is not None and spec.name in fetch_cache:
            values = fetch_cache[spec.name]
        else:
            limiter = (rate_limiters or {}).get(spec.name)
            if limiter is not None:
                limiter.wait()
            t0 = time.perf_counter()
            try:
                values = _clean(_call_with_timeout(fetcher, symbol, spec.timeout_s))
            except TimeoutError as exc:
                registry.record_failure(spec.name, is_rate_limit=False)
                last_error = str(exc)
                attempts.append({"source": spec.name, "ok": False, "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 - one source must never abort
                registry.record_failure(spec.name, is_rate_limit=spec.is_rate_limit)
                last_error = f"{type(exc).__name__}: {exc}"
                attempts.append({"source": spec.name, "ok": False, "error": last_error})
                continue
            elapsed = time.perf_counter() - t0
            if fetch_cache is not None:
                fetch_cache[spec.name] = values
            if values and cache is not None and key is not None:
                cache.set(key, values, ttl_seconds=spec.ttl_s)
            attempts.append(
                {
                    "source": spec.name,
                    "ok": bool(values.get(capability) is not None),
                    "elapsed_s": elapsed,
                }
            )

        if values.get(capability) is not None:
            registry.record_success(spec.name)
            return SourceResult(ok=True, source=spec.name, values=values)

        # The source is healthy but has nothing for *this* capability.  Do NOT
        # penalise it: cooling it down here would starve the same source for the
        # other capabilities resolved in this pass.  Only a genuinely empty
        # result counts (softly) against the source.
        if values:
            last_error = f"{spec.name} has no {capability}"
        else:
            registry.record_empty(spec.name)
            last_error = f"{spec.name} returned no data"

    # 3) stale cache as last resort
    if cache is not None and cache_base is not None:
        # Use *all* specs (not just available ones): a source in cooldown may
        # still hold a good cached value we can serve.
        for spec in registry.specs_for(capability):
            key = _cache_key(cache_base, spec, capability)
            stale = _clean(cache.get_stale(key)) if key else {}
            if stale.get(capability) is not None:
                logger.info(
                    "resolver: all sources failed for %s/%s; using stale cache (%s)",
                    capability,
                    symbol,
                    spec.name,
                )
                return SourceResult(
                    ok=True,
                    source=spec.name,
                    values=stale,
                    stale=True,
                    from_cache=True,
                )

    return SourceResult(
        ok=False, source=None, values={}, error=last_error or "no source available"
    )


def resolve_many(
    capabilities: Iterable[str],
    symbol: str,
    fetchers: dict[str, FetchFn],
    registry: SourceRegistry,
    cache: Optional[TtlCache] = None,
    cache_base: Optional[str] = None,
    rate_limiters: Optional[dict[str, RateLimiter]] = None,
) -> tuple[dict, dict]:
    """Resolve several capabilities, sharing source bundles within one call.

    Returns ``(values, provenance)`` where ``provenance[metric] = source_name``.
    """
    wanted = list(dict.fromkeys(capabilities))
    values: dict = {}
    provenance: dict = {}
    fetch_cache: dict[str, dict] = {}
    for capability in wanted:
        outcome = resolve(
            capability,
            symbol,
            fetchers,
            registry,
            cache=cache,
            cache_base=cache_base,
            rate_limiters=rate_limiters,
            fetch_cache=fetch_cache,
        )
        if outcome.ok and outcome.values.get(capability) is not None:
            for k, v in outcome.values.items():
                if values.get(k) is None and v is not None:
                    values[k] = v
                    provenance[k] = outcome.source
    return values, provenance
