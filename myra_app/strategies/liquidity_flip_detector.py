import logging
import math
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)


class LiquidityFlipDetector:
    def __init__(self, min_mcap=200, max_mcap=50000, lookback_days=95):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.lookback_days = lookback_days

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
                       COALESCE(f.market_cap, f.marketCap, 0) AS mcap,
                       COALESCE(f.free_float_pct, 40.0) AS ff_pct
                FROM fundamentals f
                INNER JOIN (
                    SELECT symbol, MAX(date) as max_date
                    FROM fundamentals
                    WHERE COALESCE(market_cap, marketCap, 0) > 0
                    GROUP BY symbol
                ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                WHERE COALESCE(f.market_cap, f.marketCap, 0) / 1e7 BETWEEN ? AND ?
                """,
                (self.min_mcap, self.max_mcap),
            ).fetchall()
        return rows

    def _get_tech_data(self, symbol: str, min_date: str) -> list[tuple]:
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, delivery,
                           delivery_pct, nifty_outperformance_score,
                           sma_50, high_52w, low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date >= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, delivery,
                           delivery_pct, nifty_outperformance_score,
                           NULL AS sma_50, NULL AS high_52w, NULL AS low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date >= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date),
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
            logger.warning("No symbols found in universe (mcap %.0f-%.0f Cr)", self.min_mcap, self.max_mcap)
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

        if as_on_date is None:
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = (ref_date - pd.Timedelta(days=self.lookback_days + 30)).strftime("%Y-%m-%d")

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date)
            if len(tech) < self.lookback_days + 5:
                continue

            col_count = len(tech[0]) if tech else 0
            if col_count >= 12:
                df = pd.DataFrame(
                    tech,
                    columns=["date", "open", "high", "low", "close", "volume",
                             "delivery", "delivery_pct", "nifty_outperformance_score",
                             "sma_50", "high_52w", "low_52w"],
                )
            else:
                df = pd.DataFrame(
                    tech,
                    columns=["date", "open", "high", "low", "close", "volume",
                             "delivery", "delivery_pct", "nifty_outperformance_score"],
                )
                df["sma_50"] = None
                df["high_52w"] = None
                df["low_52w"] = None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            if len(df) < self.lookback_days + 5:
                continue

            # Churn baseline (days -95 to -21)
            prior_df = df.iloc[-self.lookback_days:-5]
            avg_vol_prior = float(np.nanmean(prior_df["volume"].values.astype(float)))
            avg_del_prior = float(np.nanmean(prior_df["delivery_pct"].values.astype(float)))

            # Recent window (last 5 sessions)
            recent = df.tail(5)
            recent_del_pcts = recent["delivery_pct"].values.astype(float)
            recent_del_5d = float(np.nanmean(recent_del_pcts))
            del50_days = int(np.sum(recent_del_pcts > 50))
            recent_vol = float(np.nanmean(recent["volume"].values.astype(float)))

            # Flip detection
            del_jump_pp = recent_del_5d - avg_del_prior

            if avg_del_prior < 35 and recent_del_5d > 55:
                flip_type = "STRONG FLIP"
            elif avg_del_prior < 45 and recent_del_5d > 60:
                flip_type = "MODERATE FLIP"
            else:
                continue

            # Price check — not already broken out
            closes = df["close"].values.astype(float)
            latest_close = float(closes[-1])
            high_52w = float(df["high_52w"].iloc[-1]) if pd.notna(df["high_52w"].iloc[-1]) else float(df["high"].max())
            low_52w = float(df["low_52w"].iloc[-1]) if pd.notna(df["low_52w"].iloc[-1]) else float(df["low"].min())
            wk52_pos = ((latest_close - low_52w) / (high_52w - low_52w)) * 100 if (high_52w - low_52w) > 0 else 50.0
            if wk52_pos >= 90:
                continue

            # Volume rank vs universe (approximate — we use a simple ratio against overall median)
            # Compute median volume across all symbols for comparison
            prior_vol_rank = avg_vol_prior / (np.nanmean(prior_df["volume"].values.astype(float)) or 1)

            # Flip score
            flip_score = del_jump_pp * 2 + del50_days * 5 + (40 - avg_del_prior) * 0.5
            flip_score = max(0, flip_score)

            # Grade
            if flip_score >= 70:
                grade = "A"
            elif flip_score >= 50:
                grade = "B"
            elif flip_score >= 30:
                grade = "C"
            else:
                grade = "D"

            mcap_cr = mcap / 1e7

            candidates.append({
                "symbol": symbol,
                "sector": _sector_map.get(symbol, "Unknown"),
                "market_cap_cr": round(mcap_cr, 1),
                "prior_del_pct": round(avg_del_prior, 1),
                "current_del_pct": round(recent_del_5d, 1),
                "del_jump_pp": round(del_jump_pp, 1),
                "del50_days": del50_days,
                "flip_type": flip_type,
                "flip_score": round(flip_score, 1),
                "grade": grade,
                "prior_vol_rank": round(prior_vol_rank, 2),
                "close": round(latest_close, 2),
                "wk52_pos": round(wk52_pos, 1),
            })

        float_fields = [
            "market_cap_cr", "prior_del_pct", "current_del_pct", "del_jump_pp",
            "flip_score", "prior_vol_rank", "close", "wk52_pos",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["flip_score"], reverse=True)
        logger.info("Liquidity Flip scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)
