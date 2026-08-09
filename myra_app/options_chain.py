"""
NSE Option-Chain Fetcher + PCR Computation
-------------------------------------------
Pure data + math module — fetches live option-chain data from NSE India's
free API, computes Put-Call Ratio (PCR) and classifies market regime.

Persistence layer stores PCR snapshots in myra_options.db so that
base_strategy can consume the latest PCR as a market-regime signal
without hitting NSE on every call.

NSE blocks naive requests; we use a cookie-warm-up session
(same proven pattern as data_sources/nse_institutional.py).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import requests

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDICES: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")

_NSE_BASE = "https://www.nseindia.com"
_NSE_API_INDEX = "https://www.nseindia.com/api/option-chain-indices"
_NSE_API_EQUITY = "https://www.nseindia.com/api/option-chain-equities"

_NSE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _nse_session(timeout: int = 10) -> requests.Session:
    """Create a requests.Session with a valid NSE cookie via warm-up GET.

    NSE requires a cookie set by the HTML page before the JSON API
    will respond.  This mirrors the pattern proven in
    ``data_sources/nse_institutional.py``.
    """
    session = requests.Session()
    session.headers.update(_NSE_HEADERS)
    session.get(_NSE_BASE, timeout=timeout)
    return session


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_option_chain(symbol: str, kind: str = "auto") -> dict[str, Any] | None:
    """Fetch the raw option-chain JSON payload for *symbol*.

    Parameters
    ----------
    symbol:
        Ticker symbol, e.g. ``"NIFTY"`` or ``"RELIANCE"``.
    kind:
        ``"auto"`` (default) – index if *symbol* is in :data:`INDICES`,
        equity otherwise.
        ``"index"`` / ``"equity"`` – force the endpoint.

    Returns
    -------
    dict | None
        Parsed JSON body on success; ``None`` on any network / HTTP error
        or 429 throttle.
    """
    try:
        # Determine endpoint ---------------------------------------------------
        if kind == "auto":
            endpoint = _NSE_API_INDEX if symbol.upper() in INDICES else _NSE_API_EQUITY
        elif kind == "index":
            endpoint = _NSE_API_INDEX
        elif kind == "equity":
            endpoint = _NSE_API_EQUITY
        else:
            raise ValueError(f"Invalid kind={kind!r}; use 'auto', 'index', or 'equity'")

        session = _nse_session()
        resp = session.get(
            endpoint,
            params={"symbol": symbol.upper()},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            logger.warning("NSE throttled option-chain request for %s (429)", symbol)
        else:
            logger.warning(
                "HTTP %s fetching option chain for %s: %s", status, symbol, exc
            )
        return None
    except Exception as exc:  # noqa: BLE001 – graceful degradation
        logger.warning("Failed to fetch option chain for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def compute_pcr(
    pe_rows: list[dict[str, Any]] | None,
    ce_rows: list[dict[str, Any]] | None,
) -> float | None:
    """Compute Put-Call Ratio from open-interest rows.

    Parameters
    ----------
    pe_rows, ce_rows:
        Lists of dicts each containing ``"open_interest"`` (int/float).

    Returns
    -------
    float | None
        ``sum(PE.oi) / sum(CE.oi)`` or ``None`` when either total is zero
        / inputs are ``None``.
    """
    if not pe_rows or not ce_rows:
        return None

    pe_oi = sum(row.get("open_interest", 0) for row in pe_rows)
    ce_oi = sum(row.get("open_interest", 0) for row in ce_rows)

    if ce_oi == 0:
        return None

    return pe_oi / ce_oi


def pcr_regime(pcr: float | None) -> str:
    """Classify market regime from PCR.

    Thresholds (documented decision — equal to boundary → BULLISH / BEARISH):
        PCR >= 1.2  →  ``"BULLISH"``
        PCR <= 0.8  →  ``"BEARISH"``
        otherwise    →  ``"NEUTRAL"``

    ``None`` input maps to ``"NEUTRAL"``.
    """
    if pcr is None:
        return "NEUTRAL"
    if pcr >= 1.2:
        return "BULLISH"
    if pcr <= 0.8:
        return "BEARISH"
    return "NEUTRAL"


def parse_option_chain(
    data: dict[str, Any],
    atm_window: int = 10,
) -> dict[str, Any]:
    """Parse raw NSE option-chain JSON into a clean structure.

    Only records matching the **nearest expiry** are kept.  Within that
    expiry window, *atm_window* strikes on each side of ATM are used
    for PCR calculation.

    Parameters
    ----------
    data:
        Raw JSON payload from :func:`fetch_option_chain`.
    atm_window:
        Number of strikes on each side of ATM to include in PCR
        (default 10 → ~21 strikes total).

    Returns
    -------
    dict
        ``{"symbol", "spot", "expiry", "atm_strike", "strikes",
          "pcr", "total_ce_oi", "total_pe_oi", "ce_rows", "pe_rows"}``
        On empty records ``pcr`` will be ``None``.
    """
    empty: dict[str, Any] = {
        "symbol": None,
        "spot": None,
        "expiry": None,
        "atm_strike": None,
        "strikes": [],
        "pcr": None,
        "total_ce_oi": 0,
        "total_pe_oi": 0,
        "ce_rows": [],
        "pe_rows": [],
    }

    records = data.get("records", {})
    if not records:
        return empty

    all_data = records.get("data", [])
    expiry_dates = records.get("expiryDates", [])
    underlying_value = records.get("underlyingValue")

    if not all_data or not expiry_dates:
        return empty

    # Nearest expiry is first in the list
    nearest_expiry = expiry_dates[0]
    spot = underlying_value if underlying_value is not None else None

    # Filter to nearest expiry only
    filtered = [r for r in all_data if r.get("expiryDate") == nearest_expiry]
    if not filtered:
        return empty

    # Collect all strikes and find ATM
    all_strikes = sorted({r.get("strikePrice", 0) for r in filtered})
    if spot is not None and all_strikes:
        atm_strike = min(all_strikes, key=lambda s: abs(s - spot))
    else:
        atm_strike = all_strikes[len(all_strikes) // 2] if all_strikes else None

    # Build strike-range window for PCR
    if atm_strike is not None:
        in_window = [
            s
            for s in all_strikes
            if abs(s - atm_strike)
            <= atm_window
            * (all_strikes[1] - all_strikes[0] if len(all_strikes) > 1 else 0)
        ]
        # Safer: simply pick the 2*atm_window+1 strikes centred on ATM
        if len(all_strikes) > 1:
            step = all_strikes[1] - all_strikes[0]
            lo = atm_strike - atm_window * step
            hi = atm_strike + atm_window * step
            in_window = [s for s in all_strikes if lo <= s <= hi]
        else:
            in_window = list(all_strikes)
    else:
        in_window = list(all_strikes)

    window_set = set(in_window)

    # Build per-strike rows
    ce_rows: list[dict[str, Any]] = []
    pe_rows: list[dict[str, Any]] = []
    strikes: list[dict[str, Any]] = []

    for rec in filtered:
        strike = rec.get("strikePrice")
        if strike not in window_set:
            continue

        ce = rec.get("CE", {})
        pe = rec.get("PE", {})

        ce_row = {
            "strike": strike,
            "open_interest": ce.get("openInterest", 0),
            "change_oi": ce.get("changeinOpenInterest", 0),
            "ltp": ce.get("lastPrice", 0),
            "iv": ce.get("impliedVolatility", 0),
        }
        pe_row = {
            "strike": strike,
            "open_interest": pe.get("openInterest", 0),
            "change_oi": pe.get("changeinOpenInterest", 0),
            "ltp": pe.get("lastPrice", 0),
            "iv": pe.get("impliedVolatility", 0),
        }

        ce_rows.append(ce_row)
        pe_rows.append(pe_row)
        strikes.append({"strike": strike, "ce": ce_row, "pe": pe_row})

    pcr = compute_pcr(pe_rows, ce_rows)

    return {
        "symbol": data.get("records", {}).get("underlyingValue"),
        "spot": spot,
        "expiry": nearest_expiry,
        "atm_strike": atm_strike,
        "strikes": strikes,
        "pcr": pcr,
        "total_ce_oi": sum(r["open_interest"] for r in ce_rows),
        "total_pe_oi": sum(r["open_interest"] for r in pe_rows),
        "ce_rows": ce_rows,
        "pe_rows": pe_rows,
    }


# ---------------------------------------------------------------------------
# Persistence layer — myra_options.db sidecar
# ---------------------------------------------------------------------------
# Pattern mirrors news_sentiment.py: direct sqlite3 connections, WAL mode,
# lazy init so importing the module never touches the DB.


def _get_db_path() -> str:
    """Return the path to myra_options.db."""
    return os.path.join(DB_DIR, "myra_options.db")


def _init_db() -> None:
    """Create option_chain and pcr_snapshot tables if they don't exist.

    Idempotent — safe to call repeatedly.  Uses direct sqlite3 connections
    (not librarian.py) following the news_sentiment.py precedent for
    standalone sidecar databases.
    """
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS option_chain (
            symbol TEXT,
            kind TEXT,
            spot REAL,
            pcr REAL,
            regime TEXT,
            total_ce_oi INTEGER,
            total_pe_oi INTEGER,
            atm_strike REAL,
            expiry TEXT,
            strike_count INTEGER,
            raw_json TEXT,
            created_at TEXT
        )
    """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pcr_snapshot (
            index_symbol TEXT PRIMARY KEY,
            pcr REAL,
            regime TEXT,
            spot REAL,
            expiry TEXT,
            updated_at TEXT
        )
    """
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Store / Retrieve PCR snapshots
# ---------------------------------------------------------------------------


def store_pcr_snapshot(snapshot: dict) -> None:
    """UPSERT a PCR snapshot into the pcr_snapshot table.

    Parameters
    ----------
    snapshot:
        Must contain keys: ``index_symbol``, ``pcr``, ``regime``, ``spot``,
        ``expiry``, ``updated_at``.
    """
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    conn.execute(
        """
        INSERT OR REPLACE INTO pcr_snapshot
            (index_symbol, pcr, regime, spot, expiry, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["index_symbol"],
            snapshot.get("pcr"),
            snapshot.get("regime", "NEUTRAL"),
            snapshot.get("spot"),
            snapshot.get("expiry"),
            snapshot.get("updated_at", datetime.now(timezone.utc).isoformat()),
        ),
    )
    conn.commit()
    conn.close()


