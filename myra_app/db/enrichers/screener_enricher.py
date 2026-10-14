"""
Screener.in Fundamentals Enricher

Fetches PBV and ROCE from Screener.in for all symbols in the universe and stores
them in ``screener_fundamentals`` (owned exclusively by this module — see
``enrichment_sources``'s module docstring, which deliberately does *not* write
``roce`` to avoid a second writer).

Phase 8 notes (WS2/WS3/WS4) — why this looks different from the first draft
---------------------------------------------------------------------------
The original implementation had four defects that made a long backfill both slow
and unsafe. All four are addressed here and covered by
``tests/test_screener_enricher.py``:

1. **One write transaction held for the whole run.** ``conn.commit()`` ran once
   after the loop, so a multi-hour run held a write lock on
   ``myra_valuation.db`` for its entire duration (blocking every other writer)
   *and* lost all progress on a crash. Commits are now **per symbol**, matching
   the mature pattern already used by ``utils.fundamentals_sync.sync_fundamentals``.
2. **NULL clobbering.** A symbol whose PBV resolved but whose ROCE did not was
   written as ``roce = NULL``, erasing a previously valid value — a direct
   violation of the "never overwrite a valid metric with a failed fetch" rule.
   Writes are now fill-only via :func:`myra_app.safe_write.update_fill_only`.
3. **Sequential network I/O with a fixed sleep.** One symbol at a time plus a
   flat ``time.sleep(1.5)`` per symbol is almost entirely idle waiting. A bounded
   :class:`~concurrent.futures.ThreadPoolExecutor` now overlaps the *latency*
   while a process-wide, thread-safe :class:`_RequestPacer` keeps the **aggregate**
   request rate at (or below) the original budget. Per-source request pressure
   therefore does not increase; only the idle time disappears.
4. **Duplicate concurrent runs.** Two overlapping ``--enrich-screener``
   invocations (or a manual run racing the scheduler) would double-fetch and
   write. A process-wide lock plus a module-level busy flag makes the second run
   refuse instead.

Every result is reported as a truth-bearing summary (``updated`` / ``failed`` /
``skipped`` / ``partial``) and written to the existing ``sync_log`` status
vocabulary, so an interrupted or degraded run is visible instead of silent.
"""

import logging
import os
import random
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup  # requires beautifulsoup4

from myra_app.constants import DB_DIR, SCREENER_ENRICH_AUTO_ENABLED

logger = logging.getLogger(__name__)


def _source_enabled(name: str) -> bool:
    """Operator switch for a data source (Data Sources panel). Fail-open: a
    config-read error must never silently disable a whole fetch path."""
    try:
        from myra_app.data_sources import source_config

        return source_config.is_enabled(name)
    except Exception:  # noqa: BLE001 - fail open
        return True


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.screener.in/company/",
}
METRICS = {
    "pbv": "Price to book value",
    "roce": "Return on capital employed",
}
CACHE_DAYS = 7

#: Per-HTTP-request timeout. Screener.in occasionally stalls; a bounded socket
#: timeout turns a hang into a clean failure the worker can retry.
HTTP_TIMEOUT_SECONDS = 15

#: Retries for a *transient* failure (connection error / timeout / 5xx / 429).
#: A 404 or an unparseable body is treated as permanent and not retried.
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2.0
RETRY_MAX_DELAY_SECONDS = 30.0

#: Minimum spacing between *consecutive HTTP requests* across all worker threads
#: in a run. This reproduces the original loop's **aggregate** request budget:
#: it slept 1.5 s per symbol and issued ~5 requests per symbol, i.e. one request
#: per ~0.3 s. Pacing per *request* (rather than per symbol) is what makes a
#: multi-worker run safe — the request rate seen by Screener.in is unchanged,
#: only the idle time between requests is absorbed by the workers.
#:
#: Note the distinction: 5 requests/symbol at 0.3 s = the same 1.5 s/symbol the
#: original slept, so this is rate-neutral versus the original, not more
#: aggressive. A *safer* budget can be selected without code change by raising
#: this constant.
REQUEST_INTERVAL_SECONDS = 0.3

