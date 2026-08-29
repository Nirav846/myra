"""
MYRA Point-in-Time Fundamentals History Backfill
================================================

Populates the ``fundamentals_history`` table in ``myra_valuation.db`` with
historical (point-in-time) market-cap snapshots fetched from yfinance. The
table feeds leak-free ``WyckoffAutomaton`` mcap resolution (as-of lookup per
event date): the current ``fundamentals`` table only holds one recent snapshot
(e.g. 2026-05-14 -> 2026-08-24), which cannot answer "what was the mcap on
2025-03-05?" without look-ahead.

Table
-----
``fundamentals_history(symbol TEXT NOT NULL, date TEXT NOT NULL,
market_cap REAL, free_float_mcap REAL, free_float_pct REAL, source TEXT,
PRIMARY KEY (symbol, date))``

The composite primary key also serves as the ``(symbol, date)`` index used by
the as-of lookup — no extra index is required. Table creation is idempotent
(runtime-only; ``.db`` files themselves are never committed).

Derivation per symbol (mirrors tools/sync_market_cap.py)
--------------------------------------------------------
* ``yf.Ticker("<SYMBOL>.NS")`` with a ``.BO`` fallback — the fallback only
  fires when the NSE history comes back empty, so prices are never mixed
  across exchanges for one symbol.
* ``ticker.history(start, end, auto_adjust=False)`` for daily closes plus
  ``ticker.info`` for ``sharesOutstanding`` / ``floatShares``.
* When a share count is available: daily ``market_cap = Close * shares``.
  Known limitation: raw (unadjusted) closes x *today's* share count distort
  windows containing a split; the scalar ``marketCap`` fallback path is
  unaffected.
* When no share count exists: the info-level scalar ``marketCap`` is mapped
  onto the latest date in the window (a single snapshot row).
* ``free_float_pct`` = current ``floatShares`` / ``sharesOutstanding`` ratio
  (approximation applied uniformly across the window); ``free_float_mcap`` =
  ``market_cap * free_float_pct / 100``.

Storage cadence
---------------
By default one row per calendar month — the LAST trading day of each month
(deterministic: max date within each ``YYYY-MM`` bucket). This keeps the table
~15x smaller than daily while preserving leak-free as-of resolution (the as-of
resolver returns the most recent monthly snapshot <= the event date). Pass
``--daily`` to store every session instead.

Idempotency
-----------
Inserts use ``INSERT OR REPLACE`` keyed on ``(symbol, date)``; re-running over
the same window never duplicates rows.

Usage
-----
    python -m myra_app.backfill_fundamentals --start 2025-01-01 --end 2026-04-01
    python -m myra_app.backfill_fundamentals --start 2025-01-01 --end 2026-04-01 --limit 3
    python -m myra_app.backfill_fundamentals --start 2025-01-01 --end 2026-04-01 --symbols RELIANCE,TCS --dry-run
    python -m myra_app.backfill_fundamentals --start 2025-01-01 --end 2026-04-01 --daily

``--start`` / ``--end`` are both required and inclusive (yfinance's ``end`` is
exclusive, so the script adds one day internally). Full-universe runs
(3300+ symbols at a ~0.5 s rate limit per fetch) take hours — run them as a
background job; a short window with ``--limit 3`` is the recommended smoke
test. ``--dry-run`` fetches and prints planned rows without writing anything.
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from math import isfinite

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

RATE_LIMIT_SECONDS = 0.5  # polite yfinance pacing between symbols
PROGRESS_EVERY = 25
TABLE_NAME = "fundamentals_history"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    market_cap REAL,
    free_float_mcap REAL,
    free_float_pct REAL,
    source TEXT,
    PRIMARY KEY (symbol, date)
)
"""

INSERT_SQL = (
    "INSERT OR REPLACE INTO fundamentals_history "
    "(symbol, date, market_cap, free_float_mcap, free_float_pct, source) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def create_fundamentals_history_table(conn: sqlite3.Connection) -> None:
    """Create the ``fundamentals_history`` table (idempotent, runtime-only).

    SchemaRegistry.validate_schema only ALTERs existing tables, so creation
    must happen here (CREATE TABLE IF NOT EXISTS) — the registry entry is
    documentation/validation parity only.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()


def get_universe_symbols(
    conn: sqlite3.Connection,
    symbols: list[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Backfill universe.

    ``symbols`` (from ``--symbols``) overrides the default universe, which is
    the DISTINCT symbols in ``fundamentals`` (mirrors tools/sync_market_cap.py).
    ``limit`` slices the resulting list (for smoke tests).
    """
    if symbols:
        chosen = [s.strip().upper() for s in symbols if s.strip()]
    else:
        chosen = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM fundamentals ORDER BY symbol"
            ).fetchall()
        ]
    if limit:
        chosen = chosen[:limit]
    return chosen


