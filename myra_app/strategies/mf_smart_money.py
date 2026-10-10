"""MF Smart Money Scanner -- every stock mutual funds hold, with the *why*.

The Traction Board is built from the published artifact, which is generated with
``include_holds=false`` and therefore silently drops every stock a fund merely
holds without change (~270 of ~1017 for 2026-09).  It also shows a single
dimension: a month-over-month score.

This scanner reads MYRA's own RupeeVest ingestion (``mf_holding`` /
``mf_fund_aum``) and shows the **complete** held universe, including steady
holds, enriched with:

* **Ownership breadth** -- how many funds hold it and the summed % of their AUM
  (``fund_count``, ``total_weight_pct``), plus a rupee footprint
  (``aum_held_cr`` = sum over holders of ``weight% x holder AUM``).
* **Fund-level month-over-month behaviour** -- funds that *entered* / *exited*
  the register and funds that *added* / *trimmed* shares, derived from the
  per-fund holdings of two consecutive months.  ``direction`` rolls this up to
  increase / decrease / mixed / steady, so a "steady hold" (no fund changed
  anything) is visible instead of invisible.
* **Institutional cost basis vs price** -- delivery-weighted acquisition price
  (``dwap``) for the month, CMP, and ``vs_dwap_pct``.  Negative means the stock
  trades *below* the price at which funds accumulated it.
* Sector / market cap and the traction score when the ticker matches the
  published artifact.

Read-only and best-effort: a stock whose company name cannot be resolved to an
NSE ticker is still listed (``has_price = False``) rather than dropped -- data
gaps are surfaced, never hidden.

``mode`` presets:
  * ``all``          -- every held stock (default)
  * ``accumulating`` -- net fund buying (entered/added outweigh exited/trimmed)
  * ``steady``       -- held with no fund changing its position at all
  * ``trimming``     -- net fund selling
  * ``bargain``      -- steady or accumulating **and** trading below the
                        delivery-weighted acquisition price (CMP < DWAP)
"""

from __future__ import annotations

import logging
import os
import sqlite3

import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

MODES = ("all", "accumulating", "steady", "trimming", "bargain")
SORT_KEYS = (
    "traction_score",
    "vs_dwap_pct",
    "fund_count",
    "aum_held_cr",
    "share_change_pct",
)

_CHUNK = 400


