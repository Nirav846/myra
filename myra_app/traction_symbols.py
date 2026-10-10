"""Automated fund_traction symbol resolution (Phase 2.7).

``fund_traction.symbol`` is unstable across rows: when the upstream feed
supplies ``entry_estimate.nse`` the key is already a canonical NSE ticker, but
otherwise it falls back to a Trendlyne-style long name (``ZYDUSLIFESCIENCES``
for ``ZYDUSLIFE``, ``ONE97COMMUNICATIONS`` for ``PAYTM``, ``BOSCH`` for
``BOSCHLTD``, ...).  Only canonical rows join to ``technical_data`` /
``fundamentals``, so before this module the Traction Board showed price /
market-cap for only ~16% of names.

This module resolves every raw key to a canonical ticker through a fully
automated, self-updating, retry-bounded chain, and it **never hides a stock**:

  1. ``symbol_alias``     persisted resolution (written back on every success)
  2. ``symbols_master``   exact normalised-name match (in-DB, unambiguous only)
  3. ``name_to_nse.csv``  curated company-name -> NSE map
  4. ``yfinance``         name search / ``.NS`` / ``.BO`` probe (bounded retries)

The resolved ticker is written back into ``fund_traction.nse`` (fill-only: an
existing value is never clobbered) and into ``symbol_alias`` so the next run is
cheap.  Per-key state lives in ``traction_symbol_state`` (meta DB): after
``max_retries`` failures a key is parked and not re-probed until
``retry_cooldown_days`` elapse, so a permanently-unlistable SME name can never
drive an unbounded network loop.

Rows that genuinely cannot be resolved are left in place -- never deleted, never
filtered out of a read -- and simply carry no market data.  Callers surface them
with a "no market data" flag instead of hiding them.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sqlite3
from datetime import datetime
from typing import Callable, Iterable, Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.symbol_identity import normalize_name, write_aliases

logger = logging.getLogger(__name__)

#: State table (meta DB). Created locally, like fund_traction_sync's own tables.
STATE_TABLE = "traction_symbol_state"

#: After this many consecutive failed probes a key is parked until the cooldown.
DEFAULT_MAX_RETRIES = 3
#: A parked key is re-probed once this many days have passed (keeps it fresh).
DEFAULT_RETRY_COOLDOWN_DAYS = 30

#: Curated company-name -> NSE map shipped with the traction source repo.
NAME_TO_NSE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cross-fund-holdings-traction",
    "config",
    "name_to_nse.csv",
)


@dataclasses.dataclass(frozen=True)
class Resolution:
    """One resolved raw key -> canonical ticker."""

    code: str
    source: str


def _db_path(db_key: str) -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP[db_key])


def _compact(value: Optional[str]) -> str:
    """Uppercase, alphanumerics only -- no corporate-suffix stripping."""
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _ensure_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
            raw_key      TEXT PRIMARY KEY,
            resolved     TEXT,
            source       TEXT,
            status       TEXT,
            attempts     INTEGER DEFAULT 0,
            last_attempt TEXT,
            last_error   TEXT,
            updated_at   TEXT
        )
        """
    )


def load_name_to_nse(path: Optional[str] = None) -> list[tuple[str, str]]:
    """Return ``[(company_name, nse_code), ...]`` from the curated CSV.

    Best-effort: a missing/garbled file yields an empty list (local resolution
    then simply relies on ``symbols_master`` / ``symbol_alias``).
    """
    path = path or NAME_TO_NSE_PATH
    if not os.path.isfile(path):
        return []
    import csv

    out: list[tuple[str, str]] = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("company_name") or "").strip()
                code = (row.get("nse") or "").strip().upper()
                if name and code:
                    out.append((name, code))
    except (OSError, csv.Error) as exc:  # pragma: no cover - defensive
        logger.warning("name_to_nse load failed (%s): %s", path, exc)
        return []
    return out


