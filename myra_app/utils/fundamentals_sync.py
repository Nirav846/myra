import json
import logging
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from myra_app import constants
from myra_app.fetcher import DataFetcher

# --- UPDATED: Import FundamentalManager directly from myra_app ---
from myra_app.fundamental_manager import FundamentalManager
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger("myra.fundamentals_sync")

# Sidecar paths resolve through constants.DB_DIR + LibrarianCore.DB_MAP at call
# time. A module-local `os.path.join(_HERE, "db")` plus hardcoded filenames
# happened to agree with them, but drifted the moment either changed.
IST = timezone(timedelta(hours=5, minutes=30))

#: Trailing trading sessions that must contain a price for a symbol to count as
#: still tradable. Delisted/suspended/demerged names stop printing prices, so a
#: symbol missing from this window is *deprioritised* (and, if it keeps failing,
#: parked) -- never silently dropped, because BSE-only and SME names have no NSE
#: price history at all yet are real, Screener-covered companies.
ACTIVE_SESSION_WINDOW = 7

#: Consecutive source failures before a symbol is parked (skipped entirely).
PARK_AFTER_FAILURES = 5

#: How long a parked symbol is left alone before one more attempt is made.
PARK_DAYS = 30

#: Per-symbol failure backoff, capped. Jittered so retries never sync up.
MAX_BACKOFF_SECONDS = 60.0

#: Statuses that mean "slow down", not "this company has no page".
RATE_LIMIT_STATUSES = frozenset({429, 403, 503})
#: Consecutive rate-limited responses that abort the run (it resumes later).
RATE_LIMIT_ABORT_AFTER = 3
RATE_LIMIT_SLEEP_SECONDS = 120.0

#: metadata keys (myra_metadata.db)
PROGRESS_KEY = "fundamentals_sync_status"
PARK_STATE_KEY = "fundamentals_park_state"


def _db(key):
    """Absolute path of a MYRA sidecar (e.g. _db('valuation'))."""
    return os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP[key])


def partition_index_rows(symbols):
    """Split ``symbols`` into (equities, index_rows).

    Indices and benchmarks (NIFTY 100, INDIA VIX, G-SEC indices) are not
    companies: no upstream source has a fundamentals page for them, so they were
    pure guaranteed-failure noise in the queue. NSE/BSE equity tickers never
    contain whitespace, which makes that a safe discriminator -- and it keeps
    'BSE', which is a real listed company.
    """
    equities, index_rows = [], []
    for sym in symbols:
        if any(ch.isspace() for ch in (sym or "")):
            index_rows.append(sym)
        else:
            equities.append(sym)
    return equities, index_rows


# --------------------------------------------------------------------------- #
# Resumable state + throttling helpers (module-level so they are unit-testable)
# --------------------------------------------------------------------------- #


def load_active_symbols(db_dir=None):
    """Symbols that printed a price in the last ``ACTIVE_SESSION_WINDOW`` sessions.

    Read-only against the technical sidecar. Returns an empty set when the DB is
    unavailable, which simply means "no ordering preference" rather than "nothing
    is tradable" -- callers must treat an empty set as "unknown", not "empty".
    """
    path = os.path.join(db_dir or constants.DB_DIR, LibrarianCore.DB_MAP["technical"])
    if not os.path.exists(path):
        return set()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return set()
    try:
        dates = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT ?",
                (ACTIVE_SESSION_WINDOW,),
            ).fetchall()
        ]
        if not dates:
            return set()
        cutoff = dates[-1]
        return {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM technical_data WHERE date >= ?",
                (cutoff,),
            ).fetchall()
        }
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def load_park_state(meta_conn):
    """Read the persisted per-symbol failure/park bookkeeping."""
    row = meta_conn.execute(
        "SELECT value FROM metadata WHERE key = ?", (PARK_STATE_KEY,)
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_park_state(meta_conn, state):
    meta_conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (PARK_STATE_KEY, json.dumps(state)),
    )
    meta_conn.commit()


def is_parked(state, symbol, now):
    """True when ``symbol`` is parked and its park window has not expired."""
    entry = state.get(symbol)
    if not isinstance(entry, dict):
        return False
    until = entry.get("parked_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now
    except (ValueError, TypeError):
        return False


def record_failure(
    state, symbol, now, park_after=PARK_AFTER_FAILURES, park_days=PARK_DAYS
):
    """Increment a symbol's consecutive-failure count, parking it past the limit."""
    entry = state.get(symbol)
    if not isinstance(entry, dict):
        entry = {"fails": 0}
    entry["fails"] = int(entry.get("fails", 0)) + 1
    entry["last_attempt"] = now.isoformat()
    if entry["fails"] >= park_after:
        entry["parked_until"] = (now + timedelta(days=park_days)).isoformat()
    state[symbol] = entry
    return entry


def _backoff_sleep(seconds, cancel_event):
    """Sleep ``seconds`` (with jitter), waking early if cancellation is requested."""
    # Jitter so a run that failed a burst of symbols does not retry them all in
    # lockstep on the next pass.
    jittered = seconds * (0.8 + 0.4 * random.random())
    deadline = time.monotonic() + jittered
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def wait_if_paused(pause_event, cancel_event):
    """Block while paused. Returns False if a cancel arrives while waiting."""
    if pause_event is None:
        return True
    while not pause_event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            return False
        time.sleep(0.25)
    return True


def _load_progress(meta_conn):
    row = meta_conn.execute(
        "SELECT value FROM metadata WHERE key = ?", (PROGRESS_KEY,)
    ).fetchone()
    if row and row[0]:
        try:
            data = json.loads(row[0])
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass
    return {
        "status": "idle",
        "last_processed_symbol": None,
        "updated_count": 0,
        "failed_count": 0,
        "start_time": None,
        "last_sync_date": None,
    }


def _save_progress(meta_conn, progress):
    meta_conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (PROGRESS_KEY, json.dumps(progress)),
    )
    meta_conn.commit()