def _free_float_mcap(market_cap: float, ff_pct: float | None) -> float | None:
    """Free-float market cap = market_cap * free_float_pct / 100."""
    if ff_pct is None:
        return None
    return round(market_cap * ff_pct / 100.0, 2)


def _derive_rows(hist, info: dict, symbol: str) -> list[tuple]:
    """Derive point-in-time mcap rows from one yfinance history frame + info.

    Returns 5-tuples ``(date_str, market_cap, free_float_mcap, free_float_pct,
    source)``. ``source`` records whether a row came from daily Close*shares
    math (``yfinance_daily``) or the info-level scalar snapshot mapped onto the
    latest window date (``yfinance_info_snapshot``).
    """
    so = info.get("sharesOutstanding")
    fs = info.get("floatShares")
    scalar_mcap = info.get("marketCap")

    ff_pct = None
    if so and fs:
        ff_pct = round(float(fs) / float(so) * 100.0, 2)

    if so:
        rows = [
            (
                str(ts.date()),
                round(float(close) * float(so), 2),
                _free_float_mcap(float(close) * float(so), ff_pct),
                ff_pct,
                "yfinance_daily",
            )
            for ts, close in zip(hist.index, hist["Close"])
            if isfinite(float(close)) and float(close) > 0
        ]
        if rows:
            return rows
        logger.warning("No positive closes for %s — no rows derived", symbol)
        return []

    if scalar_mcap:
        last_date = str(hist.index[-1].date())
        mc = float(scalar_mcap)
        return [
            (
                last_date,
                round(mc, 2),
                _free_float_mcap(mc, ff_pct),
                ff_pct,
                "yfinance_info_snapshot",
            )
        ]
    return []


def fetch_daily_mcap_history(symbol: str, start: date, end: date) -> list[tuple]:
    """Fetch point-in-time market-cap rows for one symbol via yfinance.

    ``<SYMBOL>.NS`` first; ``.BO`` only when the NSE history is empty (never
    mixes exchange prices for one symbol). Returns ``[]`` when nothing could be
    derived — callers count that as a failure.
    """
    import yfinance as yf

    problems = []
    for suffix in (".NS", ".BO"):
        try:
            ticker = yf.Ticker(f"{symbol}{suffix}")
            hist = ticker.history(start=start, end=end, auto_adjust=False)
            if hist is None or hist.empty:
                # Small fixed-size accumulator (max 2 suffixes) for the
                # combined-failure warning — never hot-path.
                problems.append(f"{suffix}: empty history")  # noqa: PG-APPEND
                continue
            try:
                info = ticker.info or {}
            except Exception:
                info = {}
            return _derive_rows(hist, info, symbol)
        except Exception as exc:
            # Same tiny accumulator as above — 2-element cap.
            problems.append(f"{suffix}: {exc}")  # noqa: PG-APPEND
    if problems:
        logger.warning("yfinance fetch failed for %s — %s", symbol, "; ".join(problems))
    return []


def monthly_snapshot(rows: list[tuple]) -> list[tuple]:
    """Collapse daily rows to one row per calendar month — the LAST trading
    day of each month (deterministic: max ISO date within each ``YYYY-MM``
    bucket). The as-of resolver then returns the most recent monthly snapshot
    <= the event date."""
    newest: dict[str, tuple] = {}
    for row in rows:
        month = row[0][:7]
        cur = newest.get(month)
        if cur is None or row[0] > cur[0]:
            newest[month] = row
    return [newest[key] for key in sorted(newest)]