def _chunks(seq, size=_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _prior_month(months, month):
    """Previous available month strictly before ``month`` (or ``None``)."""
    eligible = [m for m in sorted(months) if m < month]
    return eligible[-1] if eligible else None


class MFSmartMoneyScanner:
    """Whole-universe mutual-fund holdings screener (read-only)."""

    def __init__(
        self,
        month: str | None = None,
        mode: str = "all",
        min_funds: int = 1,
        min_total_weight_pct: float = 0.0,
        min_mcap_cr: float = 0.0,
        max_mcap_cr: float = 0.0,
        max_vs_dwap_pct: float | None = None,
        min_aum_held_cr: float = 0.0,
        require_price: bool = False,
        sort_by: str = "traction_score",
        limit: int | None = None,
        resolve_tickers: bool = True,
        val_conn: sqlite3.Connection | None = None,
        tech_conn: sqlite3.Connection | None = None,
    ):
        mode = (mode or "all").strip().lower()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        if sort_by not in SORT_KEYS:
            raise ValueError(f"sort_by must be one of {SORT_KEYS}, got {sort_by!r}")
        self.month = month
        self.mode = mode
        self.min_funds = max(1, int(min_funds))
        self.min_total_weight_pct = float(min_total_weight_pct)
        self.min_mcap_cr = float(min_mcap_cr)
        self.max_mcap_cr = float(max_mcap_cr)
        self.max_vs_dwap_pct = max_vs_dwap_pct
        self.min_aum_held_cr = float(min_aum_held_cr)
        self.require_price = bool(require_price)
        self.sort_by = sort_by
        self.limit = limit
        self.resolve_tickers = bool(resolve_tickers)
        self._val_conn = val_conn
        self._tech_conn = tech_conn

    # -- connections --------------------------------------------------------

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _val(self) -> sqlite3.Connection:
        if self._val_conn is None:
            self._val_conn = sqlite3.connect(
                f"file:{self._db_path('valuation')}?mode=ro", uri=True
            )
        return self._val_conn

    def _tech(self) -> sqlite3.Connection:
        if self._tech_conn is None:
            self._tech_conn = sqlite3.connect(
                f"file:{self._db_path('technical')}?mode=ro", uri=True
            )
        return self._tech_conn

    # -- universe -----------------------------------------------------------

    def _get_universe(self) -> list[dict]:
        """Universe for the runner's progress accounting (length only)."""
        return self._holdings_agg(self.month or self._best_month())

    # -- aggregation --------------------------------------------------------

    def _best_month(self) -> str | None:
        """Month with the widest fund coverage (newest on ties).

        RupeeVest discloses a month fund by fund, so the newest month can be
        partial (22/62 funds for 2026-09) while the month before is complete.
        Picking the widest month keeps the universe stable and complete.
        """
        if self.month:
            return self.month
        try:
            row = (
                self._val()
                .execute(
                    "SELECT month FROM mf_holding GROUP BY month "
                    "ORDER BY COUNT(DISTINCT fund_slug) DESC, month DESC LIMIT 1"
                )
                .fetchone()
            )
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _holdings_agg(self, month: str | None) -> list[dict]:
        """Per-stock aggregate of one month's holdings (no price data)."""
        if not month:
            return []
        try:
            rows = (
                self._val()
                .execute(
                    """SELECT fincode, MAX(company) AS company,
                          COUNT(DISTINCT fund_slug) AS fund_count,
                          SUM(COALESCE(weight_pct, 0)) AS total_weight_pct
                   FROM mf_holding WHERE month = ?
                   GROUP BY fincode""",
                    (month,),
                )
                .fetchall()
            )
        except sqlite3.Error as exc:
            logger.warning("mf_smart_money holdings agg failed: %s", exc)
            return []
        return [
            {
                "fincode": str(f),
                "company": c or f"fincode:{f}",
                "fund_count": int(fc or 0),
                "total_weight_pct": float(w or 0.0),
            }
            for f, c, fc, w in rows
        ]

    # -- enrichment ---------------------------------------------------------

    def _name_index(self):
        """Local company-name -> ticker index (symbols_master + alias + csv)."""
        from myra_app.traction_symbols import _build_index, load_name_to_nse

        meta_path = self._db_path("meta")
        master, alias = [], []
        try:
            with sqlite3.connect(f"file:{meta_path}?mode=ro", uri=True) as mc:
                master = mc.execute(
                    "SELECT symbol, name FROM symbols_master"
                ).fetchall()
                try:
                    alias = mc.execute(
                        "SELECT alias, canonical FROM symbol_alias"
                    ).fetchall()
                except sqlite3.Error:
                    alias = []
        except sqlite3.Error as exc:
            logger.warning("mf_smart_money: meta index unavailable (%s)", exc)
        return _build_index(master, alias, load_name_to_nse())

    def _resolve_tickers(self, companies):
        from myra_app.traction_symbols import resolve_local

        out = {}
        if not self.resolve_tickers:
            return out
        index = self._name_index()
        for company in companies:
            res = resolve_local(company, company, index)
            out[company] = res.code if res else None
        return out

    def _price_context(self, symbols, month, as_on_date=None):
        """{symbol: {dwap, month_high, month_low, cmp}} (chunked; best-effort)."""
        out: dict[str, dict] = {}
        if not symbols:
            return out
        tech = self._tech()
        for chunk in _chunks(sorted(symbols)):
            ph = ",".join("?" for _ in chunk)
            try:
                rows = tech.execute(
                    f"""SELECT symbol, MAX(high), MIN(low),
                               SUM(delivery * ((high + low + close) / 3.0)),
                               SUM(delivery)
                        FROM technical_data
                        WHERE substr(date, 1, 7) = ? AND symbol IN ({ph})
                          AND high IS NOT NULL AND low IS NOT NULL
                        GROUP BY symbol""",
                    [month, *chunk],
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for sym, mhigh, mlow, num, den in rows:
                dwap = (num / den) if (num is not None and den) else None
                out[str(sym)] = {
                    "month_high": mhigh,
                    "month_low": mlow,
                    "dwap": round(dwap, 2) if dwap else None,
                }
        for chunk in _chunks(sorted(symbols)):
            ph = ",".join("?" for _ in chunk)
            params = [*chunk]
            date_clause = ""
            if as_on_date:
                date_clause = "AND date <= ?"
                params = [*chunk, as_on_date]
            try:
                rows = tech.execute(
                    f"""SELECT symbol, close FROM (
                            SELECT symbol, close,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY symbol ORDER BY date DESC
                                   ) AS rn
                            FROM technical_data
                            WHERE symbol IN ({ph}) {date_clause}
                        ) WHERE rn = 1""",
                    params,
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for sym, close in rows:
                out.setdefault(str(sym), {})["cmp"] = close
        return out

    def _fundamentals(self, symbols):
        out: dict[str, dict] = {}
        if not symbols:
            return out
        val = self._val()
        for chunk in _chunks(sorted(symbols)):
            ph = ",".join("?" for _ in chunk)
            try:
                rows = val.execute(
                    f"""SELECT f.symbol, f.market_cap, f.sector, f.free_float_pct
                        FROM fundamentals f
                        INNER JOIN (
                            SELECT symbol, MAX(date) AS max_date FROM fundamentals
                            GROUP BY symbol
                        ) latest
                          ON f.symbol = latest.symbol AND f.date = latest.max_date
                        WHERE f.symbol IN ({ph})""",
                    chunk,
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for sym, mcap, sector, ff in rows:
                out[str(sym)] = {"market_cap": mcap, "sector": sector, "ff_pct": ff}
        return out

    def _traction(self, symbols, month, as_on_date):
        """{ticker: row} from fund_traction for the latest month <= as_on_date."""
        if not symbols:
            return {}
        cutoff = as_on_date[:7] if as_on_date else month
        out = {}
        try:
            ph = ",".join("?" for _ in symbols)
            rows = (
                self._val()
                .execute(
                    f"""SELECT COALESCE(NULLIF(TRIM(nse), ''), symbol) AS ticker,
                           traction_score, number_of_funds, adds_new,
                           reduces_closes, pct_vs_sma, direction
                    FROM fund_traction
                    WHERE month = (SELECT MAX(month) FROM fund_traction WHERE month <= ?)
                      AND COALESCE(NULLIF(TRIM(nse), ''), symbol) IN ({ph})""",
                    [cutoff, *symbols],
                )
                .fetchall()
            )
        except sqlite3.Error:
            return {}
        for t in rows:
            out[str(t[0])] = {
                "traction_score": t[1],
                "traction_funds": t[2],
                "traction_adds": t[3],
                "traction_reduces": t[4],
                "pct_vs_sma": t[5],
                "traction_direction": t[6],
            }
        return out

    # -- scan ---------------------------------------------------------------

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        month = self.month or self._best_month()
        if not month:
            logger.warning("MF Smart Money: no mutual-fund holdings ingested yet")
            return pd.DataFrame()

        months = [
            r[0]
            for r in self._val()
            .execute("SELECT DISTINCT month FROM mf_holding ORDER BY month")
            .fetchall()
        ]
        prior = _prior_month(months, month)

        agg = self._holdings_agg(month)
        if not agg:
            logger.warning("MF Smart Money: no holdings for month %s", month)
            return pd.DataFrame()

        cur_by_fund = self._fund_holdings_by_stock(month)
        prev_by_fund = self._fund_holdings_by_stock(prior) if prior else {}
        aum = self._fund_aum_map(month, prior)

        tickers = self._resolve_tickers({r["company"] for r in agg})
        symbols = sorted({t for t in tickers.values() if t})
        price = self._price_context(symbols, month, as_on_date)
        fundy = self._fundamentals(symbols)
        traction = self._traction(symbols, month, as_on_date)

        rows = []
        for base in agg:
            fincode = base["fincode"]
            company = base["company"]
            ticker = tickers.get(company)
            cur = cur_by_fund.get(fincode, {})
            prev = prev_by_fund.get(fincode, {})
            metrics = self._ownership_metrics(cur, prev)
            ctx = price.get(ticker, {}) if ticker else {}
            fnd = fundy.get(ticker, {}) if ticker else {}
            trac = traction.get(ticker, {}) if ticker else {}

            cmp_v = ctx.get("cmp")
            dwap = ctx.get("dwap")
            vs_dwap = None
            if cmp_v is not None and dwap:
                vs_dwap = round((cmp_v - dwap) / dwap * 100, 2)
            mhigh, mlow = ctx.get("month_high"), ctx.get("month_low")
            range_pos = None
            if cmp_v is not None and mhigh and mlow and mhigh > mlow:
                range_pos = round((cmp_v - mlow) / (mhigh - mlow) * 100, 1)

            holders_aum = sum(
                aum.get(slug, 0.0) * (v.get("w") or 0) / 100.0
                for slug, v in cur.items()
                if aum.get(slug)
            )

            mcap_cr = (
                round(fnd["market_cap"] / 1e7, 2) if fnd.get("market_cap") else None
            )

            row = {
                "symbol": ticker or "",
                "company": company,
                "name": company,
                "sector": fnd.get("sector") or "",
                "fund_count": base["fund_count"],
                "prev_fund_count": metrics["prev_fund_count"],
                "net_funds": metrics["net_funds"],
                "funds_entered": metrics["funds_entered"],
                "funds_exited": metrics["funds_exited"],
                "funds_added": metrics["funds_added"],
                "funds_trimmed": metrics["funds_trimmed"],
                "funds_steady": metrics["funds_steady"],
                "total_weight_pct": round(base["total_weight_pct"], 2),
                "stake_change_pp": metrics["stake_change_pp"],
                "share_change_pct": metrics["share_change_pct"],
                "aum_held_cr": round(holders_aum, 1) if holders_aum else None,
                "market_cap_cr": mcap_cr,
                "cmp": cmp_v,
                "dwap": dwap,
                "vs_dwap_pct": vs_dwap,
                "month_high": mhigh,
                "month_low": mlow,
                "range_position": range_pos,
                "direction": metrics["direction"],
                "has_price": cmp_v is not None,
                "traction_score": trac.get("traction_score"),
                "pct_vs_sma": trac.get("pct_vs_sma"),
                "traction_direction": trac.get("traction_direction"),
                "month": month,
                "prior_month": prior,
            }
            if self._keep(row):
                rows.append(row)

        rows.sort(key=self._sort_key, reverse=self._sort_desc())
        if self.limit:
            rows = rows[: self.limit]
        logger.info(
            "MF Smart Money scan: month=%s mode=%s holdings=%d -> %d rows",
            month,
            self.mode,
            len(agg),
            len(rows),
        )
        return pd.DataFrame(rows)

    # -- behaviour helpers --------------------------------------------------

    def _fund_holdings_by_stock(self, month):
        """{fincode: {fund_slug: {'w': weight_pct, 's': shares}}} for a month."""
        out: dict[str, dict] = {}
        if not month:
            return out
        try:
            cur = (
                self._val()
                .execute(
                    "SELECT fincode, fund_slug, weight_pct, shares FROM mf_holding "
                    "WHERE month = ?",
                    (month,),
                )
                .fetchall()
            )
        except sqlite3.Error:
            return out
        for fincode, slug, w, s in cur:
            out.setdefault(str(fincode), {})[slug] = {"w": w or 0.0, "s": s}
        return out

    def _fund_aum_map(self, month, prior):
        """Fund AUM lookup tolerant of a month missing from ``mf_fund_aum``."""
        aum = self._fund_aum(month)
        if prior:
            for slug, val in self._fund_aum(prior).items():
                aum.setdefault(slug, val)
        return aum

    def _fund_aum(self, month):
        if not month:
            return {}
        try:
            rows = (
                self._val()
                .execute(
                    "SELECT fund_slug, aum_cr FROM mf_fund_aum WHERE month = ?",
                    (month,),
                )
                .fetchall()
            )
        except sqlite3.Error:
            return {}
        return {r[0]: r[1] for r in rows if r[0]}

    @staticmethod
    def _ownership_metrics(cur: dict, prev: dict) -> dict:
        """Roll per-fund holdings into entry/exit/add/trim counts + direction."""
        entered = [k for k in cur if k not in prev]
        exited = [k for k in prev if k not in cur]
        held = [k for k in cur if k in prev]
        added, trimmed, steady = 0, 0, 0
        for k in held:
            cs, ps = cur[k].get("s"), prev[k].get("s")
            if cs is None or ps is None or cs == ps:
                steady += 1
            elif cs > ps:
                added += 1
            else:
                trimmed += 1

        net = (added + len(entered)) - (trimmed + len(exited))
        if not (added or trimmed or entered or exited):
            direction = "steady"
        elif net > 0:
            direction = "increase"
        elif net < 0:
            direction = "decrease"
        else:
            direction = "mixed"

        stake_delta = sum(v.get("w", 0.0) for v in cur.values()) - sum(
            v.get("w", 0.0) for v in prev.values()
        )
        share_moves = []
        for k in held:
            cs, ps = cur[k].get("s"), prev[k].get("s")
            if cs is not None and ps not in (None, 0):
                share_moves.append((cs - ps) / ps * 100.0)
        share_pct = (
            round(sum(share_moves) / len(share_moves), 2) if share_moves else None
        )

        return {
            "prev_fund_count": len(prev),
            "net_funds": net,
            "funds_entered": len(entered),
            "funds_exited": len(exited),
            "funds_added": added,
            "funds_trimmed": trimmed,
            "funds_steady": steady,
            "stake_change_pp": round(stake_delta, 2),
            "share_change_pct": share_pct,
            "direction": direction,
        }

    def _keep(self, row) -> bool:
        if row["fund_count"] < self.min_funds:
            return False
        if row["total_weight_pct"] < self.min_total_weight_pct:
            return False
        mcap = row["market_cap_cr"]
        if self.min_mcap_cr and (mcap is None or mcap < self.min_mcap_cr):
            return False
        if self.max_mcap_cr and mcap is not None and mcap > self.max_mcap_cr:
            return False
        if self.max_vs_dwap_pct is not None:
            if row["vs_dwap_pct"] is None or row["vs_dwap_pct"] > self.max_vs_dwap_pct:
                return False
        if self.min_aum_held_cr and (
            not row["aum_held_cr"] or row["aum_held_cr"] < self.min_aum_held_cr
        ):
            return False
        if self.require_price and not row["has_price"]:
            return False

        mode = self.mode
        if mode == "all":
            return True
        if mode == "steady":
            return row["direction"] == "steady"
        if mode == "accumulating":
            return row["direction"] == "increase"
        if mode == "trimming":
            return row["direction"] == "decrease"
        if mode == "bargain":
            below = row["vs_dwap_pct"] is not None and row["vs_dwap_pct"] < 0
            return below and row["direction"] in ("steady", "increase")
        return True

    def _sort_key(self, row):
        key = self.sort_by
        if key == "vs_dwap_pct":
            v = row.get("vs_dwap_pct")
            return v if v is not None else float("inf")
        if key == "share_change_pct":
            v = row.get("share_change_pct")
            return v if v is not None else float("-inf")
        v = row.get(key)
        return v if v is not None else float("-inf")

    def _sort_desc(self) -> bool:
        # vs_dwap_pct sorts ascending (most-below-cost first); rest descending.
        return self.sort_by != "vs_dwap_pct"

    # -- runner contract ----------------------------------------------------

    def _get_tech_data(self, symbol, min_date=None, max_date=None):
        """No per-symbol OHLCV loop: price context is one grouped SQL pass.

        Present because the scanner runner wraps this attribute for progress.
        """
        return None
