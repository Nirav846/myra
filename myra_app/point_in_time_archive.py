"""Point-in-time archive: append-only snapshots + lossless raw capture.

Why this exists
---------------
`valuation.fundamentals` is a **rolling snapshot** — it is overwritten in
place, so today's values overwrite yesterday's. That makes point-in-time
fundamentals backtests impossible after the fact, and it is why the
pre-2026-04 fund-traction history could not be reconstructed (Prompt 2
probe: the upstream filenames carry no year).

This module starts recording that history going forward. It is deliberately
*additive and append-only*:

* It writes to its own database (``myra_archive.db``), never to
  ``myra_valuation.db``. The valuation DB is owned by the upstox fetcher
  and fundamental writers are disabled, so the archive keeps its hands off
  it entirely (read-only, ``mode=ro``).
* It never modifies or drops an existing table, and never rewrites a
  snapshot that has already been recorded for a given date.
* A row is *never* replaced with a nullier version of itself: insertion is
  ``INSERT OR IGNORE``, so re-running is a no-op and cannot clobber.

Two things are captured:

``fundamentals_snapshots``
    One row per symbol per ``as_of_date``, holding the full source row as
    JSON. The live ``fundamentals`` table has ~64 columns; storing the row
    verbatim means the archive can never drift from the source schema and
    needs no column mapping to stay correct.

``raw_traction_archive``
    The raw fund-traction JSON exactly as served, written to a gitignored
    directory. No schema mapping is applied and no fields are dropped, so
    the file stays a lossless record even though the live app schema does
    not match what is in our local table today. Skipped when the SHA-256 is
    unchanged from the previous capture.

Usage::

    python -m myra_app.point_in_time_archive            # both
    python -m myra_app.point_in_time_archive --only fundamentals
    python -m myra_app.point_in_time_archive --only raw
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timezone

import requests

from myra_app.constants import DB_DIR, PROJECT_ROOT, TRACTION_BASE_URL
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

# Raw payloads are a lossless record, not source code. Gitignore keeps the
# directory out of history; the manifest rows in the DB stay auditable.
ARCHIVE_RAW_DIR = os.path.join(PROJECT_ROOT, "archive", "raw")

_CREATE_FUNDAMENTALS_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS fundamentals_snapshots (
    symbol      TEXT NOT NULL,
    as_of_date  TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source      TEXT,
    captured_at TEXT NOT NULL,
    row_sha256  TEXT NOT NULL,
    row_json    TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of_date)
)
"""

_CREATE_RAW_TRACTION_ARCHIVE = """
CREATE TABLE IF NOT EXISTS raw_traction_archive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at   TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    source_month TEXT,
    sha256       TEXT NOT NULL,
    byte_size    INTEGER,
    symbol_count INTEGER,
    raw_path     TEXT NOT NULL,
    recorded_at  TEXT NOT NULL
)
"""


def _archive_db_path() -> str:
    """Resolve the archive DB through DB_DIR + LibrarianCore.DB_MAP."""
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["archive"])


def _valuation_db_path() -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])


