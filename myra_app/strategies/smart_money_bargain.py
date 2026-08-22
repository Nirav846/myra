"""
Smart Money Bargain Scanner
============================
Combines DCB discount (delivery-weighted cost basis) with fund traction data
to find stocks where institutional buying pressure meets price dislocation.

Signal: DCB discount >= 15% AND aggregated traction score >= 30 AND optionally
pct_vs_sma < 10%.

Traction aggregation options:
  - "latest":   use the most recent month's score
  - "max":      use the maximum score in the window (default)
  - "average":  use the mean score in the window
  - "momentum": latest_score - previous_month_score

Backtest (Apr-Jul 2026, 3M horizon):
  - threshold=30, discount>=15%: +17.06% avg return, 80% win rate, Sharpe 3.41
"""

import logging
import os
import sqlite3
from datetime import date

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.strategies.dcb_bargain import DCBBargainScanner

logger = logging.getLogger(__name__)

_VALID_AGGREGATIONS = {"latest", "max", "average", "momentum"}


class SmartMoneyBargainScanner(DCBBargainScanner):
    """Extends DCBBargainScanner with fund traction filtering.

    Runs DCB scan first (inherited), then joins fund_traction data and
    filters by aggregated traction score >= min_traction_score and optionally
    pct_vs_sma < max_pct_vs_sma.

    Traction aggregation can use a multi-month window to smooth noise:
      - "latest":   use only the latest month's score
      - "max":      best score in the window (catches spikes)
      - "average":  mean score over the window (smooths outliers)
      - "momentum": latest - previous (captures direction of change)
    """

    def __init__(
        self,
        min_traction_score: float = 30.0,
        max_pct_vs_sma: float = 10.0,
        filter_pct_vs_sma: bool = True,
        traction_window: int = 3,
        traction_aggregation: str = "max",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.min_traction_score = min_traction_score
        self.max_pct_vs_sma = max_pct_vs_sma
        self.filter_pct_vs_sma = filter_pct_vs_sma
        self.traction_window = max(1, traction_window)
        if traction_aggregation not in _VALID_AGGREGATIONS:
            raise ValueError(
                f"traction_aggregation must be one of {_VALID_AGGREGATIONS}, "
                f"got {traction_aggregation!r}"
            )
        self.traction_aggregation = traction_aggregation

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_available_months(self) -> list[str]:
        """Return sorted list of all available months (ascending)."""
        val_db = os.path.join(DB_DIR, "myra_valuation.db")
        if not os.path.exists(val_db):
            return []
        conn = sqlite3.connect(val_db)
        try:
            rows = conn.execute(
                "SELECT DISTINCT month FROM fund_traction ORDER BY month ASC"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def _get_traction_months(self, latest_month: str | None = None) -> list[str]:
        """Return up to `traction_window` months ending at `latest_month`."""
        all_months = self._get_available_months()
        if not all_months:
            return []
        if not latest_month:
            latest_month = all_months[-1]
        # Filter to months <= latest_month and take the last N
        eligible = [m for m in all_months if m <= latest_month]
        return eligible[-self.traction_window:]

    def _get_traction_data_multi(
        self, months: list[str]
    ) -> dict[str, list[dict]]:
        """Return {symbol: [{month, traction_score, ...}, ...]} for all rows
        across the given months, sorted by month ascending per symbol."""
        if not months:
            return {}
        val_db = os.path.join(DB_DIR, "myra_valuation.db")
        if not os.path.exists(val_db):
            return {}
        conn = sqlite3.connect(val_db)
        conn.row_factory = sqlite3.Row
        try:
            ph = ",".join("?" for _ in months)
            rows = conn.execute(
                f"""SELECT symbol, month, traction_score, number_of_funds,
                           adds_new, reduces_closes, pct_vs_sma,
                           sma_30, month_end_close, close_latest
                    FROM fund_traction
                    WHERE month IN ({ph})
                    ORDER BY symbol, month ASC""",
                months,
            ).fetchall()
            result: dict[str, list[dict]] = {}
            for r in rows:
                d = dict(r)
                result.setdefault(d["symbol"], []).append(d)
            return result
        finally:
            conn.close()

    @staticmethod
    def _aggregate_scores(
        records: list[dict], method: str
    ) -> tuple[float | None, dict]:
        """Compute an aggregated traction score from a list of monthly records
        (sorted by month ascending).

        Returns (aggregated_score, metadata_dict).
        metadata_dict includes raw values for transparency.
        """
        scores = [(r["month"], r.get("traction_score") or 0.0) for r in records]
        meta = {"months_used": len(scores), "months": [s[0] for s in scores]}

        if not scores:
            return None, meta

        if method == "latest":
            val = scores[-1][1]
            meta["aggregation_detail"] = f"latest ({scores[-1][0]})"
            return round(val, 2), meta

        if method == "max":
            best = max(scores, key=lambda x: x[1])
            val = best[1]
            meta["aggregation_detail"] = f"max {val:.1f} in {best[0]}"
            return round(val, 2), meta

        if method == "average":
            vals = [s[1] for s in scores]
            val = float(np.mean(vals))
            meta["aggregation_detail"] = f"avg {val:.1f} over {len(vals)}mo"
            return round(val, 2), meta

        if method == "momentum":
            if len(scores) < 2:
                # Not enough data for momentum — use 0 as fallback
                meta["aggregation_detail"] = "momentum: insufficient data"
                return 0.0, meta
            prev_score = scores[-2][1]
            curr_score = scores[-1][1]
            val = curr_score - prev_score
            meta["aggregation_detail"] = (
                f"momentum: {scores[-1][0]}({curr_score:.1f}) "
                f"- {scores[-2][0]}({prev_score:.1f}) = {val:+.1f}"
            )
            return round(val, 2), meta

        # Should never reach here due to __init__ validation
        return None, meta

    # ── main scan ────────────────────────────────────────────────────────

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        """Run DCB scan, then filter/join with fund traction data."""
        # Step 1: run parent DCB scan (universe + DCB + delivery filters)
        dcb_df = super().scan(as_on_date=as_on_date)
        if dcb_df.empty:
            return dcb_df

        # Step 2: determine traction months and fetch multi-month data
        latest_month = self._get_latest_traction_month()
        if not latest_month:
            logger.warning("Smart Money Bargain: no traction data available")
            return pd.DataFrame()
        window_months = self._get_traction_months(latest_month)
        logger.info(
            "Smart Money Bargain: traction window=%d months=%s method=%s",
            self.traction_window, window_months, self.traction_aggregation,
        )
        multi_data = self._get_traction_data_multi(window_months)
        if not multi_data:
            logger.warning("Smart Money Bargain: no traction data for window")
            return pd.DataFrame()

        # Step 3: merge and filter
        candidates = []
        for _, row in dcb_df.iterrows():
            symbol = row["symbol"]
            records = multi_data.get(symbol)
            if not records:
                continue

            agg_score, agg_meta = self._aggregate_scores(
                records, self.traction_aggregation
            )
            if agg_score is None:
                continue

            if agg_score < self.min_traction_score:
                continue

            # Use latest month's pct_vs_sma for the overbought filter
            latest_rec = records[-1]
            pct_vs_sma = latest_rec.get("pct_vs_sma")
            if (
                self.filter_pct_vs_sma
                and pct_vs_sma is not None
                and pct_vs_sma > self.max_pct_vs_sma
            ):
                continue

            rec = row.to_dict()
            # Original traction fields (from latest month)
            rec["traction_score"] = round(float(latest_rec.get("traction_score") or 0), 2)
            rec["traction_aggregated"] = agg_score
            rec["traction_method"] = self.traction_aggregation
            rec["traction_window"] = self.traction_window
            rec["traction_months"] = window_months
            rec["traction_detail"] = agg_meta.get("aggregation_detail", "")
            rec["fund_count"] = latest_rec.get("number_of_funds")
            rec["adds_new"] = latest_rec.get("adds_new")
            rec["reduces_closes"] = latest_rec.get("reduces_closes")
            rec["net_adds"] = (latest_rec.get("adds_new") or 0) - (
                latest_rec.get("reduces_closes") or 0
            )
            rec["pct_vs_sma_traction"] = (
                round(float(pct_vs_sma), 2) if pct_vs_sma is not None else None
            )
            rec["sma_30"] = latest_rec.get("sma_30")
            rec["month_end_close"] = latest_rec.get("month_end_close")
            rec["close_latest"] = latest_rec.get("close_latest")
            # Combined score: DCB discount * 0.4 + aggregated traction * 0.4 + del_abs * 0.2
            disc = float(rec.get("discount_pct", 0))
            dabs = float(rec.get("del_abs", 0))
            rec["combined_score"] = round(
                disc * 0.4 + agg_score * 0.4 + dabs * 0.2, 2
            )
            candidates.append(rec)

        if not candidates:
            return pd.DataFrame()

        # Sort by combined score descending
        candidates.sort(key=lambda x: x.get("combined_score", 0), reverse=True)
        return pd.DataFrame(candidates)
