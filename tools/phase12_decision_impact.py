"""Phase 12 — before/after decision impact for the fund-traction quality score.

Bounded and reproducible: a synthetic valuation database with a handful of
deliberately-constructed rows, scored through the *real* SQL expression. No live
market data is read and no live database is touched.

The cases below exist to isolate each defect, not to argue that the new numbers
are "better stocks". They show only that an unavailable input no longer becomes
a fabricated one.
"""

from __future__ import annotations

import sqlite3

# The expression as it was before Phase 12 (alias omitted: it is used inline).
OLD_SQL = """
ROUND(
    COALESCE(f.roe, 0) * 0.4
    + COALESCE(f.net_margin, 0) * 0.3
    + (100 - MIN(COALESCE(f.pe, 100), 100)) * 0.3, 2
)
"""

CASES = [
    # symbol, roe, net_margin, pe, what the row represents
    ("COMPLETE", 25.0, 0.15, 20.0, "all three inputs present"),
    ("NO_ROE", None, 0.15, 20.0, "ROE absent (85% of real rows)"),
    ("NO_PE", 25.0, 0.15, None, "P/E absent"),
    ("LOSS_MAKING", None, None, -341.7, "negative P/E - no positive earnings"),
    ("CORRUPT_NM", 25.0, -26570.0, 20.0, "mixed-scale net_margin"),
    ("ALL_MISSING", None, None, None, "nothing known"),
    ("REAL_ZERO_PE", 25.0, None, 0.0, "explicit valid zero P/E"),
    ("REAL_ZERO_ROE", 0.0, None, 20.0, "explicit valid zero ROE (breakeven)"),
]


def build_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE fundamentals (symbol TEXT, roe REAL, net_margin REAL, pe REAL)"
    )
    conn.executemany(
        "INSERT INTO fundamentals VALUES (?, ?, ?, ?)",
        [(s, r, n, p) for s, r, n, p, _ in CASES],
    )
    conn.commit()
    return conn


def _fmt(value):
    return "unavail" if value is None else f"{value:.2f}"


def main() -> int:
    import os
    import tempfile

    from myra_web.routes.fund_traction import quality_score_sql

    tmp = os.path.join(tempfile.mkdtemp(prefix="p12_"), "val.db")
    conn = build_db(tmp)
    new_sql = quality_score_sql("f")

    print(f"{'symbol':14} {'OLD':>10} {'NEW':>10}  description")
    print("-" * 78)
    changed = 0
    for sym, *_rest in CASES:
        desc = _rest[-1]
        row = conn.execute(
            f"SELECT ({OLD_SQL}), ({new_sql}) FROM fundamentals f WHERE f.symbol = ?",
            (sym,),
        ).fetchone()
        old, new = row[0], row[1]
        mark = "  <-- changed" if old != new else ""
        if old != new:
            changed += 1
        print(f"{sym:14} {_fmt(old):>10} {_fmt(new):>10}  {desc}{mark}")

    conn.close()
    print(f"\n{changed} of {len(CASES)} synthetic cases change.")
    print("\nKey reading:")
    print("  LOSS_MAKING  : was the HIGHEST-scoring row (negative P/E had no floor)")
    print("  NO_ROE       : was penalised purely for being unmeasured")
    print("  CORRUPT_NM   : was swamped by a mixed-scale net_margin")
    print("  ALL_MISSING  : now 'unavail' (NULL) instead of a fabricated number")
    print("  REAL_ZERO_*  : valid zeros still flow through, not nulled out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
