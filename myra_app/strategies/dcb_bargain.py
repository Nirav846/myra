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


class DCBBargainScanner:
    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        dcb_window=120,
        min_discount_pct=5.0,
        max_discount_pct=60.0,
        min_del_abs=-2.0,
        min_adtv_cr=1.0,
        min_high_del_days=10,
        sanity_mult=5.0,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.dcb_window = dcb_window
        self.min_discount_pct = min_discount_pct
        self.max_discount_pct = max_discount_pct
        self.min_del_abs = min_del_abs
        self.min_adtv_cr = min_adtv_cr
        self.min_high_del_days = min_high_del_days
        self.sanity_mult = sanity_mult

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
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                if max_date:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ? AND date <= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date, max_date),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date),
                    ).fetchall()
            except sqlite3.OperationalError:
                if max_date:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ? AND date <= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date, max_date),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
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

    @staticmethod
    def _compute_dcb(closes: np.ndarray, delivery_pcts: np.ndarray, avg_del: float) -> float | None:
        """Delivery-weighted average close on days where delivery_pct > avg_del."""
        mask = delivery_pcts > avg_del
        if int(np.sum(mask)) == 0:
            return None
        weights = delivery_pcts[mask]
        return float(np.average(closes[mask], weights=weights))

    @staticmethod
    def _compute_del_abs(df: pd.DataFrame, window: int = 20) -> float:
        """20-day delivery absorption: avg delivery% on up days minus avg delivery% on down days."""
        sub = df.tail(window)
        closes = sub["close"].values.astype(float)
        prev = np.roll(closes, 1)
        prev[0] = closes[0]
        returns = (closes - prev) / prev * 100
        del_pcts = sub["delivery_pct"].values.astype(float)
        up_mask = returns >= 0
        down_mask = returns < 0
        up_avg = float(np.nanmean(del_pcts[up_mask])) if np.any(up_mask) else 0.0
        down_avg = float(np.nanmean(del_pcts[down_mask])) if np.any(down_mask) else 0.0
        return up_avg - down_avg

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
        total_calendar = int((self.dcb_window + 50) * 1.8) + 10
        min_date = f"{(ref_date - pd.Timedelta(days=total_calendar)):%Y-%m-%d}"

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()
            try:
                tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
                if len(tech) < max(60, int((self.dcb_window + 50) * 0.6) + 5):
                    continue

                col_count = len(tech[0]) if tech else 0
                if col_count >= 8:
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
                        ],
                    )
                else:
                    continue

                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)

                if len(df) < max(60, int((self.dcb_window + 50) * 0.6) + 5):
                    continue

                # DCB window: last dcb_window TRADING rows
                window_df = df.iloc[-self.dcb_window:]

                # Average delivery % in window
                avg_del = float(np.nanmean(window_df["delivery_pct"].values.astype(float)))

                # High-delivery days count
                mask = window_df["delivery_pct"].values.astype(float) > avg_del
                high_del_days = int(np.sum(mask))
                if high_del_days < self.min_high_del_days:
                    continue

                # Delivery Cost Basis
                dcb = self._compute_dcb(
                    window_df["close"].values.astype(float),
                    window_df["delivery_pct"].values.astype(float),
                    avg_del,
                )
                if dcb is None or dcb <= 0:
                    continue

                # Current close
                close = float(window_df["close"].values.astype(float)[-1])
                if close <= 0:
                    continue

                # Sanity check: DCB must be < sanity_mult * close
                if dcb > close * self.sanity_mult:
                    continue

                # Discount %
                discount_pct = (dcb - close) / dcb * 100
                if discount_pct < self.min_discount_pct or discount_pct > self.max_discount_pct:
                    continue

                # ADTV in ₹ Cr
                adtv_cr = float(
                    np.nanmean(
                        window_df["close"].values.astype(float)
                        * window_df["volume"].values.astype(float)
                    )
                ) / 1e7
                if adtv_cr < self.min_adtv_cr:
                    continue

                # Delivery absorption (last 20 rows of full df)
                del_abs = self._compute_del_abs(df)
                if del_abs < self.min_del_abs:
                    continue

                # Score and tier
                score = discount_pct * 0.6 + del_abs * 0.4
                tier = "HIGH" if score >= 20 else ("MOD" if score >= 10 else "LOW")

                # Free float market cap
                free_float_mcap_cr = (mcap * ff_pct / 100.0) / 1e7

                candidates.append(
                    {
                        "symbol": symbol,
                        "sector": _sector_map.get(symbol, "Unknown"),
                        "close": round(close, 2),
                        "dcb": round(dcb, 2),
                        "discount_pct": round(discount_pct, 2),
                        "del_abs": round(del_abs, 2),
                        "adtv_cr": round(adtv_cr, 2),
                        "high_del_days": high_del_days,
                        "free_float_mcap_cr": round(free_float_mcap_cr, 2),
                        "score": round(score, 2),
                        "tier": tier,
                    }
                )
            except Exception:
                continue

        # Sanitize float fields
        float_fields = [
            "close",
            "dcb",
            "discount_pct",
            "del_abs",
            "adtv_cr",
            "free_float_mcap_cr",
            "score",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["score"], reverse=True)
        logger.info(
            "DCB Bargain scan complete: %d candidates found", len(candidates)
        )
        return pd.DataFrame(candidates)
