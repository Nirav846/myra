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


class FloatExhaustionScanner:
    def __init__(
        self, min_mcap=200, max_mcap=50000, window_days=20, min_float_util_pct=10.0
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.window_days = window_days
        self.min_float_util_pct = min_float_util_pct

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
                       COALESCE(f.free_float_pct, 40.0) AS ff_pct,
                       COALESCE(f.promoter_holding_pct, 50.0) AS promoter_pct,
                       f.promoter_holding_pct AS raw_promoter_pct,
                       f.free_float_pct AS raw_ff_pct
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

    def scan(self, as_on_date: str | None = None) -> list[dict]:
        rows = self._get_universe()
        if not rows:
            logger.warning(
                "No symbols found in universe (mcap %.0f-%.0f Cr)",
                self.min_mcap,
                self.max_mcap,
            )
            return []

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
        min_date = (ref_date - pd.Timedelta(days=self.window_days + 10)).strftime(
            "%Y-%m-%d"
        )

        candidates: list[dict] = []

        for idx, (
            symbol,
            mcap,
            ff_pct,
            promoter_pct,
            raw_promoter_pct,
            raw_ff_pct,
        ) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date)
            if len(tech) < self.window_days:
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

            if len(df) < self.window_days:
                continue

            latest_close = float(df["close"].iloc[-1])
            if latest_close <= 0:
                continue

            # Skip symbols where we lack both free-float and promoter data
            raw_ff = raw_ff_pct
            raw_prom = raw_promoter_pct
            if (raw_ff is None or raw_ff <= 0) and (raw_prom is None or raw_prom <= 0):
                continue

            # Shares calculation
            shares_total_approx = mcap / latest_close
            available_float_pct = ff_pct if ff_pct > 0 else (100 - promoter_pct - 15)
            free_float_shares = shares_total_approx * available_float_pct / 100

            if free_float_shares <= 0:
                continue

            # Query delivery data for the window
            recent = df.tail(self.window_days)
            deliveries = recent["delivery"].values.astype(float)
            closes = recent["close"].values.astype(float)
            opens = recent["open"].values.astype(float)

            cum_delivery = float(np.nansum(deliveries))
            up_day_delivery = float(np.nansum(deliveries[closes > opens]))

            # Float utilisation
            float_util_pct = cum_delivery / free_float_shares * 100
            smart_float_ratio = up_day_delivery / free_float_shares * 100

            # Absorption rate: recent vs overall pace
            recent5_del = (
                float(np.nanmean(deliveries[-5:])) if len(deliveries) >= 5 else 0
            )
            overall_del = float(np.nanmean(deliveries))
            absorption_rate = recent5_del / overall_del if overall_del > 0 else 1.0

            if float_util_pct < self.min_float_util_pct:
                continue

            # Exhaustion tier
            if float_util_pct >= 40:
                exhaustion_tier = "T3 CRITICAL"
            elif float_util_pct >= 25:
                exhaustion_tier = "T2 HIGH"
            elif float_util_pct >= 15:
                exhaustion_tier = "T1 ELEVATED"
            else:
                exhaustion_tier = "WATCH"

            # 52-week position
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

            mcap_cr = mcap / 1e7

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap_cr, 1),
                    "free_float_shares": int(free_float_shares),
                    "cum_delivery_20d": int(cum_delivery),
                    "float_util_pct": round(float_util_pct, 1),
                    "smart_float_ratio": round(smart_float_ratio, 1),
                    "absorption_rate": round(absorption_rate, 3),
                    "exhaustion_tier": exhaustion_tier,
                    "close": round(latest_close, 2),
                    "wk52_pos": round(wk52_pos, 1),
                }
            )

        float_fields = [
            "market_cap_cr",
            "float_util_pct",
            "smart_float_ratio",
            "absorption_rate",
            "close",
            "wk52_pos",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["float_util_pct"], reverse=True)
        logger.info(
            "Float Exhaustion scan complete: %d candidates found", len(candidates)
        )
        return candidates