def get_latest_pcr_snapshot() -> dict | None:
    """Return the most recent PCR snapshot row, or ``None`` if the DB is
    empty or absent."""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return None

    _init_db()
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT index_symbol, pcr, regime, spot, expiry, updated_at "
        "FROM pcr_snapshot ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row is None:
        return None

    return {
        "index_symbol": row[0],
        "pcr": row[1],
        "regime": row[2],
        "spot": row[3],
        "expiry": row[4],
        "updated_at": row[5],
    }


# ---------------------------------------------------------------------------
# High-level refresh helper
# ---------------------------------------------------------------------------


def refresh_pcr() -> dict:
    """Fetch option-chain for all 4 indices, compute PCR, store snapshots.

    Returns
    -------
    dict
        ``{"snapshots": [...], "updated_at": "<iso>"}``
        On NSE failure: ``{"snapshots": [], "error": "<message>"}``.
        If individual indices fail they are silently skipped (snapshot list
        may be shorter than 4).

    Notes
    -----
    - A 1.0 s delay is inserted between NSE calls to avoid throttling.
    - Exceptions per index are caught so one failure does not abort the loop.
    """
    snapshots: list[dict] = []
    errors: list[str] = []

    for idx, symbol in enumerate(INDICES):
        try:
            raw = fetch_option_chain(symbol)
            if raw is None:
                errors.append(f"{symbol}: fetch returned None")
                continue

            parsed = parse_option_chain(raw)
            pcr_val = parsed.get("pcr")
            regime = pcr_regime(pcr_val)
            now_iso = datetime.now(timezone.utc).isoformat()

            snapshot = {
                "index_symbol": symbol,
                "pcr": pcr_val,
                "regime": regime,
                "spot": parsed.get("spot"),
                "expiry": parsed.get("expiry"),
                "updated_at": now_iso,
            }
            store_pcr_snapshot(snapshot)
            snapshots.append(snapshot)

        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            logger.warning("refresh_pcr failed for %s: %s", symbol, exc)

        # Polite delay between NSE calls (skip after last)
        if idx < len(INDICES) - 1:
            time.sleep(1.0)

    now_iso = datetime.now(timezone.utc).isoformat()

    if not snapshots and errors:
        return {"snapshots": [], "error": "; ".join(errors), "updated_at": now_iso}

    return {"snapshots": snapshots, "updated_at": now_iso}