def _build_index(
    master_rows: Iterable[tuple[str, Optional[str]]],
    alias_rows: Iterable[tuple[str, str]],
    csv_rows: Iterable[tuple[str, str]],
) -> dict:
    """Build the local lookup tables used by :func:`resolve_local`.

    ``symbols_master`` names that map to more than one symbol are dropped
    (ambiguous -- never guessed).  csv/most-likely-first wins for duplicates.
    """
    master_by_name: dict[str, str] = {}
    ambiguous: set[str] = set()
    identity: set[str] = set()
    for symbol, name in master_rows:
        sym = (symbol or "").strip().upper()
        if not sym:
            continue
        identity.add(sym)
        key = normalize_name(name)
        if not key:
            continue
        if key in master_by_name and master_by_name[key] != sym:
            ambiguous.add(key)
        else:
            master_by_name[key] = sym
    for key in ambiguous:
        master_by_name.pop(key, None)

    csv_by_name: dict[str, str] = {}
    for name, code in csv_rows:
        key = normalize_name(name)
        key_compact = _compact(name)
        for k in (key, key_compact):
            if k and k not in csv_by_name:
                csv_by_name[k] = code.upper()

    alias: dict[str, str] = {}
    for a, c in alias_rows:
        a_up, c_up = (a or "").strip().upper(), (c or "").strip().upper()
        if a_up and c_up:
            alias[a_up] = c_up

    return {
        "master": master_by_name,
        "csv": csv_by_name,
        "alias": alias,
        "identity": identity,
    }


def resolve_local(
    name: Optional[str], raw_key: Optional[str], index: dict
) -> Optional[Resolution]:
    """Resolve one raw key using only local, deterministic sources.

    Order: persisted alias -> master-name match -> curated csv.  Returns ``None``
    when nothing local matches (caller may then fall back to the network).
    """
    raw = (raw_key or "").strip().upper()
    if index["alias"].get(raw):
        return Resolution(index["alias"][raw], "alias")

    keys: list[str] = []
    for cand in (normalize_name(name), normalize_name(raw), _compact(raw)):
        if cand and cand not in keys:
            keys.append(cand)
    for k in keys:
        if k in index["master"]:
            return Resolution(index["master"][k], "master_name")
    for k in keys:
        if k in index["csv"]:
            return Resolution(index["csv"][k], "name_to_nse")
    return None


def _ticker_universe(
    tech_conn: sqlite3.Connection,
    val_conn: sqlite3.Connection,
) -> set[str]:
    """Every ticker we actually hold *market data* for (technical + fundamentals).

    Deliberately excludes ``symbols_master``: a name that resolves to a listing
    we hold no price/fundamental rows for is reported as ``resolved_no_data`` so
    the UI can say "no market data" rather than "data is coming".
    """
    universe: set[str] = set()
    try:
        for (sym,) in tech_conn.execute("SELECT DISTINCT symbol FROM technical_data"):
            if sym:
                universe.add(str(sym).strip().upper())
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.debug("technical universe query failed: %s", exc)
    try:
        for (sym,) in val_conn.execute("SELECT symbol FROM fundamentals"):
            if sym:
                universe.add(str(sym).strip().upper())
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.debug("fundamentals universe query failed: %s", exc)
    return universe


