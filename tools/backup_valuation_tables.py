"""Export valuation tables to a dated, checksummed backup. READ-ONLY on the DB.

Why this exists
---------------
`fund_traction` holds 2026-04..2026-07 and its upstream is gone: the configured
`data/` path now 404s, and the sync swallows that 404 and reports success. The
local table may be the only surviving copy of those months anywhere, so it gets
backed up before anything else touches the system.

Design notes
------------
* The source database is opened with `mode=ro`; this tool never writes to it.
* Every CSV is re-read after writing and its row count compared against the DB,
  so a truncated or mis-encoded export fails loudly instead of looking fine.
* The manifest records a sha256 per file, so a later run can prove a backup was
  not altered in transit.
* Payloads are gitignored; the manifest is tracked as the audit record.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402

TABLES = ("fund_traction", "fund_cross_buy")
EXPECTED_ROWS = {"fund_traction": 2470, "fund_cross_buy": 1852}
BACKUP_ROOT = Path(__file__).resolve().parents[1] / "backups"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def open_read_only(db: Path) -> sqlite3.Connection:
    # mode=ro: guarantees no write path exists on this connection.
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def table_meta(con: sqlite3.Connection, table: str) -> dict:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    month = "month" if "month" in cols else None
    lo = hi = None
    if month:
        lo, hi = con.execute(
            f"SELECT MIN({month}), MAX({month}) FROM {table}"
        ).fetchone()
    return {
        "columns": cols,
        "row_count": n,
        "month_column": month,
        "min_month": lo,
        "max_month": hi,
    }


def export_table(con: sqlite3.Connection, table: str, out_dir: Path) -> dict:
    meta = table_meta(con, table)
    cols = meta["columns"]
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    csv_path = out_dir / f"{table}.csv"
    # newline="" + explicit lineterminator keeps the file byte-stable and
    # readable by any CSV parser without pandas' quoting surprises.
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        w.writerows(rows)

    # Re-read from disk; never trust the in-memory count.
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        reloaded = list(csv.reader(fh))
    header, body = reloaded[0], reloaded[1:]
    problems = []
    if header != cols:
        problems.append(f"header mismatch: {header} != {cols}")
    if len(body) != meta["row_count"]:
        problems.append(
            f"row count mismatch on disk: {len(body)} != " f"{meta['row_count']}"
        )
    exp = EXPECTED_ROWS.get(table)
    if exp is not None and meta["row_count"] != exp:
        problems.append(f"row count {meta['row_count']} != expected {exp}")
    if problems:
        raise SystemExit(f"FAILED to verify backup of {table}: " + "; ".join(problems))

    return {
        "table": table,
        "csv_file": csv_path.name,
        "db_row_count": meta["row_count"],
        "csv_row_count": len(body),
        "rows_verified_match": True,
        "expected_row_count": exp,
        "columns": cols,
        "min_month": meta["min_month"],
        "max_month": meta["max_month"],
        "bytes": csv_path.stat().st_size,
        "sha256": sha256_of(csv_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--tag", default=None, help="backup folder name (default: today's date)"
    )
    args = ap.parse_args(argv)

    tag = args.tag or date.today().isoformat()
    out_dir = BACKUP_ROOT / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    db = Path(DB_DIR) / LibrarianCore.DB_MAP["valuation"]
    print(f"source database : {db}")
    print(f"source mtime    : {datetime.fromtimestamp(db.stat().st_mtime).isoformat()}")
    print(f"backup directory: {out_dir}")
    print("opening READ-ONLY (mode=ro) -- no write path exists\n")

    con = open_read_only(db)
    try:
        present = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [t for t in TABLES if t not in present]
        if missing:
            raise SystemExit(f"table(s) not present: {missing}")
        entries = [export_table(con, t, out_dir) for t in TABLES]
    finally:
        con.close()

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": (
            "Protect fund_traction / fund_cross_buy: the upstream "
            "monthly JSONs are gone (404) and the sync reports "
            "success, so these local rows may be irreplaceable."
        ),
        "source_db": str(db),
        "source_db_mtime": datetime.fromtimestamp(db.stat().st_mtime).isoformat(
            timespec="seconds"
        ),
        "source_opened": "read-only (sqlite mode=ro)",
        "backup_dir": str(out_dir.relative_to(BACKUP_ROOT.parents[0])),
        "tables": entries,
        "all_row_counts_verified": all(e["rows_verified_match"] for e in entries),
    }
    mpath = out_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("BACKUP VERIFIED")
    for e in entries:
        print(
            f"  {e['table']:<16} db={e['db_row_count']:>5} "
            f"csv={e['csv_row_count']:>5} match=True  "
            f"months {e['min_month']}..{e['max_month']}"
        )
        print(
            f"                   {e['csv_file']}  {e['bytes']:>8} B  "
            f"sha256={e['sha256'][:16]}..."
        )
    print(f"  manifest -> {mpath}")
    print(
        f"\nsource db mtime after export: "
        f"{datetime.fromtimestamp(db.stat().st_mtime).isoformat()} "
        f"(unchanged: read-only access)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
