import logging
import math
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date, datetime
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


class SeasonalDeliveryHarvester:
    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        min_hist_del=40.0,
        min_consistency_pct=55.0,
        min_years=2,
        target_month=None,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_hist_del = min_hist_del
        self.min_consistency_pct = min_consistency_pct
        self.min_years = min_years
        self.target_month = target_month

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        val_db = self._db_path("valuation")
        if not os.path.exists(val_db):
            return []
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
                """
                SELECT f.symbol,
                       COALESCE(f.market_cap, 0) AS mcap,
                       COALESCE(f.free_float_pct, 40.0) AS ff_pct
                FROM fundamentals f
                INNER JOIN (
                    SELECT symbol, MAX(date) as max_date
                    FROM fundamentals
                    WHERE COALESCE(market_cap, 0) > 0
                    GROUP BY symbol
                ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                WHERE COALESCE(f.market_cap, 0) / 1e7 BETWEEN ? AND ?
                """,
                (self.min_mcap, self.max_mcap),
            ).fetchall()
        return rows

    def _get_all_tech_data(
        self, symbol: str, max_date: str | None = None
    ) -> list[tuple]:
        max_date = max_date or date.today().isoformat()
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT date, close, delivery_pct, nifty_outperformance_score,
                           high_52w, low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date <= ?
                    ORDER BY date ASC
                    """,
                    (symbol, max_date),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT date, close, delivery_pct, nifty_outperformance_score,
                           NULL AS high_52w, NULL AS low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date <= ?
                    ORDER BY date ASC
                    """,
                    (symbol, max_date),
                ).fetchall()
        return rows

    @staticmethod
    def _sanitize_float(value):
        if value is None:
            return None
        try:
            if math.isnan(value) or math.isinf(value):
                return None
        except TypeError:
            pass
        return value

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        rows = self._get_universe()
        if not rows:
            logger.warning(
                "No symbols found in universe (mcap %.0f-%.0f Cr)",
                self.min_mcap,
                self.max_mcap,
            )
            return pd.DataFrame()

        _sector_map: dict[str, str] = {}
        try:
            val_db = self._db_path("valuation")
            with sqlite3.connect(val_db) as _sc:
                _sec_rows = _sc.execute(
                    """
                    SELECT f.symbol, f.sector
                    FROM fundamentals f
                    INNER JOIN (
                        SELECT symbol, MAX(date) as max_date
                        FROM fundamentals
                        WHERE sector IS NOT NULL
                        GROUP BY symbol
                    ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                    WHERE f.sector IS NOT NULL
                    """
                ).fetchall()
                _sector_map = {r[0].strip(): r[1] for r in _sec_rows}
        except Exception:
            pass

        today = date.today()
        current_month = (
            self.target_month if self.target_month is not None else today.month
        )
        current_year = today.year
        is_current_or_past = current_month < today.month or (
            current_month == today.month and today.year >= current_year
        )

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_all_tech_data(symbol, max_date=as_on_date)
            if len(tech) < 60:
                continue

            col_count = len(tech[0]) if tech else 0
            cols = ["date", "close", "delivery_pct", "nifty_outperformance_score"]
            if col_count >= 6:
                cols += ["high_52w", "low_52w"]
            else:
                cols += ["high_52w", "low_52w"]

            df = pd.DataFrame(tech, columns=cols)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df["year"] = df["date"].dt.year
            df["month"] = df["date"].dt.month

            # Skip symbols with data in only 1 year
            years_available = df["year"].nunique()
            if years_available < self.min_years:
                continue

            # Build seasonal profile: monthly averages per year
            monthly_stats = (
                df.groupby(["year", "month"])["delivery_pct"].mean().reset_index()
            )
            monthly_stats.columns = ["year", "month", "avg_del_month"]

            grand_avg = float(df["delivery_pct"].mean()) if len(df) > 0 else 0

            # For each month, compute historical average excluding current year
            month_profile = monthly_stats[monthly_stats["year"] < current_year].copy()
            if month_profile.empty:
                continue

            hist_rows = month_profile[month_profile["month"] == current_month]
            if hist_rows.empty:
                continue

            hist_avg_del = float(hist_rows["avg_del_month"].mean())
            years_of_data = int(hist_rows["year"].nunique())

            # Consistency: how many years was this month above grand average
            if years_of_data > 0:
                years_above = int((hist_rows["avg_del_month"] > grand_avg).sum())
                consistency_pct = years_above / years_of_data * 100
            else:
                consistency_pct = 0.0

            if hist_avg_del < self.min_hist_del:
                continue
            if consistency_pct < self.min_consistency_pct:
                continue
            if years_of_data < self.min_years:
                continue

            # Current month data
            current_month_df = df[
                (df["year"] == current_year) & (df["month"] == current_month)
            ]
            trading_days_so_far = len(current_month_df)

            current_del = None
            seasonal_edge = None
            early_signal = False
            seasonal_score = None

            if is_current_or_past and trading_days_so_far >= 3:
                current_del = float(current_month_df["delivery_pct"].mean())
                seasonal_edge = current_del - hist_avg_del

                if current_del <= hist_avg_del:
                    continue

                early_signal = trading_days_so_far <= 5 and seasonal_edge > 5
                seasonal_score = (
                    seasonal_edge * 2 + consistency_pct * 0.4 + years_of_data * 3
                )

            elif not is_current_or_past:
                # Preview mode: show historical stats only
                seasonal_score = (
                    hist_avg_del * 0.3 + consistency_pct * 0.4 + years_of_data * 3
                )

            if seasonal_score is None:
                continue

            if seasonal_score >= 75:
                grade = "A"
            elif seasonal_score >= 55:
                grade = "B"
            elif seasonal_score >= 35:
                grade = "C"
            else:
                grade = "D"

            # 52-week position
            latest_close = float(df["close"].iloc[-1])
            high_52w = (
                float(df["high_52w"].iloc[-1])
                if "high_52w" in df.columns and pd.notna(df["high_52w"].iloc[-1])
                else float(df["close"].max())
            )
            low_52w = (
                float(df["low_52w"].iloc[-1])
                if "low_52w" in df.columns and pd.notna(df["low_52w"].iloc[-1])
                else float(df["close"].min())
            )
            wk52_pos = (
                ((latest_close - low_52w) / (high_52w - low_52w)) * 100
                if (high_52w - low_52w) > 0
                else 50.0
            )

            mcap_cr = mcap / 1e7

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap_cr, 1),
                    "current_month": MONTH_NAMES[current_month],
                    "hist_avg_del": round(hist_avg_del, 1),
                    "current_del": round(current_del, 1)
                    if current_del is not None
                    else None,
                    "seasonal_edge": round(seasonal_edge, 1)
                    if seasonal_edge is not None
                    else None,
                    "consistency_pct": round(consistency_pct, 1),
                    "years_of_data": years_of_data,
                    "early_signal": early_signal,
                    "seasonal_score": round(seasonal_score, 1),
                    "grade": grade,
                    "close": round(latest_close, 2),
                    "wk52_pos": round(wk52_pos, 1),
                    "trading_days_so_far": trading_days_so_far,
                }
            )

        float_fields = [
            "market_cap_cr",
            "hist_avg_del",
            "current_del",
            "seasonal_edge",
            "consistency_pct",
            "seasonal_score",
            "close",
            "wk52_pos",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x.get("seasonal_score") or 0, reverse=True)
        logger.info(
            "Seasonal Delivery scan complete: %d candidates found", len(candidates)
        )
        return pd.DataFrame(candidates)