def _read_state(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        return {
            r[0]: {
                "resolved": r[1],
                "source": r[2],
                "status": r[3],
                "attempts": int(r[4] or 0),
                "last_attempt": r[5],
                "last_error": r[6],
            }
            for r in conn.execute(
                f"SELECT raw_key, resolved, source, status, attempts, "
                f"last_attempt, last_error FROM {STATE_TABLE}"
            )
        }
    except sqlite3.Error:
        return {}


def _write_state(
    conn: sqlite3.Connection,
    raw_key: str,
    resolved: Optional[str],
    source: Optional[str],
    status: str,
    attempts: int,
    error: Optional[str],
) -> None:
    now = datetime.now().isoformat()
    conn.execute(
        f"""
        INSERT INTO {STATE_TABLE}
            (raw_key, resolved, source, status, attempts, last_attempt,
             last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_key) DO UPDATE SET
            resolved     = excluded.resolved,
            source       = excluded.source,
            status       = excluded.status,
            attempts     = excluded.attempts,
            last_attempt = excluded.last_attempt,
            last_error   = excluded.last_error,
            updated_at   = excluded.updated_at
        """,
        (raw_key, resolved, source, status, attempts, now, error, now),
    )


def _cooled_down(last_attempt: Optional[str], days: int) -> bool:
    """True when a parked key is allowed to be re-probed."""
    if not last_attempt:
        return True
    try:
        ts = datetime.fromisoformat(last_attempt)
    except (TypeError, ValueError):
        return True
    return (datetime.now() - ts).days >= days


def _yf_has_price(code: str, exchange: str, yf) -> bool:
    try:
        info = yf.Ticker(f"{code}.{exchange}").fast_info
        price = info.get("last_price") if hasattr(info, "get") else None
        return bool(price)
    except Exception:  # noqa: BLE001 - any yfinance/network failure = "no price"
        return False


def _yfinance_probe(name: Optional[str], raw_key: Optional[str]) -> Optional[str]:
    """Best-effort network resolution. Returns a canonical ticker or ``None``.

    Never raises: a missing yfinance install, a rate-limit or a network error is
    treated as "not resolvable this run", leaving the key for a later retry.
    """
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        logger.debug("yfinance unavailable for symbol probe: %s", exc)
        return None

    nm = (name or "").strip()
    if nm:
        try:
            quotes = yf.Search(nm, max_results=8).quotes or []
        except Exception:  # noqa: BLE001
            quotes = []
        for q in quotes:
            sym = str(q.get("symbol") or "").upper()
            exch = str(q.get("exchange") or "").upper()
            if sym.endswith(".NS") and exch in ("NSI", "NSE", "NSEI", "NS", ""):
                code = sym[:-3]
                if _yf_has_price(code, "NS", yf):
                    return code

    code = (raw_key or "").strip().upper()
    if code:
        for exch in ("NS", "BO"):
            if _yf_has_price(code, exch, yf):
                return code
    return None


def _alias_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    try:
        return conn.execute("SELECT alias, canonical FROM symbol_alias").fetchall()
    except sqlite3.Error:
        return []


def backfill_traction_nse(
    dry_run: bool = True,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_cooldown_days: int = DEFAULT_RETRY_COOLDOWN_DAYS,
    max_probes: Optional[int] = None,
    remote_probe: Optional[
        Callable[[Optional[str], Optional[str]], Optional[str]]
    ] = None,
    meta_conn: Optional[sqlite3.Connection] = None,
    val_conn: Optional[sqlite3.Connection] = None,
    tech_conn: Optional[sqlite3.Connection] = None,
    name_to_nse_path: Optional[str] = None,
) -> dict:
    """Resolve every ``fund_traction.symbol`` to a canonical ticker.

    ``dry_run=True`` (default) resolves and reports without writing.  The network
    probe only runs when ``dry_run=False`` (or an explicit ``remote_probe`` is
    supplied) so callers can preview offline.

    Writes are additive and idempotent:

    * ``fund_traction.nse`` is filled only where it is currently empty
      (an existing value is never clobbered);
    * ``symbol_alias`` is upserted (``alias`` PK);
    * ``traction_symbol_state`` records attempts so a permanently-unresolvable
      key stops being probed after ``max_retries`` (until the cooldown elapses).

    ``max_probes`` caps the total network probes in a single run so a large
    backlog can never hang one invocation; leftover keys are deferred to the
    next run (nothing is lost, nothing is hidden).

    No row is ever deleted or filtered; an unresolved stock simply keeps an empty
    ``nse`` and is shown without market data.
    """
    own: dict[str, bool] = {}
    if meta_conn is None:
        meta_conn = sqlite3.connect(_db_path("meta"))
        own["meta"] = True
    if val_conn is None:
        val_conn = sqlite3.connect(_db_path("valuation"))
        own["val"] = True
    if tech_conn is None:
        try:
            tech_conn = sqlite3.connect(
                f"file:{_db_path('technical')}?mode=ro", uri=True
            )
        except sqlite3.Error:
            tech_conn = sqlite3.connect(":memory:")
        own["tech"] = True

    probe = remote_probe
    if probe is None and not dry_run:
        probe = _yfinance_probe

    try:
        index = _build_index(
            meta_conn.execute("SELECT symbol, name FROM symbols_master").fetchall(),
            _alias_rows(meta_conn),
            load_name_to_nse(name_to_nse_path),
        )
        universe = _ticker_universe(tech_conn, val_conn)
        # DDL only on a real run; a dry run must leave the DB byte-identical.
        if not dry_run:
            _ensure_state_table(meta_conn)
        state = _read_state(meta_conn)

        rows = val_conn.execute(
            "SELECT symbol, MAX(name), MAX(nse) FROM fund_traction GROUP BY symbol"
        ).fetchall()

        report = {
            "checked": len(rows),
            "already": 0,
            "resolved": 0,
            "resolved_no_data": 0,
            "unresolved": 0,
            "parked": 0,
            "deferred": 0,
            "probed": 0,
            "written": 0,
            "dry_run": dry_run,
            "samples": [],
        }

        for raw, name, existing_nse in rows:
            raw = (raw or "").strip()
            if not raw:
                continue
            existing = (existing_nse or "").strip().upper()
            st = state.get(raw, {})
            attempts = int(st.get("attempts") or 0)
            code: Optional[str] = None
            source: Optional[str] = None

            if existing:
                code, source = existing, "existing"
                report["already"] += 1
            else:
                res = resolve_local(name, raw, index)
                if res:
                    code, source = res.code, res.source
                elif max_probes is not None and report["probed"] >= max_probes:
                    report["deferred"] += 1
                    continue
                elif probe is not None:
                    cooled = _cooled_down(st.get("last_attempt"), retry_cooldown_days)
                    if attempts < max_retries or cooled:
                        if attempts >= max_retries:
                            attempts = 0  # fresh retry window
                        report["probed"] += 1
                        error: Optional[str] = None
                        try:
                            code = probe(name, raw)
                        except Exception as exc:  # noqa: BLE001
                            code, error = None, str(exc)
                        if code:
                            source = "yfinance"
                        else:
                            attempts += 1
                            report["unresolved"] += 1
                            if not dry_run:
                                _write_state(
                                    meta_conn,
                                    raw,
                                    None,
                                    None,
                                    "unresolved",
                                    attempts,
                                    error,
                                )
                            continue
                    else:
                        report["parked"] += 1
                        continue

            if not code:
                report["unresolved"] += 1
                continue

            in_universe = code in universe
            status = "resolved" if in_universe else "resolved_no_data"
            report["resolved" if in_universe else "resolved_no_data"] += 1
            if len(report["samples"]) < 12:
                report["samples"].append(
                    {"raw": raw, "name": name, "code": code, "source": source}
                )

            if not dry_run:
                if not existing:
                    val_conn.execute(
                        "UPDATE fund_traction SET nse = ? WHERE symbol = ? "
                        "AND (nse IS NULL OR TRIM(nse) = '')",
                        (code, raw),
                    )
                    report["written"] += 1
                write_aliases(
                    meta_conn, {raw: code}, source=source or "traction", confidence=1.0
                )
                _write_state(meta_conn, raw, code, source, status, attempts, None)

        if not dry_run:
            val_conn.commit()
            meta_conn.commit()
        return report
    finally:
        for key, conn in (("meta", meta_conn), ("val", val_conn), ("tech", tech_conn)):
            if own.get(key) and conn is not None:
                conn.close()
