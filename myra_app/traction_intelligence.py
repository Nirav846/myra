"""Traction intelligence: month-over-month cohorts + delivery-weighted entry price.

Complements the Traction Board (which is a single-month view) with two
read-time, schema-free signals:

* **MoM fund cohorts** -- for each symbol, the per-fund breakdown
  (``fund_traction_funds``) of the target month is diffed against the prior
  available month to classify funds into new / exited / held-adding /
  held-trimming.  This is what turns "17 funds hold this" into "7 funds entered,
  2 left, 5 added, 3 trimmed".
* **Delivery-weighted entry price (DWAP)** -- ``sum(delivery * typical) /
  sum(delivery)`` over the month, where ``typical = (high + low + close) / 3``.
  Delivery is a real ownership transfer, not intraday churn, so this is a
  defensible proxy for the price at which institutions accumulated.  Compared to
  CMP it answers "are funds still below water / is the smart money in profit?".

Also reports CMP vs the month's high/low (where price sits in the month range).

Everything is read-only and best-effort: a missing technical row or an empty
delivery series yields ``None`` for that field, and the symbol is still listed.
Data gaps are surfaced, never hidden.
"""

from __future__ import annotations

import logging
import os
import sqlite3

from myra_app import fund_traction_sync as fts
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

#: Chunk size for IN (...) lookups against technical_data.
_CHUNK = 500


def _val_path() -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])


def _tech_path() -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])


def _chunks(items: list[str], size: int = _CHUNK):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _month_price_context(
    tech_conn: sqlite3.Connection, symbols: list[str], month: str
) -> dict[str, dict]:
    """Per-symbol month high/low, delivery-weighted price and latest close.

    One grouped query per symbol chunk (no per-symbol round-trips).
    """
    out: dict[str, dict] = {}
    if not symbols:
        return out
    for chunk in _chunks(symbols):
        ph = ",".join("?" for _ in chunk)
        try:
            rows = tech_conn.execute(
                f"""
                SELECT symbol,
                       MAX(high) AS mhigh,
                       MIN(low)  AS mlow,
                       SUM(delivery * ((high + low + close) / 3.0)) AS dwap_num,
                       SUM(delivery) AS dwap_den
                FROM technical_data
                WHERE substr(date, 1, 7) = ?
                  AND symbol IN ({ph})
                  AND high IS NOT NULL AND low IS NOT NULL
                GROUP BY symbol
                """,
                [month, *chunk],
            ).fetchall()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            logger.debug("month price context failed: %s", exc)
            rows = []
        for sym, mhigh, mlow, num, den in rows:
            dwap = (num / den) if (num is not None and den) else None
            out[str(sym)] = {
                "month_high": mhigh,
                "month_low": mlow,
                "dwap": round(dwap, 2) if dwap else None,
            }

    # Latest close (one windowed query per chunk) -> CMP.
    for chunk in _chunks(symbols):
        ph = ",".join("?" for _ in chunk)
        try:
            rows = tech_conn.execute(
                f"""
                SELECT symbol, close FROM (
                    SELECT symbol, close,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol ORDER BY date DESC
                           ) AS rn
                    FROM technical_data
                    WHERE symbol IN ({ph}) AND close IS NOT NULL
                ) WHERE rn = 1
                """,
                chunk,
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for sym, close in rows:
            out.setdefault(str(sym), {})["cmp"] = close
    return out


def _latest_month(conn: sqlite3.Connection, table: str) -> str | None:
    try:
        row = conn.execute(f"SELECT MAX(month) FROM {table}").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _prior_available(conn: sqlite3.Connection, table: str, month: str) -> str | None:
    try:
        months = [
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT month FROM {table} ORDER BY month DESC"
            )
        ]
    except sqlite3.Error:
        return None
    return fts._prior_month(months, month)