def _ro(path: str) -> sqlite3.Connection:
    """Open a SQLite file strictly read-only.

    mode=ro means a bug in this module cannot write to a source database
    even if the connection were reused; SQLite refuses writes to it.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_FUNDAMENTALS_SNAPSHOTS)
    conn.execute(_CREATE_RAW_TRACTION_ARCHIVE)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_fund_snap_date "
        "ON fundamentals_snapshots (as_of_date)"
    )
    conn.commit()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _nonnull(row) -> int:
    return sum(1 for v in tuple(row) if v is not None)


def _stored_path(path: str) -> str:
    """Path relative to the project root, for a portable audit record.

    Falls back to the absolute path when the archive lives on a different
    drive (e.g. a relocated or test data directory) and relpath would raise.
    """
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return path


# ── Fundamentals snapshots ──────────────────────────────────────────────────


def archive_fundamentals(as_of_date: str | None = None) -> dict:
    """Copy the current ``valuation.fundamentals`` rows into the archive.

    One snapshot row per symbol per ``as_of_date`` (default: today, IST-free
    UTC date string YYYY-MM-DD). Idempotent: a symbol already snapshotted for
    that date is left untouched, so re-running is safe and can never
    overwrite a good row with a nullier one.

    The valuation DB is opened read-only. Returns a result dict; never
    raises for per-row problems.
    """
    as_of_date = as_of_date or date.today().isoformat()
    result = {
        "success": False,
        "as_of_date": as_of_date,
        "source_rows": 0,
        "rows_archived": 0,
        "rows_unchanged": 0,
        "error": None,
    }

    src_path = _valuation_db_path()
    if not os.path.exists(src_path):
        result["error"] = f"valuation DB not found: {src_path}"
        logger.error("Archive fundamentals: %s", result["error"])
        return result

    os.makedirs(os.path.dirname(_archive_db_path()), exist_ok=True)
    src = _ro(src_path)
    dst = sqlite3.connect(_archive_db_path())
    dst.row_factory = sqlite3.Row
    try:
        _ensure_tables(dst)

        src.row_factory = sqlite3.Row
        cur = src.execute("SELECT * FROM fundamentals")
        rows = cur.fetchall()
        result["source_rows"] = len(rows)
        if not rows:
            result["error"] = "fundamentals table is empty"
            logger.warning("Archive fundamentals: source table empty")
            return result

        captured_at = datetime.now(timezone.utc).isoformat()
        # Existing snapshots for this date, so we can report unchanged rows.
        existing = {
            r["symbol"]: _nonnull(json.loads(r["row_json"]))
            for r in dst.execute(
                "SELECT symbol, row_json FROM fundamentals_snapshots "
                "WHERE as_of_date = ?",
                (as_of_date,),
            )
        }

        for row in rows:
            d = {k: row[k] for k in row.keys()}
            payload = _canonical(d)
            row_sha = hashlib.sha256(payload.encode()).hexdigest()
            symbol = d.get("symbol")
            if not symbol:
                continue
            snapshot_id = f"{as_of_date}-{row_sha[:12]}"

            prev = existing.get(symbol)
            if prev is not None:
                # Append-only: never rewrite. Also guard the null-clobber
                # invariant explicitly in case the row is later edited.
                if prev >= _nonnull(d):
                    result["rows_unchanged"] += 1
                    continue
                logger.debug(
                    "Archive fundamentals: %s already snapshotted with equal "
                    "or greater completeness; keeping it",
                    symbol,
                )
                result["rows_unchanged"] += 1
                continue

            dst.execute(
                "INSERT OR IGNORE INTO fundamentals_snapshots "
                "(symbol, as_of_date, snapshot_id, source, captured_at, "
                " row_sha256, row_json) VALUES (?,?,?,?,?,?,?)",
                (
                    symbol,
                    as_of_date,
                    snapshot_id,
                    d.get("source"),
                    captured_at,
                    row_sha,
                    payload,
                ),
            )
            result["rows_archived"] += 1

        dst.commit()
        result["success"] = True
        logger.info(
            "Archive fundamentals: %s -> %d archived, %d already present "
            "(of %d source rows)",
            as_of_date,
            result["rows_archived"],
            result["rows_unchanged"],
            result["source_rows"],
        )
        return result

    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        logger.exception("Archive fundamentals failed")
        return result
    finally:
        src.close()
        dst.close()


# ── Raw fund-traction capture ───────────────────────────────────────────────


def _count_symbols(payload) -> int | None:
    """Best-effort symbol count without assuming a schema mapping."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("stocks", "data", "results", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return len(val)
    return None


