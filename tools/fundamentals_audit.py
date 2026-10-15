"""Read-only audit of fundamental-data provenance and freshness (Phase 11 P2).

This tool answers "what does MYRA actually know about a stock?" rather than
"what columns exist?". For every field it reports, with an explicit denominator:

  * how many rows hold a usable value (non-NULL, and non-zero where zero is not
    a meaningful answer);
  * how many are missing, zero, or impossible (negative ROE is real; a
    negative market cap is not);
  * the newest ``last_updated`` / reporting period actually observed;
  * the known producer, so a coverage number can be attributed rather than
    merely counted.

Design rules this tool obeys:
  * **strictly read-only** - every connection opens ``mode=ro``; it cannot
    mutate a database even if a bug tries to;
  * **bounded** - it is a one-off diagnostic, never called from a UI refresh;
  * **honest about denominators** - "82% of rows" and "82% of the tracked
    universe" are different claims and are reported separately.

Usage::

    python tools/fundamentals_audit.py            # coverage + freshness
    python tools/fundamentals_audit.py --json     # machine-readable
    python tools/fundamentals_audit.py --field roe
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app import constants  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402

# ---------------------------------------------------------------------------
# Field definitions: provenance is asserted here, not inferred from the schema.
#
# `producer` is the module that writes the field (verified by grep in the audit
# report). `units` matters because a percentage and a fraction are the same
# column type and very different numbers.
# ---------------------------------------------------------------------------

FIELDS: dict[str, dict] = {
    "roe": {
        "label": "Return on equity",
        "producer": "fundamental_sync / Morningstar bulk + Screener.in",
        "units": "percent (0-100)",
        "meaningful_zero": True,
        "negative_ok": True,
    },
    "market_cap": {
        "label": "Market capitalisation",
        "producer": "fundamental_sync._compute_market_cap_from_prices (derived)",
        "units": "INR",
        "meaningful_zero": False,
        "negative_ok": False,
    },
    "profit_growth": {
        "label": "Profit growth",
        "producer": "NONE FOUND",
        "units": "unknown",
        "meaningful_zero": False,
        "negative_ok": True,
    },
    "net_margin": {
        "label": "Net margin",
        "producer": "screener_fundamentals (Screener.in)",
        "units": "fraction (0-1) or percent - VERIFY",
        "meaningful_zero": True,
        "negative_ok": True,
    },
    "inst_holding": {
        "label": "Institutional holding %",
        "producer": "institutional DB, month-end shareholding",
        "units": "percent",
        "meaningful_zero": True,
        "negative_ok": False,
    },
    "sales_growth": {
        "label": "Sales growth",
        "producer": "NONE FOUND",
        "units": "unknown",
        "meaningful_zero": False,
        "negative_ok": True,
    },
    "debt_to_equity": {
        "label": "Debt to equity",
        "producer": "fundamental_sync (bulk provider)",
        "units": "ratio",
        "meaningful_zero": True,
        "negative_ok": False,
    },
    "dividend_yield": {
        "label": "Dividend yield",
        "producer": "fundamental_sync (bulk provider)",
        "units": "unknown",
        "meaningful_zero": True,
        "negative_ok": True,
    },
}


def _db(key: str) -> str:
    return os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP[key])


def _ro(key: str) -> sqlite3.Connection | None:
    path = _db(key)
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except Exception:
        return 0


def universe_size() -> dict:
    """Two different denominators, deliberately not conflated."""
    out = {}
    conn = _ro("technical")
    if conn:
        out["technical_symbols"] = _scalar(
            conn, "SELECT COUNT(DISTINCT symbol) FROM technical_data"
        )
        conn.close()
    conn = _ro("valuation")
    if conn:
        out["fundamental_rows"] = _scalar(conn, "SELECT COUNT(*) FROM fundamentals")
        out["fundamental_symbols"] = _scalar(
            conn, "SELECT COUNT(DISTINCT symbol) FROM fundamentals"
        )
        out["screener_rows"] = _scalar(
            conn, "SELECT COUNT(*) FROM screener_fundamentals"
        )
        conn.close()
    return out


def field_coverage(field: str, meta: dict, universe: dict) -> dict:
    """Coverage for one field, with every denominator spelled out."""
    conn = _ro("valuation")
    if conn is None:
        return {"field": field, "error": "valuation database not found"}
    total_rows = _scalar(conn, "SELECT COUNT(*) FROM fundamentals")
    non_null = _scalar(
        conn, f"SELECT COUNT(*) FROM fundamentals WHERE {field} IS NOT NULL"
    )
    zeros = (
        _scalar(conn, f"SELECT COUNT(*) FROM fundamentals WHERE {field} = 0")
        if not meta["meaningful_zero"]
        else 0
    )
    negatives = _scalar(conn, f"SELECT COUNT(*) FROM fundamentals WHERE {field} < 0")
    try:
        stats = conn.execute(
            f"SELECT MIN({field}), MAX({field}), AVG({field}) FROM fundamentals"
            f" WHERE {field} IS NOT NULL"
        ).fetchone()
        lo, hi, avg = stats[0], stats[1], stats[2]
    except Exception:
        lo = hi = avg = None

    latest = conn.execute(
        f"SELECT MAX(last_updated) FROM fundamentals WHERE {field} IS NOT NULL"
    ).fetchone()[0]
    reporting = conn.execute(
        f"SELECT MAX(date) FROM fundamentals WHERE {field} IS NOT NULL"
    ).fetchone()[0]

    conn.close()

    usable = non_null - (zeros if not meta["meaningful_zero"] else 0)
    tracked = universe.get("technical_symbols") or 0
    return {
        "field": field,
        "label": meta["label"],
        "producer": meta["producer"],
        "units": meta["units"],
        "rows_total": total_rows,
        "non_null": non_null,
        "zero_rows": zeros,
        "negative_rows": negatives,
        "usable": usable,
        "pct_of_fundamental_rows": (
            round(100.0 * usable / total_rows, 2) if total_rows else 0.0
        ),
        "pct_of_tracked_universe": (
            round(100.0 * usable / tracked, 2) if tracked else 0.0
        ),
        "tracked_universe": tracked,
        "min": lo,
        "max": hi,
        "mean": round(avg, 4) if avg is not None else None,
        "latest_last_updated": latest,
        "latest_reporting_period": reporting,
    }


def freshness() -> dict:
    """Retrieval time vs reporting period - kept explicitly separate."""
    conn = _ro("valuation")
    if conn is None:
        return {}
    out = {}
    for label, sql in (
        ("fundamentals_last_updated", "SELECT MAX(last_updated) FROM fundamentals"),
        (
            "fundamentals_last_fundamental_update",
            "SELECT MAX(last_fundamental_update) FROM fundamentals",
        ),
        ("fundamentals_reporting_date", "SELECT MAX(date) FROM fundamentals"),
        (
            "screener_last_updated",
            "SELECT MAX(last_updated) FROM screener_fundamentals",
        ),
    ):
        try:
            out[label] = conn.execute(sql).fetchone()[0]
        except Exception:
            out[label] = None
    try:
        out["source_freshness_rows"] = _scalar(
            conn, "SELECT COUNT(*) FROM fundamental_source_freshness"
        )
        out["source_freshness_sources"] = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT source FROM fundamental_source_freshness"
            )
        ]
    except Exception:
        pass
    conn.close()
    return out


def per_source_freshness() -> list[dict]:
    conn = _ro("valuation")
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT source, COUNT(*) AS symbols, MAX(last_success_at) AS latest"
            " FROM fundamental_source_freshness GROUP BY source ORDER BY symbols DESC"
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--field", help="audit a single field")
    args = ap.parse_args()

    universe = universe_size()
    fields = [args.field] if args.field else list(FIELDS)

    report = {
        "generated_at": datetime.now().isoformat(),
        "db_dir": constants.DB_DIR,
        "universe": universe,
        "freshness": freshness(),
        "per_source_freshness": per_source_freshness(),
        "fields": [
            field_coverage(f, FIELDS[f], universe) for f in fields if f in FIELDS
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    u = report["universe"]
    print("=" * 78)
    print("MYRA fundamental-data audit (READ-ONLY)")
    print(f"generated : {report['generated_at']}")
    print(f"db dir    : {report['db_dir']}")
    print("=" * 78)
    print("\nDENOMINATORS")
    print(
        f"  symbols with technical data (tracked universe) : {u.get('technical_symbols')}"
    )
    print(
        f"  rows in fundamentals                          : {u.get('fundamental_rows')}"
    )
    print(
        f"  distinct symbols in fundamentals              : {u.get('fundamental_symbols')}"
    )
    print(f"  rows in screener_fundamentals (ROE/ROCE)      : {u.get('screener_rows')}")

    print("\nFRESHNESS  (retrieval time is NOT the reporting period)")
    for k, v in report["freshness"].items():
        print(f"  {k:42} {v}")

    print("\nCOVERAGE BY FIELD")
    hdr = f"{'field':16} {'usable':>7} {'/rows':>7} {'/univ':>7} {'null':>7} {'zero':>6} {'neg':>6}  {'latest last_updated':<20}"
    print(hdr)
    print("-" * len(hdr))
    for f in report["fields"]:
        if "error" in f:
            print(f"{f['field']:16} ERROR {f['error']}")
            continue
        print(
            f"{f['field']:16} {f['usable']:>7} {f['pct_of_fundamental_rows']:>6}% "
            f"{f['pct_of_tracked_universe']:>6}% {f['non_null'] - f['usable']:>7} "
            f"{f['zero_rows']:>6} {f['negative_rows']:>6}  {str(f['latest_last_updated'] or '-'):<20}"
        )

    print("\nPROVENANCE")
    for f in report["fields"]:
        if "error" in f:
            continue
        print(f"  {f['field']:16} {f['producer']}")
        print(f"  {'':16} units={f['units']}")

    if report["per_source_freshness"]:
        print("\nPER-SOURCE RETRIEVAL FRESHNESS")
        for s in report["per_source_freshness"]:
            print(f"  {s['source']:26} symbols={s['symbols']:>6}  latest={s['latest']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