def insert_history_rows(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """INSERT OR REPLACE into ``fundamentals_history`` (bulk, single commit).

    Re-running the same ``(symbol, date)`` rows never duplicates them.
    """
    conn.executemany(INSERT_SQL, rows)
    conn.commit()
    return len(rows)


def backfill_fundamentals_history(
    start: date,
    end: date,
    limit: int | None = None,
    symbols: list[str] | None = None,
    dry_run: bool = False,
    daily: bool = False,
    db_path: str | None = None,
) -> dict:
    """Run the fundamentals-history backfill over the inclusive [start, end].

    Returns a summary dict: symbols_attempted / symbols_ok / symbols_failed /
    rows_written / mode ('daily' | 'monthly') / elapsed_seconds. When
    ``dry_run`` is True nothing is written to the database.
    """
    db_path = db_path or os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Valuation database not found at {db_path}")

    mode = "daily" if daily else "monthly"
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        if not dry_run:
            create_fundamentals_history_table(conn)
        universe = get_universe_symbols(conn, symbols=symbols, limit=limit)
        if not universe:
            logger.warning("No symbols to process.")
            return {
                "symbols_attempted": 0,
                "symbols_ok": 0,
                "symbols_failed": 0,
                "rows_written": 0,
                "mode": mode,
                "elapsed_seconds": 0.0,
            }

        # yfinance's `end` is exclusive — add a day so the requested end date
        # is covered.
        end_inclusive = end + timedelta(days=1)
        logger.info(
            "Backfilling %d symbols from %s to %s (%s snapshots)",
            len(universe),
            start.isoformat(),
            end.isoformat(),
            mode,
        )

        ok = 0
        failed = 0
        rows_written = 0
        t0 = time.time()
        for i, sym in enumerate(universe, 1):
            try:
                daily_rows = fetch_daily_mcap_history(sym, start, end_inclusive)
                if not daily_rows:
                    failed += 1
                    logger.warning("No data for %s — counted as failure", sym)
                    continue
                final_rows = daily_rows if daily else monthly_snapshot(daily_rows)
                if dry_run:
                    logger.info(
                        "[dry-run] %s: %d row(s) from %d sessions",
                        sym,
                        len(final_rows),
                        len(daily_rows),
                    )
                else:
                    rows_written += insert_history_rows(
                        conn, [(sym, *row) for row in final_rows]
                    )
                ok += 1
            except Exception as exc:
                failed += 1
                logger.warning("Failed for %s: %s", sym, exc, exc_info=True)
            time.sleep(RATE_LIMIT_SECONDS)
            if i % PROGRESS_EVERY == 0 or i == len(universe):
                logger.info(
                    "Progress %d / %d (ok=%d failed=%d rows=%d, %.0fs)",
                    i,
                    len(universe),
                    ok,
                    failed,
                    rows_written,
                    time.time() - t0,
                )
    finally:
        conn.close()

    summary = {
        "symbols_attempted": len(universe),
        "symbols_ok": ok,
        "symbols_failed": failed,
        "rows_written": rows_written,
        "mode": mode,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    logger.info(
        "Done. %d/%d symbols ok, %d failed, %d rows %s (%.1fs)",
        ok,
        len(universe),
        failed,
        rows_written,
        "staged (dry-run)" if dry_run else "written",
        summary["elapsed_seconds"],
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    parser = argparse.ArgumentParser(
        prog="python -m myra_app.backfill_fundamentals",
        description=(
            "Backfill point-in-time market-cap history into fundamentals_history"
            " (yfinance) for leak-free Wyckoff mcap resolution."
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date YYYY-MM-DD (inclusive)",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End date YYYY-MM-DD (inclusive)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N universe symbols (smoke tests)",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbol list (overrides the fundamentals universe)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print planned rows without writing anything",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Store every session instead of monthly snapshots",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Override the valuation DB path (default: myra_app/db/myra_valuation.db)",
    )
    args = parser.parse_args(argv)

    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        parser.error("--start/--end must be YYYY-MM-DD")
    if start > end:
        parser.error("--start must not be after --end")

    symbol_list = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    try:
        summary = backfill_fundamentals_history(
            start=start,
            end=end,
            limit=args.limit,
            symbols=symbol_list,
            dry_run=args.dry_run,
            daily=args.daily,
            db_path=args.db,
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))

    print(f"\n{'=' * 60}")
    print("  FUNDAMENTALS HISTORY BACKFILL SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Symbols attempted: {summary['symbols_attempted']}")
    print(f"  Symbols ok:        {summary['symbols_ok']}")
    print(f"  Symbols failed:    {summary['symbols_failed']}")
    print(f"  Rows ({summary['mode']}):       {summary['rows_written']}")
    print(f"  Elapsed:           {summary['elapsed_seconds']}s")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