#: How many symbols may be in flight at once. Small on purpose: the win comes
#: from overlapping network latency, not from hammering Screener.in.
DEFAULT_MAX_WORKERS = 4

#: Per-batch commit size for the reject mirror. Rejects are advisory, so they do
#: not need a commit per row (that would be a write amplification regression).
REJECT_COMMIT_EVERY = 50


class _RequestPacer:
    """Thread-safe minimum-interval gate shared by every worker in a run.

    The original code did ``time.sleep(1.5)`` per symbol inside a single
    thread. With workers, a plain sleep would multiply the request rate by the
    worker count, so pacing must be *global* — this lock guarantees that no two
    requests start closer together than ``interval``, no matter how many workers
    are active.
    """

    def __init__(self, interval_seconds: float = REQUEST_INTERVAL_SECONDS) -> None:
        self._interval = float(interval_seconds)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_allowed:
                    self._next_allowed = now + self._interval
                    return
                sleep_for = self._next_allowed - now
            time.sleep(sleep_for)

    def penalise(self, seconds: float) -> None:
        """Push the shared gate out after a 429/503 (global cool-down)."""
        with self._lock:
            self._next_allowed = max(
                self._next_allowed, time.monotonic() + float(seconds)
            )


#: Process-wide guard: only one screener enrichment may run at a time.
_RUN_LOCK = threading.Lock()
_RUN_ACTIVE = threading.Event()

#: Per-thread resources. A sqlite3 connection is not safe to share across
#: threads, and a requests.Session is not safe to share either, so both are
#: thread-local and torn down when the worker thread ends.
_local = threading.local()


def _thread_db(db_path: str) -> sqlite3.Connection:
    """This thread's own connection to the valuation sidecar (WAL, own writer)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout = 60000")
    _local.conn = conn
    return conn


def _close_thread_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        _local.conn = None


def _thread_session() -> requests.Session:
    """This thread's own HTTP session (connection reuse + cookie priming)."""
    session = getattr(_local, "session", None)
    if session is not None:
        return session
    session = requests.Session()
    session.headers.update(HEADERS)
    _local.session = session
    return session


def _close_thread_session() -> None:
    session = getattr(_local, "session", None)
    if session is not None:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        _local.session = None


# ── HTTP with bounded retry / backoff (WS3) ───────────────────────────────────


class _PermanentError(Exception):
    """A failure that retrying cannot fix (404, unparseable body)."""