def _cohorts(
    conn: sqlite3.Connection,
    month: str,
    prior_month: str | None,
    key_map: dict[str, str] | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """Diff per-fund breakdown between ``month`` and ``prior_month``.

    ``key_map`` normalises each raw ``fund_traction_funds.symbol`` to its
    canonical ticker -- essential because the upstream artifact switched from
    tickers (``AARTIIND``) to Trendlyne long names (``AARTIINDUSTRIES``) between
    months, so raw keys are not comparable across months.

    Returns ``(by_symbol, fund_leaderboard)`` keyed by canonical symbol.
    """
    key_map = key_map or {}
    months = [month] + ([prior_month] if prior_month else [])
    ph = ",".join("?" for _ in months)
    try:
        rows = conn.execute(
            f"""SELECT month, symbol, fund_slug, fund_name, share_change_pct
                FROM fund_traction_funds WHERE month IN ({ph})""",
            months,
        ).fetchall()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.debug("cohort query failed: %s", exc)
        return {}, []

    cur: dict[str, dict[str, float | None]] = {}
    prev: dict[str, dict[str, float | None]] = {}
    names: dict[str, str] = {}
    for m, sym, slug, fname, chg in rows:
        key = slug or fname
        names[key] = fname or slug or key
        canon = key_map.get(sym, sym)
        (cur if m == month else prev).setdefault(canon, {})[key] = chg

    by_symbol: dict[str, dict] = {}
    fund_new: dict[str, int] = {}
    fund_exit: dict[str, int] = {}

    all_symbols = set(cur) | set(prev)
    for sym in all_symbols:
        c = cur.get(sym, {})
        p = prev.get(sym, {})
        new = [k for k in c if k not in p]
        exited = [k for k in p if k not in c]
        held = [k for k in c if k in p]
        added = [k for k in held if (c[k] or 0) > 0]
        reduced = [k for k in held if (c[k] or 0) < 0]
        for k in new:
            fund_new[k] = fund_new.get(k, 0) + 1
        for k in exited:
            fund_exit[k] = fund_exit.get(k, 0) + 1
        delta = len(c) - len(p)
        by_symbol[sym] = {
            "fund_count_prev": len(p),
            "fund_count_delta": delta,
            "new_funds": len(new),
            "exited_funds": len(exited),
            "held_funds": len(held),
            "expanding_funds": len(added),
            "trimming_funds": len(reduced),
            "new_fund_names": [names[k] for k in new][:8],
            "exited_fund_names": [names[k] for k in exited][:8],
            "cohort": (
                "adding_funds"
                if delta > 0
                else "losing_funds"
                if delta < 0
                else "unchanged"
            ),
        }

    leaderboard = []
    for k in set(fund_new) | set(fund_exit):
        leaderboard.append(
            {
                "fund": names.get(k, k),
                "symbols_added": fund_new.get(k, 0),
                "symbols_exited": fund_exit.get(k, 0),
                "net": fund_new.get(k, 0) - fund_exit.get(k, 0),
            }
        )
    leaderboard.sort(key=lambda r: (-r["net"], -r["symbols_added"]))
    return by_symbol, leaderboard[:15]


def get_traction_intelligence(
    month: str | None = None,
    *,
    val_conn: sqlite3.Connection | None = None,
    tech_conn: sqlite3.Connection | None = None,
) -> dict:
    """Month-over-month traction intelligence payload (read-only)."""
    result: dict = {
        "success": False,
        "month": None,
        "prior_month": None,
        "rows": [],
        "fund_leaderboard": [],
        "stats": {},
        "count": 0,
        "error": None,
    }
    own_val = val_conn is None
    own_tech = tech_conn is None
    if val_conn is None:
        val_conn = sqlite3.connect(_val_path())
    try:
        target = month or _latest_month(val_conn, "fund_traction")
        if not target:
            result["error"] = "No fund traction data. Run the fund traction sync first."
            return result
        result["month"] = target
        prior = _prior_available(
            conn=val_conn, table="fund_traction_funds", month=target
        )
        result["prior_month"] = prior

        try:
            base = val_conn.execute(
                """SELECT symbol, name, nse, sector, traction_score,
                          number_of_funds, adds_new, reduces_closes, pct_vs_sma
                   FROM fund_traction WHERE month = ?""",
                (target,),
            ).fetchall()
        except sqlite3.Error as exc:
            result["error"] = str(exc)
            return result

        # raw fund symbol -> canonical ticker, so a month-to-month format change
        # in the upstream artifact cannot masquerade as mass fund churn.
        key_map: dict[str, str] = {}
        try:
            for sym, nse in val_conn.execute(
                "SELECT symbol, MAX(nse) FROM fund_traction GROUP BY symbol"
            ):
                key_map[sym] = (nse or "").strip() or sym
        except sqlite3.Error:
            pass
        by_symbol, leaderboard = _cohorts(val_conn, target, prior, key_map)
        result["fund_leaderboard"] = leaderboard

        resolved = [(r[2] or "").strip() or r[0] for r in base]
        try:
            if tech_conn is None:
                tech_conn = sqlite3.connect(f"file:{_tech_path()}?mode=ro", uri=True)
            price_ctx = _month_price_context(tech_conn, sorted(set(resolved)), target)
        except sqlite3.Error:
            price_ctx = {}

        rows = []
        cohort_counts: dict[str, int] = {}
        below_dwap = 0
        for sym, name, nse, sector, score, fcount, adds, reduces, pct_sma in base:
            key = (nse or "").strip() or sym
            ctx = price_ctx.get(key, {})
            cmp_v = ctx.get("cmp")
            mhigh = ctx.get("month_high")
            mlow = ctx.get("month_low")
            dwap = ctx.get("dwap")
            rng = None
            if (
                cmp_v is not None
                and mhigh is not None
                and mlow is not None
                and mhigh > mlow
            ):
                rng = round((cmp_v - mlow) / (mhigh - mlow) * 100, 1)
            vs_dwap = None
            if cmp_v is not None and dwap:
                vs_dwap = round((cmp_v - dwap) / dwap * 100, 2)
                if vs_dwap < 0:
                    below_dwap += 1
            coh = by_symbol.get(key, {})
            label = coh.get("cohort", "unknown")
            cohort_counts[label] = cohort_counts.get(label, 0) + 1
            rows.append(
                {
                    "symbol": sym,
                    "resolved": key,
                    "name": name or sym,
                    "nse": nse or "",
                    "sector": sector or "",
                    "traction_score": score,
                    "fund_count": fcount,
                    "adds_new": adds,
                    "reduces_closes": reduces,
                    "pct_vs_sma": pct_sma,
                    "cmp": cmp_v,
                    "month_high": mhigh,
                    "month_low": mlow,
                    "range_position": rng,
                    "dwap": dwap,
                    "vs_dwap_pct": vs_dwap,
                    "has_market_data": cmp_v is not None,
                    **coh,
                }
            )

        rows.sort(key=lambda r: (r.get("traction_score") or 0), reverse=True)
        with_ctx = sum(1 for r in rows if r["has_market_data"])
        result["rows"] = rows
        result["count"] = len(rows)
        result["stats"] = {
            "total": len(rows),
            "with_price_context": with_ctx,
            "without_market_data": len(rows) - with_ctx,
            "below_dwap": below_dwap,
            "cohorts": cohort_counts,
        }
        result["success"] = True
        return result
    finally:
        if own_tech and tech_conn is not None:
            tech_conn.close()
        if own_val:
            val_conn.close()