def _select_symbols_needing_fundamentals(val_conn):
    return [
        r[0]
        for r in val_conn.execute(
            "SELECT symbol FROM fundamentals WHERE "
            "sector IS NULL OR sector = '' OR pe IS NULL OR roe IS NULL OR market_cap IS NULL "
            "ORDER BY symbol"
        ).fetchall()
    ]


def sync_fundamentals(
    force=False,
    task_id: int = None,
    cancel_event=None,
    pause_event=None,
    progress_cb=None,
):
    """Refresh fundamentals from Screener.in (multi-source fallback).

    Designed to be interrupted. Any one of Ctrl+C, a dropped connection or a hard
    power loss leaves the DB consistent and the run *resumable*:

    * the work queue is derived from the DB each run (rows that are already
      complete drop out), so resumption needs no cursor replay;
    * progress and park state are committed per symbol, not every 25;
    * ``cancel_event`` is checked before every symbol and ``pause_event`` blocks
      between them, so an operator Stop actually stops (it used to run to the end
      and leave the UI stuck on "cancelling");
    * symbols that keep failing are parked, never deleted, and wake up on their
      own after :data:`PARK_DAYS`.

    Returns a small summary dict. ``dry`` writes happen only through
    :class:`~myra_app.fundamental_manager.FundamentalManager` (fill-only).
    """
    from myra_app.task_tracker import update

    def _n(message):
        if task_id is not None:
            update(task_id, message)

    # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns the fundamentals table
    if constants.DISABLE_FUNDAMENTAL_WRITERS:
        logger.info(
            "sync_fundamentals skipped: DISABLE_FUNDAMENTAL_WRITERS=True "
            "(upstox_fetcher owns fundamentals)"
        )
        return {"skipped": "DISABLE_FUNDAMENTAL_WRITERS"}

    _n("Checking fundamentals freshness…")
    today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()

    meta_conn = sqlite3.connect(_db("meta"), timeout=10)
    meta_conn.execute("PRAGMA journal_mode=WAL")
    try:
        progress = _load_progress(meta_conn)

        if not force and progress.get("status") == "complete":
            last_date = progress.get("last_sync_date", "")
            if last_date:
                try:
                    days = (
                        datetime.strptime(today, "%Y-%m-%d").date()
                        - datetime.strptime(last_date, "%Y-%m-%d").date()
                    ).days
                    if days < 30:
                        print(f"[MYRA FUNDA] Already synced {days} days ago. Skipping.")
                        return {"skipped": "fresh", "days": days}
                except ValueError:
                    pass

        val_conn = sqlite3.connect(_db("valuation"), timeout=10)
        try:
            symbols_needed = _select_symbols_needing_fundamentals(val_conn)
        finally:
            val_conn.close()

        equities, index_rows = partition_index_rows(symbols_needed)
        if index_rows:
            print(
                f"[MYRA FUNDA] Skipping {len(index_rows)} index/benchmark rows "
                f"(not companies): {', '.join(index_rows[:5])}"
                + ("…" if len(index_rows) > 5 else "")
            )

        now = datetime.now(timezone.utc)
        park_state = load_park_state(meta_conn)
        active = load_active_symbols()
        if not active:
            # Unknown, not empty: keep the old (alphabetic) order.
            print("[MYRA FUNDA] technical_data unavailable — no tradability ordering.")

        queue, parked = [], 0
        for sym in equities:
            if is_parked(park_state, sym, now):
                parked += 1
                continue
            queue.append(sym)
        # Tradable names first; a symbol with no recent price is very likely
        # delisted/suspended but is still attempted (BSE-only and SME names have
        # no NSE price history at all yet are real companies).
        queue.sort(key=lambda s: (s not in active, s))

        if parked:
            print(
                f"[MYRA FUNDA] {parked} symbol(s) parked after repeated failures "
                f"(retried in ~{PARK_DAYS} days)."
            )

        if not queue:
            print("[MYRA FUNDA] All fundamentals up to date.")
            progress["status"] = "complete"
            progress["last_sync_date"] = today
            _save_progress(meta_conn, progress)
            return {"updated": 0, "failed": 0, "queued": 0, "parked": parked}

        print(
            f"[MYRA FUNDA] Starting sync for {len(queue)} symbols "
            f"({len(active & set(queue))} with recent prices)…"
        )
        progress["status"] = "in_progress"
        progress["updated_count"] = 0
        progress["failed_count"] = 0
        progress["start_time"] = now.isoformat()
        _save_progress(meta_conn, progress)
        _n(f"Fetching fundamentals for {len(queue)} symbols…")

        mgr = FundamentalManager(db_dir=constants.DB_DIR)
        mgr.set_fetcher(DataFetcher())

        total = len(queue)
        updated = failed = rate_limited = 0
        consec_fail = consec_rate_limited = 0
        cancelled = False

        for i, symbol in enumerate(queue):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                print("[MYRA FUNDA] Cancel requested — stopping; resumes next run.")
                break
            if not wait_if_paused(pause_event, cancel_event):
                cancelled = True
                print("[MYRA FUNDA] Cancelled while paused.")
                break

            try:
                ok = mgr.fetch_fundamentals(symbol)
            except Exception as exc:  # noqa: BLE001 - one symbol must never abort
                ok = False
                mgr.last_error = f"{type(exc).__name__}: {exc}"
                mgr.last_http_status = None

            status = getattr(mgr, "last_http_status", None)
            if ok:
                updated += 1
                consec_fail = consec_rate_limited = 0
                park_state.pop(symbol, None)
                _record_source_success(
                    symbol,
                    getattr(mgr, "last_source", None),
                    getattr(mgr, "last_owned_fields", None),
                )
            elif status in RATE_LIMIT_STATUSES:
                # The source is asking us to slow down; this is not the symbol's
                # fault, so it must not count toward parking it.
                rate_limited += 1
                consec_fail = 0
                consec_rate_limited += 1
                print(
                    f"  [THROTTLED] {symbol}: HTTP {status} — backing off "
                    f"{int(RATE_LIMIT_SLEEP_SECONDS)}s"
                )
                _backoff_sleep(RATE_LIMIT_SLEEP_SECONDS, cancel_event)
                if consec_rate_limited >= RATE_LIMIT_ABORT_AFTER:
                    print(
                        "[MYRA FUNDA] Screener.in is rate-limiting; aborting run "
                        "— resumes next time."
                    )
                    cancelled = True
                    break
            else:
                failed += 1
                consec_rate_limited = 0
                consec_fail += 1
                record_failure(park_state, symbol, now)
                print(f"  [FAIL] {symbol}: {mgr.last_error or 'unknown failure'}")
                if consec_fail > 1:
                    _backoff_sleep(
                        min(MAX_BACKOFF_SECONDS, 2.0 ** min(consec_fail, 10)),
                        cancel_event,
                    )

            progress["last_processed_symbol"] = symbol
            progress["updated_count"] = updated
            progress["failed_count"] = failed
            # Commit per symbol: WAL makes this cheap, and a hard kill then loses
            # at most the symbol in flight.
            _save_progress(meta_conn, progress)
            save_park_state(meta_conn, park_state)

            done = i + 1
            if progress_cb is not None:
                try:
                    progress_cb(done, total, {"symbol": symbol, "ok": ok})
                except Exception:  # noqa: BLE001 - progress must never break the run
                    logger.debug("progress_cb failed", exc_info=True)
            if done % 25 == 0 or done == total:
                _n(f"Fundamentals: {done}/{total}")
                print(f"[MYRA FUNDA] Progress: {done}/{total}")

        if cancelled:
            # Leave the run resumable — do NOT stamp it complete.
            progress["status"] = "in_progress"
        else:
            progress["status"] = "complete"
            progress["last_sync_date"] = today
        _save_progress(meta_conn, progress)
        save_park_state(meta_conn, park_state)

        print(
            f"[MYRA FUNDA] Done. Updated: {updated}, Failed: {failed}, "
            f"Throttled: {rate_limited}, Parked: {parked}"
            + (" (cancelled)" if cancelled else "")
        )
        _n(
            f"Fundamentals sync {'cancelled' if cancelled else 'complete'}. "
            f"Updated: {updated}"
        )
        return {
            "updated": updated,
            "failed": failed,
            "throttled": rate_limited,
            "queued": total,
            "parked": parked,
            "cancelled": cancelled,
        }
    finally:
        meta_conn.close()


def _record_source_success(symbol, source, owned_fields=None):
    """Record which source owns ``symbol``'s metrics right now (best-effort)."""
    if not source:
        return
    if source in ("screener_in", "screener") and not owned_fields:
        # Screener answered but carried none of the fields it is authoritative
        # for (e.g. a broken top-ratios block). A symbol-level claim would freeze
        # P/E, sector, etc. for the whole takeover window, blocking the very
        # sources that could fill those gaps -- so don't stamp one.
        return
    try:
        from myra_app.source_priority import record_source_success

        record_source_success(symbol, source)
    except Exception:  # noqa: BLE001 - freshness is advisory, never fatal
        logger.debug("could not record source freshness for %s", symbol, exc_info=True)


# Keep backward compatibility
def get_etf_symbols():
    return set()  # not used by fundamentals sync