def _request_with_retry(
    method: str,
    url: str,
    *,
    pacer: _RequestPacer,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """GET ``url`` with a shared pacer and bounded retry on transient errors.

    Retries only *transient* conditions: connection errors, timeouts, HTTP 429
    and 5xx. A 4xx that is not 429 (notably 404 — symbol has no Screener page)
    is permanent and returned immediately so it is not retried. Backoff is
    exponential with full jitter, capped, and a 429 additionally pushes the
    shared pacer out so every worker slows down together.
    """
    session = session or _thread_session()
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pacer.wait()
        try:
            resp = session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as exc:  # timeout / connection reset
            last_exc = exc
            if attempt == MAX_ATTEMPTS:
                break
        else:
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if resp.status_code == 429:
                    # Honour a short Retry-After when the server provides one.
                    hint = _retry_after_seconds(resp)
                    pacer.penalise(hint or RETRY_BASE_DELAY_SECONDS)
                if attempt == MAX_ATTEMPTS:
                    return resp
            elif resp.status_code >= 400:
                # 404 etc: this symbol simply has no page. Not worth retrying.
                return resp
            else:
                return resp
        backoff = min(
            RETRY_MAX_DELAY_SECONDS, RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        )
        time.sleep(random.uniform(0, backoff))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("request failed with no response")


def _retry_after_seconds(resp: requests.Response) -> Optional[float]:
    try:
        raw = resp.headers.get("Retry-After")
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _get_company_id(symbol: str, pacer: _RequestPacer) -> Optional[str]:
    """Scrape the Screener.in company page for its numeric company ID."""
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    try:
        resp = _request_with_retry("GET", url, pacer=pacer)
    except requests.RequestException as exc:
        logger.debug(f"company page request failed for {symbol}: {exc}")
        return None
    if resp.status_code != 200:
        logger.debug(f"Failed to fetch company page for {symbol}: {resp.status_code}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    div = soup.find("div", {"data-company-id": True})
    if div:
        return div["data-company-id"]
    for script in soup.find_all("script"):
        if script.string and "company_id" in script.string:
            match = re.search(r'company_id["\']?\s*[:=]\s*["\']?(\d+)', script.string)
            if match:
                return match.group(1)
    return None


def _fetch_metric(
    company_id: str, metric_query: str, pacer: _RequestPacer
) -> Optional[float]:
    """Fetch a single metric from the Screener.in chart API."""
    q = metric_query.replace(" ", "+")
    url = (
        f"https://www.screener.in/api/company/{company_id}/chart/"
        f"?q={q}&days=1825&consolidated=true"
    )
    try:
        resp = _request_with_retry("GET", url, pacer=pacer)
    except requests.RequestException as exc:
        logger.debug(f"Error fetching metric {metric_query}: {exc}")
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    datasets = data.get("datasets", [])
    if not datasets:
        return None
    values = datasets[0].get("values", [])
    if not values:
        return None
    latest = values[-1]
    if isinstance(latest, list) and len(latest) >= 2:
        return float(latest[1])
    return float(latest) if latest is not None else None


def _fetch_symbol_data(symbol: str, pacer: _RequestPacer) -> Dict[str, Optional[float]]:
    """Fetch all metrics for a single symbol (network only, no DB access)."""
    company_id = _get_company_id(symbol, pacer)
    if not company_id:
        return {}
    result: Dict[str, Optional[float]] = {}
    for key, query in METRICS.items():
        val = _fetch_metric(company_id, query, pacer)
        if val is not None:
            result[key] = val
    return result


# ── DB layer ─────────────────────────────────────────────────────────────────


def _ensure_table(conn: sqlite3.Connection):
    """Create screener_fundamentals table if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS screener_fundamentals (
            symbol TEXT PRIMARY KEY,
            pbv REAL,
            roce REAL,
            last_updated TEXT
        )
    """)
    conn.commit()


def _select_symbols() -> List[str]:
    """The universe to enrich: symbols that already have a market cap.

    Isolated so tests can pin the symbol set instead of inheriting whatever the
    shared fixture ``fundamentals`` table happens to contain.
    """
    conn = sqlite3.connect(os.path.join(DB_DIR, "myra_valuation.db"))
    try:
        return [
            row[0]
            for row in conn.execute(
                "SELECT symbol FROM fundamentals WHERE market_cap IS NOT NULL"
            ).fetchall()
        ]
    finally:
        conn.close()


def _write_symbol(db_path: str, symbol: str, data: Dict[str, Optional[float]]) -> int:
    """Fill-only upsert for one symbol, committed immediately.

    ``upsert_row`` is used rather than ``update_fill_only`` because
    ``screener_fundamentals`` is a *sparse* table: a symbol seen for the first
    time has no row yet, and a plain UPDATE would match nothing and silently
    store nothing. On conflict the same statement keeps the AGENTS.md invariant
    — a metric that did not resolve this pass leaves the stored value untouched
    (``COALESCE(excluded.col, table.col)``) instead of being nulled — and
    ``last_updated`` is stamped only when at least one metric actually landed.
    """
    from myra_app.safe_write import upsert_row

    payload = {k: v for k, v in data.items() if v is not None}
    if not payload:
        return 0
    payload["last_updated"] = datetime.now().isoformat()
    conn = _thread_db(db_path)
    upsert_row(
        conn,
        "screener_fundamentals",
        "symbol",
        payload | {"symbol": symbol},
        always_overwrite={"last_updated"},
    )
    conn.commit()
    return 1


# ── Public API ───────────────────────────────────────────────────────────────


def enrich_screener_fundamentals(
    force: bool = False,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    limit: Optional[int] = None,
    progress_cb=None,
) -> dict:
    """Enrich ``screener_fundamentals`` with PBV and ROCE from Screener.in.

    Parameters
    ----------
    force:
        Ignore the per-symbol cache and refresh everything.
    max_workers:
        Bounded worker count. Clamped to ``[1, 16]``. Default
        :data:`DEFAULT_MAX_WORKERS`.
    limit:
        Optional cap on the number of symbols processed (used by tests and by
        an operator smoke run).
    progress_cb:
        Optional ``cb(done, total, symbol, ok)`` invoked as symbols complete.

    Returns a summary dict with ``total`` / ``updated`` / ``failed`` /
    ``skipped`` / ``partial`` / ``cancelled`` / ``elapsed_s``. ``partial`` is
    True whenever some but not all symbols were handled, so a caller can report
    a degraded run rather than a clean success.
    """
    t_start = time.perf_counter()

    if not force and not SCREENER_ENRICH_AUTO_ENABLED:
        logger.info("Screener enricher auto-run disabled. Use force=True to override.")
        return {
            "total": 0,
            "updated": 0,
            "failed": 0,
            "skipped": 0,
            "partial": False,
            "cancelled": False,
            "elapsed_s": 0.0,
            "reason": "auto_run_disabled",
        }
    if not _source_enabled("screener_in"):
        logger.info(
            "Screener.in disabled in Data Sources — skipping screener enricher."
        )
        return {
            "total": 0,
            "updated": 0,
            "failed": 0,
            "skipped": 0,
            "partial": False,
            "cancelled": False,
            "elapsed_s": 0.0,
            "reason": "source_disabled",
        }

    # Duplicate-run prevention: refuse rather than double-fetch and double-write.
    if not _RUN_LOCK.acquire(blocking=False):
        logger.warning(
            "Screener enrichment already running in this process — refusing "
            "a duplicate run."
        )
        return {
            "total": 0,
            "updated": 0,
            "failed": 0,
            "skipped": 0,
            "partial": False,
            "cancelled": False,
            "elapsed_s": 0.0,
            "reason": "already_running",
        }

    # Phase 9: the in-process lock above cannot see a *second OS process*. The
    # manual entry points are separate processes (run_pipeline.py vs the API
    # server), so a cross-process lock is required to honour "no duplicate
    # simultaneous runs of the same logical task" across every entry point.
    cross_acquired, owner_pid = _acquire_cross_process_lock()
    if not cross_acquired:
        logger.warning(
            "Screener enrichment is already running in another process "
            f"(pid={owner_pid}) — refusing a duplicate run."
        )
        _RUN_LOCK.release()
        return {
            "total": 0,
            "updated": 0,
            "failed": 0,
            "skipped": 0,
            "partial": False,
            "cancelled": False,
            "elapsed_s": 0.0,
            "reason": "already_running_other_process",
        }
    _RUN_ACTIVE.set()

    try:
        return _run_locked(
            force=force,
            max_workers=max_workers,
            limit=limit,
            progress_cb=progress_cb,
            t_start=t_start,
        )
    finally:
        _RUN_ACTIVE.clear()
        if cross_acquired:
            _release_cross_process_lock()
        _RUN_LOCK.release()


LOCK_NAME = "screener_enrich"


def _acquire_cross_process_lock() -> tuple[bool, Optional[int]]:
    """Advisory cross-process lock for the backfill. Never raises."""
    try:
        from myra_app.utils import task_lock

        return task_lock.acquire(LOCK_NAME)
    except Exception as exc:  # noqa: BLE001 - advisory guard
        logger.debug(f"cross-process lock unavailable: {exc}")
        return True, None


def _release_cross_process_lock() -> None:
    try:
        from myra_app.utils import task_lock

        task_lock.release(LOCK_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"cross-process lock release skipped: {exc}")


def _run_locked(
    *,
    force: bool,
    max_workers: int,
    limit: Optional[int],
    progress_cb,
    t_start: float,
) -> dict:
    """Worker body. Callers must already hold ``_RUN_LOCK``."""
    db_path = os.path.join(DB_DIR, "myra_valuation.db")
    conn = sqlite3.connect(db_path)
    try:
        _ensure_table(conn)
        existing = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT symbol, last_updated FROM screener_fundamentals"
            ).fetchall()
        }
    finally:
        conn.close()

    symbols = _select_symbols()
    logger.info(f"Found {len(symbols)} symbols to enrich.")

    cutoff = (datetime.now() - timedelta(days=CACHE_DAYS)).isoformat()
    todo: List[str] = []
    skipped = 0
    for sym in symbols:
        if not force and sym in existing and existing[sym] >= cutoff:
            skipped += 1
            continue
        todo.append(sym)  # noqa: PG-APPEND - plain list build, not a pandas concat
    if limit is not None:
        cap = max(0, int(limit))
        # Only count what the cap actually excluded; a cap larger than the work
        # available must not drive the counter negative.
        skipped += max(0, len(todo) - cap)
        todo = todo[:cap]

    total = len(todo)
    updated = failed = done = 0
    pacer = _RequestPacer(REQUEST_INTERVAL_SECONDS)
    workers = max(1, min(int(max_workers), 16))

    logger.info(
        f"[screener_enrich] {total} symbols to fetch "
        f"({skipped} skipped by cache/limit), {workers} workers, "
        f"pacing {REQUEST_INTERVAL_SECONDS}s between requests"
    )

    def _work(symbol: str):
        # The thread's sqlite connection is opened, used and closed per symbol so
        # a worker's write lock is always released before the next symbol starts
        # (and before the thread ends). Leaving it open to the garbage collector
        # would keep a write lock on the sidecar for an unbounded time.
        try:
            data = _fetch_symbol_data(symbol, pacer)
            if data:
                _write_symbol(db_path, symbol, data)
                return True
            return False
        finally:
            _close_thread_db()

    if total:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="screener-enrich"
        ) as pool:
            futures = {pool.submit(_work, sym): sym for sym in todo}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    ok = future.result()
                except Exception as exc:  # noqa: BLE001 - one symbol never aborts
                    logger.warning(f"enrich failed for {symbol}: {exc}")
                    ok = False
                if ok:
                    updated += 1
                else:
                    failed += 1
                done += 1
                if progress_cb is not None:
                    try:
                        progress_cb(done, total, symbol, ok)
                    except Exception:  # noqa: BLE001 - progress must not break
                        pass

    elapsed = time.perf_counter() - t_start
    partial = failed > 0 and updated > 0
    summary = {
        "total": total,
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "partial": partial,
        "cancelled": False,
        "elapsed_s": round(elapsed, 2),
    }
    logger.info(
        f"[screener_enrich] done: updated={updated} failed={failed} "
        f"skipped={skipped} in {elapsed:.1f}s"
    )
    _record_screener_status(summary)
    return summary


def _record_screener_status(summary: dict) -> None:
    """Write the run outcome to the existing sync_log status vocabulary.

    Uses the same task-status surface as every other background task, so Mission
    Control / data-health sees one consistent contract. A degraded run is
    recorded as ``partial`` with the counts in ``error_message`` rather than a
    bare success.
    """
    try:
        from myra_app.utils.task_utils import _mark_task_run

        if summary.get("partial"):
            status = "partial"
            message = (
                f"updated={summary.get('updated')} failed={summary.get('failed')} "
                f"skipped={summary.get('skipped')}"
            )
        elif summary.get("failed") and not summary.get("updated"):
            status = "failed"
            message = f"all {summary.get('total')} symbols failed"
        else:
            status = "success"
            message = (
                f"updated={summary.get('updated')} " f"skipped={summary.get('skipped')}"
            )
        _mark_task_run("screener_enrich", status=status, error_message=message[:500])
    except Exception as exc:  # noqa: BLE001 - status write must never fail the run
        logger.debug(f"could not record screener_enrich status: {exc}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    enrich_screener_fundamentals(force=True)
