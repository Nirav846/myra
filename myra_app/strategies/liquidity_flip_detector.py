import logging
import math
from myra_app.strategies.scanner_utils import sanitize_float
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_12,
)

logger = logging.getLogger(__name__)


class LiquidityFlipDetector:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        prior_window=120,
        recent_window=30,
        lookback_days=150,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.prior_window = prior_window
        self.recent_window = recent_window
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

    def _get_tech_data(
        self, symbol: str, min_date: str, max_date: str | None = None
    ) -> list[tuple]:
        max_date = max_date or date.today().isoformat()
        if self._bulk_data is not None:
            return rows_for_symbol(
                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
            )
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
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date, max_date),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, delivery,
                           delivery_pct, nifty_outperformance_score,
                           NULL AS sma_50, NULL AS high_52w, NULL AS low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date, max_date),
                ).fetchall()
        return rows

    @staticmethod
    def _sanitize_float(value):
        return sanitize_float(value)

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

        if as_on_date is None:
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = (
            f"{(ref_date - pd.Timedelta(days=self.lookback_days + 200)):%Y-%m-%d}"
        )

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < max(60, int(self.lookback_days * 0.6) + 5):
                continue

            col_count = len(tech[0]) if tech else 0
            if col_count >= 12:
                df = pd.DataFrame(
                    tech,
                    columns=[
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "delivery",
                        "delivery_pct",
                        "nifty_outperformance_score",
                        "sma_50",
                        "high_52w",
                        "low_52w",
                    ],
                )
            else:
                df = pd.DataFrame(
                    tech,
                    columns=[
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "delivery",
                        "delivery_pct",
                        "nifty_outperformance_score",
                    ],
                )
                df["sma_50"] = None
                df["high_52w"] = None
                df["low_52w"] = None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            df["sma_200"] = df["close"].rolling(200, min_periods=1).mean()
            # SMA-200: require 200+ trading days, else set None (insufficient data)
            sma_200_val = float(df["sma_200"].iloc[-1]) if len(df) >= 200 else None

            if len(df) < max(60, int(self.lookback_days * 0.6) + 5):
                continue

            # Churn baseline (prior_window days before recent_window)
            if self.prior_window > 0 and self.recent_window > 0:
                prior_df = df.iloc[
                    -(self.prior_window + self.recent_window) : -self.recent_window
                ]
            else:
                prior_df = df.iloc[-self.lookback_days : -5]
            avg_vol_prior = float(np.nanmean(prior_df["volume"].values.astype(float)))
            avg_del_prior = float(
                np.nanmean(prior_df["delivery_pct"].values.astype(float))
            )

            # Recent window (last recent_window sessions)
            recent = df.tail(self.recent_window)
            recent_del_pcts = recent["delivery_pct"].values.astype(float)
            avg_del_recent = float(np.nanmean(recent_del_pcts))
            del50_days = int(np.sum(recent_del_pcts > 50))
            recent_vol = float(np.nanmean(recent["volume"].values.astype(float)))

            # Flip detection
            del_jump_pp = avg_del_recent - avg_del_prior

            # Dynamic thresholds based on recent window size
            if self.recent_window <= 10:
                strong_flip_del = 60.0
                moderate_flip_del = 55.0
            else:
                strong_flip_del = 55.0
                moderate_flip_del = 50.0

            # Flip grade determination
            if avg_del_recent >= strong_flip_del and del_jump_pp >= 20:
                flip_type = "STRONG FLIP"
            elif avg_del_recent >= moderate_flip_del and del_jump_pp >= 12:
                flip_type = "MODERATE FLIP"
            elif del_jump_pp >= 8:
                flip_type = "EARLY FLIP"
            else:
                continue

            # Delivery Value filter
            del_values = recent["delivery"] * recent["close"] / 1e7
            avg_del_value_cr = float(np.nanmean(del_values))
            if avg_del_value_cr < 15.0:
                continue

            # Price check
            closes = df["close"].values.astype(float)
            latest_close = float(closes[-1])
            high_52w = (
                float(df["high_52w"].iloc[-1])
                if pd.notna(df["high_52w"].iloc[-1])
                else float(df["high"].max())
            )
            low_52w = (
                float(df["low_52w"].iloc[-1])
                if pd.notna(df["low_52w"].iloc[-1])
                else float(df["low"].min())
            )
            wk52_pos = (
                ((latest_close - low_52w) / (high_52w - low_52w)) * 100
                if (high_52w - low_52w) > 0
                else 50.0
            )
            wk52_penalty = 1.0
            if wk52_pos >= 98:
                wk52_penalty = 0.5
            elif wk52_pos >= 95:
                wk52_penalty = 0.7
            elif wk52_pos >= 85:
                wk52_penalty = 0.9

            # SMA-200 factor
            sma_200_factor = 1.0
            if sma_200_val is not None and latest_close < sma_200_val:
                sma_200_factor = 0.7
            elif sma_200_val is not None and len(df) >= 250:
                sma_200_recent = float(df["sma_200"].iloc[-50:].mean())
                sma_200_older = float(df["sma_200"].iloc[-100:-50].mean())
                if sma_200_recent >= sma_200_older:
                    sma_200_factor = 1.1

            # Flip consistency: % of recent window days with delivery > 50%
            flip_consistency = int(
                np.sum(recent["delivery_pct"] > 50) / self.recent_window * 100
            )

            # Volume rank vs universe
            prior_vol_rank = avg_vol_prior / (
                np.nanmean(prior_df["volume"].values.astype(float)) or 1
            )

            # Flip score
            flip_score = (
                (
                    (avg_del_recent * 0.30)
                    + (min(avg_del_value_cr / 50.0 * 25.0, 25.0))
                    + (del_jump_pp * 0.20)
                    + (flip_consistency * 0.15)
                    + ((100 - wk52_pos) * 0.10)
                )
                * wk52_penalty
                * sma_200_factor
            )

            flip_score = max(0, min(flip_score, 100))

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

            # Confidence indicator
            if (
                flip_type in ("STRONG FLIP", "MODERATE FLIP")
                and sma_200_factor >= 1.0
                and 30 <= wk52_pos <= 90
            ):
                confidence = "High"
            elif (
                flip_type in ("STRONG FLIP", "MODERATE FLIP", "EARLY FLIP")
                and sma_200_factor >= 0.7
                and wk52_pos <= 95
            ):
                confidence = "Moderate"
            else:
                confidence = "Low"

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap_cr, 1),
                    "confidence": confidence,
                    "prior_del_pct": round(avg_del_prior, 1),
                    "current_del_pct": round(avg_del_recent, 1),
                    "del_jump_pp": round(del_jump_pp, 1),
                    "del50_days": del50_days,
                    "flip_type": flip_type,
                    "avg_del_value_cr": round(avg_del_value_cr, 2),
                    "flip_consistency": flip_consistency,
                    "sma_200": round(sma_200_val, 2)
                    if sma_200_val is not None
                    else None,
                    "sma_200_factor": round(sma_200_factor, 2),
                    "flip_score": round(flip_score, 1),
                    "grade": grade,
                    "prior_vol_rank": round(prior_vol_rank, 2),
                    "close": round(latest_close, 2),
                    "wk52_pos": round(wk52_pos, 1),
                }
            )

        float_fields = [
            "market_cap_cr",
            "prior_del_pct",
            "current_del_pct",
            "del_jump_pp",
            "flip_score",
            "prior_vol_rank",
            "close",
            "wk52_pos",
            "avg_del_value_cr",
            "flip_consistency",
            "sma_200",
            "sma_200_factor",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["flip_score"], reverse=True)
        logger.info(
            "Liquidity Flip scan complete: %d candidates found", len(candidates)
        )
        return pd.DataFrame(candidates)