def archive_traction_raw(
    fetched_at: str | None = None,
    base_url: str = TRACTION_BASE_URL,
    months: list[str] | None = None,
) -> dict:
    """Save the raw fund-traction JSON losslessly and record its manifest row.

    The upstream filenames carry no year, so each response is only knowable
    as "whatever is on that URL today". We therefore store the bytes verbatim
    and dedupe on SHA-256: if the latest archived file for the URL has the
    same hash, nothing is written (the rolling snapshot did not change).

    ``months`` is a list of "YYYY-MM" strings; defaults to the months the
    traction sync considers current, so the archive and the live table
    advance together.
    """
    fetched_at = fetched_at or date.today().isoformat()
    result = {
        "success": False,
        "fetched_at": fetched_at,
        "files_archived": 0,
        "files_unchanged": 0,
        "error": None,
    }

    if months is None:
        from myra_app.fund_traction_sync import _expected_months

        months = _expected_months()

    day_dir = os.path.join(ARCHIVE_RAW_DIR, fetched_at)
    os.makedirs(day_dir, exist_ok=True)

    os.makedirs(os.path.dirname(_archive_db_path()), exist_ok=True)
    dst = sqlite3.connect(_archive_db_path())
    dst.row_factory = sqlite3.Row
    try:
        _ensure_tables(dst)
        errors = []

        # Resolve month name from the "YYYY-MM" string via the traction map.
        from myra_app.fund_traction_sync import _MONTH_NAMES

        for month in months:
            m = int(month.split("-")[1])
            name = _MONTH_NAMES[m - 1]
            url = f"{base_url}{name}_traction.json"
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                blob = resp.content
            except Exception as e:  # noqa: BLE001
                errors.append(f"{month}: {type(e).__name__}: {e}")
                logger.warning("Archive raw: could not fetch %s", url)
                continue

            sha = hashlib.sha256(blob).hexdigest()

            prev = dst.execute(
                "SELECT sha256 FROM raw_traction_archive WHERE source_url = ? "
                "ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            if prev is not None and prev["sha256"] == sha:
                result["files_unchanged"] += 1
                logger.info(
                    "Archive raw: %s unchanged (sha %s), skipping", month, sha[:12]
                )
                continue

            raw_path = os.path.join(day_dir, f"{name}_traction.json")
            # Write the served bytes verbatim; no re-serialisation.
            with open(raw_path, "wb") as fh:
                fh.write(blob)

            try:
                payload = json.loads(blob.decode("utf-8", errors="replace"))
                symbol_count = _count_symbols(payload)
            except Exception:  # noqa: BLE001
                symbol_count = None

            dst.execute(
                "INSERT INTO raw_traction_archive (fetched_at, source_url, "
                "source_month, sha256, byte_size, symbol_count, raw_path, "
                "recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    fetched_at,
                    url,
                    month,
                    sha,
                    len(blob),
                    symbol_count,
                    _stored_path(raw_path),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            result["files_archived"] += 1
            logger.info("Archive raw: saved %s (%d bytes)", month, len(blob))

        dst.commit()
        result["success"] = not errors
        if errors:
            result["error"] = "; ".join(errors)
            logger.error("Archive raw: some months failed: %s", result["error"])
        return result

    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        logger.exception("Archive raw failed")
        return result
    finally:
        dst.close()


# ── Combined entry point + CLI ──────────────────────────────────────────────


def run_archive(as_of_date: str | None = None) -> dict:
    """Run both capture steps; used by the weekly task and the CLI."""
    fund = archive_fundamentals(as_of_date=as_of_date)
    raw = archive_traction_raw(fetched_at=as_of_date)
    return {"fundamentals": fund, "raw": raw}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append-only point-in-time archive capture."
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Snapshot date YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--only",
        choices=["fundamentals", "raw", "all"],
        default="all",
        help="Which capture step to run.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.only == "fundamentals":
        res = archive_fundamentals(as_of_date=args.as_of)
        ok = res["success"]
    elif args.only == "raw":
        res = archive_traction_raw(fetched_at=args.as_of)
        ok = res["success"]
    else:
        res = run_archive(as_of_date=args.as_of)
        ok = res["fundamentals"]["success"] or res["raw"]["success"]

    print(json.dumps(res, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
