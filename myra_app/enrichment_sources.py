"""Cost-aware enrichment adapters (Phase 2).

Thin, isolated adapters that reuse the *existing* fetch code (no new network
logic) and expose it to the cost-aware resolver:

* ``yfinance_fetch``         — wraps ``fetchers.full_fundamentals.fetch_yfinance_data``
* ``screener_fetch``         — wraps the Screener.in snapshot/chart helpers
* ``nse_shareholding_fetch`` — wraps ``utils.bse_shareholding.fetch_nse_shareholding``

``enrich_symbol`` / ``enrich_batch`` resolve every wanted capability through
``data_sources.resolver`` (fast → medium → slow, with stale-cache last resort)
and write the results into ``fundamentals`` only through ``safe_write``, so a
failed or partial fetch can never erase a valid metric.

Safety defaults
---------------
* ``dry_run=True`` by default — resolve + report, **write nothing**. Callers that
  own a connection (the pipeline task / a CLI) opt in with ``dry_run=False``.
* Writes are fill-only / zero-guarded via ``safe_write.update_fill_only``.
* ``roce`` has no column in ``fundamentals`` (it lives in ``screener_fundamentals``
  and is owned by ``db.enrichers.screener_enricher``) so it is resolved for
  reporting but deliberately **not written** here — avoiding a second writer.

See ``docs/ENRICHMENT_PIPELINE_PLAN.md`` §4.2/§4.4.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Iterable, Optional

from myra_app.data_sources.base import RateLimiter
from myra_app.data_sources.cache import TtlCache
from myra_app.data_sources.registry import SourceRegistry, SourceSpec
from myra_app.data_sources.resolver import resolve_many
from myra_app.safe_write import (
    FUNDAMENTALS_ZERO_GUARDED,
    sanitize_metrics,
    update_fill_only,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Capability names == canonical ``fundamentals`` column names (where they exist).
# --------------------------------------------------------------------------- #
MCAP = "market_cap"
SHARES = "shares_outstanding"
FREE_FLOAT_SHARES = "free_float_shares"
FREE_FLOAT_PCT = "free_float_pct"
FREE_FLOAT_MCAP = "free_float_market_cap"
PROMOTER = "promoter_holding_pct"
PUBLIC = "public_holding_pct"
INSIDER = "insider_holding_pct"
SECTOR = "sector"
INDUSTRY = "industry"
PE = "pe"
ROE = "roe"
ROCE = "roce"
BOOK_VALUE = "book_value"
DIVIDEND_YIELD = "dividend_yield"
EPS = "eps"

#: Everything the orchestrator asks for by default (roce is fetch-only).
DEFAULT_WANT = (
    MCAP,
    SHARES,
    FREE_FLOAT_SHARES,
    PROMOTER,
    PUBLIC,
    INSIDER,
    SECTOR,
    INDUSTRY,
    PE,
    ROE,
    ROCE,
    BOOK_VALUE,
    DIVIDEND_YIELD,
    EPS,
)

#: yfinance returns these as 0..1 *fractions*; MYRA stores percentages
#: (``fundamentals.roe`` is compared against 15, rendered with a "%" suffix, etc.).
_YF_FRACTION_CAPS = {INSIDER, ROE}

_CRORE = 1e7


def _clean(values: dict) -> dict:
    return {k: v for k, v in values.items() if v is not None}


# --------------------------------------------------------------------------- #
# Adapters — each returns {capability: value}; {} on failure. Never raise.
# --------------------------------------------------------------------------- #


def yfinance_fetch(symbol: str) -> dict:
    """Fast path: yfinance ``.info`` mapped to canonical capability names."""
    try:
        from myra_app.fetchers.full_fundamentals import (
            YFINANCE_AVAILABLE,
            fetch_yfinance_data,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("yfinance adapter unavailable: %s", exc)
        return {}
    if not YFINANCE_AVAILABLE:
        return {}

    raw = fetch_yfinance_data(symbol) or {}
    out: dict = {
        MCAP: raw.get("market_cap"),
        SHARES: raw.get("shares_outstanding"),
        FREE_FLOAT_SHARES: raw.get("float_shares"),
        SECTOR: raw.get("sector"),
        INDUSTRY: raw.get("industry"),
        PE: raw.get("pe"),
        ROE: raw.get("roe"),
        BOOK_VALUE: raw.get("book_value"),
        DIVIDEND_YIELD: raw.get("dividend_yield"),
        EPS: raw.get("trailing_eps"),
        INSIDER: raw.get("held_percent_insiders"),
    }
    for cap in _YF_FRACTION_CAPS:
        val = out.get(cap)
        if val is not None:
            try:
                out[cap] = float(val) * 100.0
            except (TypeError, ValueError):
                out[cap] = None
    return _clean(out)


def screener_fetch(symbol: str) -> dict:
    """Slow path: Screener.in snapshot (Scrapling) with a requests chart-API
    fallback for ROE/ROCE. Returns {} when nothing usable comes back."""
    try:
        from myra_app.fetchers import full_fundamentals as ff
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("screener adapter unavailable: %s", exc)
        return {}

    out: dict = {}
    snapshot: dict = {}
    if getattr(ff, "SCRAPLING_AVAILABLE", False):
        try:
            snapshot = ff.fetch_screener_snapshot(symbol) or {}
        except Exception as exc:  # noqa: BLE001 - one source must never abort
            logger.debug("screener snapshot failed for %s: %s", symbol, exc)
            snapshot = {}

    if snapshot:
        out[ROE] = snapshot.get("roe")
        out[ROCE] = snapshot.get("roce")
        out[BOOK_VALUE] = snapshot.get("book_value")
        out[PE] = snapshot.get("pe")
        # Screener meta market cap is in Crores; fundamentals stores raw rupees.
        crore = snapshot.get("market_cap_crore")
        if crore is not None:
            try:
                out[MCAP] = float(crore) * _CRORE
            except (TypeError, ValueError):
                pass
        holding = snapshot.get("shareholding") or {}
        if holding.get("promoters") is not None:
            out[PROMOTER] = holding["promoters"]
        if snapshot.get("sector"):
            out[SECTOR] = snapshot.get("sector")

    # Fallback (plain requests) for the metrics that are Screener-only.
    if out.get(ROE) is None or out.get(ROCE) is None:
        try:
            cid = ff._get_company_id_requests(symbol)
            if cid:
                for key, cap in (
                    ("roe", ROE),
                    ("roce", ROCE),
                    ("price_to_book", "price_to_book"),
                ):
                    if out.get(cap) is not None:
                        continue
                    series = ff.fetch_timeseries(cid, key)
                    if series:
                        out[cap] = series[-1].get("value")
        except Exception as exc:  # noqa: BLE001
            logger.debug("screener chart fallback failed for %s: %s", symbol, exc)

    return _clean(out)


def nse_shareholding_fetch(symbol: str) -> dict:
    """NSE quarterly shareholding (dalal) → promoter/public split."""
    try:
        from myra_app.utils.bse_shareholding import fetch_nse_shareholding
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("nse shareholding adapter unavailable: %s", exc)
        return {}
    try:
        row = fetch_nse_shareholding(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.debug("nse shareholding failed for %s: %s", symbol, exc)
        return {}
    if not row:
        return {}
    out = {
        PROMOTER: row.get("promoter_pct"),
        PUBLIC: row.get("public_pct"),
    }
    if out.get(PUBLIC) is None and out.get(PROMOTER) is not None:
        out[PUBLIC] = round(100.0 - float(out[PROMOTER]), 2)
    return _clean(out)


# --------------------------------------------------------------------------- #
# Registry + fetcher wiring (module-level singletons).
# --------------------------------------------------------------------------- #
YFINANCE = "yfinance"
SCREENER = "screener_in"
NSE_SHAREHOLDING = "nse_shareholding"

SOURCE_FETCHERS: dict[str, Callable[[str], dict]] = {
    YFINANCE: yfinance_fetch,
    SCREENER: screener_fetch,
    NSE_SHAREHOLDING: nse_shareholding_fetch,
}

_SPECS = (
    SourceSpec(
        name=YFINANCE,
        capabilities=frozenset(
            {
                MCAP,
                SHARES,
                FREE_FLOAT_SHARES,
                SECTOR,
                INDUSTRY,
                PE,
                ROE,
                BOOK_VALUE,
                DIVIDEND_YIELD,
                EPS,
                INSIDER,
            }
        ),
        cost="fast",
        priority=0,
        timeout_s=20.0,
        rate_per_sec=3.0,
        ttl_s=86_400,
    ),
    SourceSpec(
        name=NSE_SHAREHOLDING,
        capabilities=frozenset({PROMOTER, PUBLIC}),
        cost="medium",
        priority=0,
        timeout_s=20.0,
        rate_per_sec=1.0,
        ttl_s=7 * 86_400,
    ),
    SourceSpec(
        name=SCREENER,
        capabilities=frozenset({ROE, ROCE, MCAP, BOOK_VALUE, SECTOR, PROMOTER, PE}),
        cost="slow",
        priority=0,
        timeout_s=40.0,
        rate_per_sec=0.5,
        ttl_s=7 * 86_400,
    ),
)


def default_registry() -> SourceRegistry:
    """Fresh registry holding the production source specs."""
    return SourceRegistry(_SPECS)


def default_rate_limiters() -> dict[str, RateLimiter]:
    return {spec.name: RateLimiter(rate_per_sec=spec.rate_per_sec) for spec in _SPECS}


# --------------------------------------------------------------------------- #
# Source-health persistence (Phase 2.4) — metadata-backed, best-effort.
# --------------------------------------------------------------------------- #
#: Key in the ``metadata`` table of ``myra_metadata.db``.
HEALTH_META_KEY = "enrichment_source_health"


def persist_health(registry: SourceRegistry) -> bool:
    """Persist ``registry`` health to the metadata table. Never raises."""
    import json

    try:
        from myra_app.librarian_core import LibrarianCore

        lib = LibrarianCore(read_only=False)
        try:
            lib._meta_conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (HEALTH_META_KEY, json.dumps(registry.snapshot(), default=str)),
            )
            lib._meta_conn.commit()
        finally:
            lib.close()
        return True
    except Exception as exc:  # noqa: BLE001 - health persistence is best-effort
        logger.debug("persist source health failed: %s", exc)
        return False


def load_health(registry: SourceRegistry) -> bool:
    """Restore persisted source health into ``registry``. Never raises."""
    import json

    try:
        from myra_app.librarian_core import LibrarianCore

        lib = LibrarianCore(read_only=True)
        try:
            row = lib._meta_conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (HEALTH_META_KEY,)
            ).fetchone()
        finally:
            lib.close()
        if row and row[0]:
            registry.restore_health(json.loads(row[0]))
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("load source health failed: %s", exc)
    return False


# --------------------------------------------------------------------------- #
# Derived free-float metrics
# --------------------------------------------------------------------------- #
def derive_free_float(values: dict) -> dict:
    """Fill ``free_float_pct``/``_shares``/``_market_cap`` when derivable.
    Returns a *new* dict; never overwrites an already-present value.
    """
    out = dict(values)
    pct = out.get(FREE_FLOAT_PCT)
    if pct is None:
        public = out.get(PUBLIC)
        promoter = out.get(PROMOTER)
        if public is not None:
            pct = float(public)
        elif promoter is not None:
            pct = 100.0 - float(promoter)
        if pct is not None and 0.0 < pct <= 100.0:
            out[FREE_FLOAT_PCT] = pct
        else:
            pct = None

    if pct is not None:
        if out.get(FREE_FLOAT_SHARES) is None and out.get(SHARES) is not None:
            try:
                out[FREE_FLOAT_SHARES] = float(out[SHARES]) * pct / 100.0
            except (TypeError, ValueError):
                pass
        if out.get(FREE_FLOAT_MCAP) is None and out.get(MCAP) is not None:
            try:
                out[FREE_FLOAT_MCAP] = float(out[MCAP]) * pct / 100.0
            except (TypeError, ValueError):
                pass
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
#: Columns we are allowed to write into ``fundamentals``.
_WRITABLE = frozenset(
    {
        MCAP,
        SHARES,
        FREE_FLOAT_SHARES,
        FREE_FLOAT_PCT,
        FREE_FLOAT_MCAP,
        PROMOTER,
        PUBLIC,
        INSIDER,
        SECTOR,
        INDUSTRY,
        PE,
        ROE,
        BOOK_VALUE,
        DIVIDEND_YIELD,
        EPS,
    }
)


def resolve_symbol(
    symbol: str,
    want: Iterable[str] = DEFAULT_WANT,
    *,
    registry: Optional[SourceRegistry] = None,
    cache: Optional[TtlCache] = None,
    rate_limiters: Optional[dict] = None,
) -> tuple[dict, dict]:
    """Resolve all wanted capabilities for ``symbol`` (no writes).

    Returns ``(values, provenance)`` where ``provenance[metric] = source_name``.
    Derived free-float metrics are included in ``values``.
    """
    registry = registry or default_registry()
    cache = cache or TtlCache()
    rate_limiters = rate_limiters or default_rate_limiters()
    values, provenance = resolve_many(
        want,
        symbol,
        SOURCE_FETCHERS,
        registry,
        cache=cache,
        cache_base=f"fundamentals:{symbol.upper()}",
        rate_limiters=rate_limiters,
    )
    values = derive_free_float(values)
    return sanitize_metrics(values), provenance


def enrich_symbol(
    symbol: str,
    conn,
    want: Iterable[str] = DEFAULT_WANT,
    *,
    dry_run: bool = True,
    registry: Optional[SourceRegistry] = None,
    cache: Optional[TtlCache] = None,
    rate_limiters: Optional[dict] = None,
) -> dict:
    """Resolve + (optionally) fill-only write one symbol.

    Returns a report dict ``{symbol, values, provenance, written, dry_run}``.
    ``written`` is the rowcount from the fill-only update (0 when dry-run or
    nothing safe to write).
    """
    values, provenance = resolve_symbol(
        symbol,
        want,
        registry=registry,
        cache=cache,
        rate_limiters=rate_limiters,
    )
    report = {
        "symbol": symbol,
        "values": values,
        "provenance": provenance,
        "written": 0,
        "dry_run": dry_run,
    }
    if dry_run or conn is None or not values:
        return report

    now = datetime.now().isoformat()
    writable = {k: v for k, v in values.items() if k in _WRITABLE}
    # Stamp the row refresh time. Deliberately NOT ``last_fundamental_update``:
    # that column gates ``_refresh_stale_shares_outstanding`` ('' < date('now','-90 days')''),
    # so stamping it here would mark a row's shares as fresh and permanently
    # suppress the shares backfill for symbols we merely enriched for other metrics.
    writable["last_updated"] = now
    # ``last_updated`` must never be lost to COALESCE semantics: it is always a
    # real timestamp, so it wins naturally. ``symbol`` is the key.
    report["written"] = update_fill_only(
        conn,
        "fundamentals",
        "symbol",
        symbol.upper(),
        writable,
        zero_guarded=FUNDAMENTALS_ZERO_GUARDED,
    )
    return report


def enrich_batch(
    symbols: Iterable[str],
    conn,
    want: Iterable[str] = DEFAULT_WANT,
    *,
    dry_run: bool = True,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    progress_cb: Optional[Callable[[int, int, dict], None]] = None,
    registry: Optional[SourceRegistry] = None,
    cache: Optional[TtlCache] = None,
    rate_limiters: Optional[dict] = None,
) -> dict:
    """Enrich many symbols with cooperative cancel/pause checkpoints.

    Writes are committed per symbol (cheap, idempotent, resumable). Returns an
    aggregate report ``{total, written, resolved, failed, dry_run}``.
    """
    from myra_app.feature_enrichment import _checkpoint

    registry = registry or default_registry()
    cache = cache or TtlCache()
    rate_limiters = rate_limiters or default_rate_limiters()
    symbols = list(symbols)
    total = len(symbols)
    written = resolved = failed = 0

    for idx, symbol in enumerate(symbols, start=1):
        _checkpoint(cancel_event, pause_event)
        try:
            report = enrich_symbol(
                symbol,
                conn,
                want,
                dry_run=dry_run,
                registry=registry,
                cache=cache,
                rate_limiters=rate_limiters,
            )
        except Exception as exc:  # noqa: BLE001 - one symbol must never abort
            failed += 1
            logger.warning("enrich failed for %s: %s", symbol, exc)
            continue
        if report["values"]:
            resolved += 1
        if report["written"]:
            written += 1
            if conn is not None:
                conn.commit()
        if progress_cb is not None:
            try:
                progress_cb(idx, total, report)
            except Exception:  # noqa: BLE001
                pass
    if conn is not None and not dry_run:
        conn.commit()
    return {
        "total": total,
        "written": written,
        "resolved": resolved,
        "failed": failed,
        "dry_run": dry_run,
    }
