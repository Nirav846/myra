import logging
import math
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
    COLUMNS_8,
)

logger = logging.getLogger(__name__)


class DCBBargainScanner:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_8

    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        dcb_window=120,
        min_discount_pct=15.0,
        max_discount_pct=60.0,
        min_del_abs=-2.0,
        min_adtv_cr=1.0,
        min_high_del_days=10,
        sanity_mult=5.0,
        timeframe="daily",
        min_ff_mcap=0.0,
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
        self.timeframe = timeframe
        self.min_ff_mcap = min_ff_mcap

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
        if self._bulk_data is not None:
            return rows_for_symbol(
                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
            )
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
    def _compute_dcb(
        closes: np.ndarray, delivery_pcts: np.ndarray, avg_del: float
    ) -> float | None:
        """Delivery-weighted average close on days where delivery_pct > avg_del."""
        mask = delivery_pcts > avg_del
        if int(np.sum(mask)) == 0:
            return None
        weights = delivery_pcts[mask]
        return float(np.average(closes[mask], weights=weights))

    @staticmethod
    def _compute_del_abs(df: pd.DataFrame, window: int = 20) -> float:
        """20-day delivery absorption: avg delivery% on up days minus avg delivery% on down days.
        An 'up day' is close > open; a 'down day' is close < open. Flat days excluded.
        """
        sub = df.tail(window)
        if len(sub) == 0:
            return 0.0
        opens = sub["open"].values.astype(float)
        closes = sub["close"].values.astype(float)
        del_pcts = sub["delivery_pct"].values.astype(float)

        up_mask = closes > opens
        down_mask = closes < opens

        up_avg = float(np.nanmean(del_pcts[up_mask])) if np.any(up_mask) else 0.0
        down_avg = float(np.nanmean(del_pcts[down_mask])) if np.any(down_mask) else 0.0
        return up_avg - down_avg

    @staticmethod
    def _get_weekly_data(df_daily: pd.DataFrame) -> pd.DataFrame:
        """Aggregate daily OHLCV+delivery to weekly candles."""
        if df_daily is None or len(df_daily) < 5:
            return pd.DataFrame()
        df = df_daily.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        weekly = df.resample("W").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "delivery": "sum",
            }
        )
        weekly["delivery_pct"] = (
            weekly["delivery"] / weekly["volume"].replace(0, float("nan")) * 100
        ).fillna(0)
        weekly = weekly.dropna(subset=["open", "close"])
        return weekly.reset_index()

    @staticmethod
    def _compute_depth_tag(discount_pct: float) -> str:
        """Return DEEP / MID / SHALLOW based on discount percentage."""
        if discount_pct > 20:
            return "DEEP"
        elif discount_pct > 10:
            return "MID"
        return "SHALLOW"

    def _is_lower_circuit(self, df: pd.DataFrame, idx: int) -> bool:
        """Check if the candle at idx was a lower-circuit day."""
        if idx < 1:
            return False
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]
        close = float(row["close"])
        low = float(row["low"])
        prev_close = float(prev["close"])
        # Close pinned at the low (within 1%) AND dropped 5%+ from previous close
        is_pinned = close <= low * 1.01
        is_significant_drop = close < prev_close * 0.95  # 5% drop
        return is_pinned and is_significant_drop

    @staticmethod
    def _check_spike_deep(df_daily: pd.DataFrame, discount_pct: float) -> bool:
        """Return True if today's delivery_pct >= 1.3x 50-day avg AND
        close_loc >= 0.6 AND discount_pct > 20. Uses daily frame."""
        if len(df_daily) < 20:
            return False
        del_avg = df_daily["delivery_pct"].tail(50).mean()
        if pd.isna(del_avg) or del_avg <= 0:
            return False
        last = df_daily.iloc[-1]
        if last["delivery_pct"] < 1.3 * del_avg:
            return False
        high, low, close = float(last["high"]), float(last["low"]), float(last["close"])
        if high == low:
            clr = 1.0 if close == high else 0.0
        else:
            clr = (close - low) / (high - low)
        return clr >= 0.6 and discount_pct > 20

    def _compute_depth_history(
        self, df_daily: pd.DataFrame
    ) -> tuple[float | None, float | None, float | None]:
        """Compute 1-year DCB discount range (min, median, max) from historical cutoffs.
        Uses the daily frame with strided windows for efficiency."""
        if len(df_daily) < self.dcb_window + 20:
            return None, None, None

        closes_all = df_daily["close"].values.astype(float)
        del_all = df_daily["delivery_pct"].values.astype(float)

        # Collect historical cutoffs: every 10th trading row, at least one per month
        n = len(df_daily)
        # Start from dcb_window (need at least that many rows)
        # Generate candidate cutoff indices
        step = max(1, n // 20)  # ~20 cutoffs evenly spaced
        cutoff_indices = list(range(self.dcb_window, n, step))
        # Also add one per month (roughly every 21 trading days)
        month_step = 21
        for i in range(self.dcb_window, n, month_step):
            if i not in cutoff_indices:
                cutoff_indices.append(i)
        cutoff_indices = sorted(set(cutoff_indices))

        discount_pcts = []
        for cutoff_idx in cutoff_indices:
            window_closes = closes_all[cutoff_idx - self.dcb_window : cutoff_idx]
            window_del = del_all[cutoff_idx - self.dcb_window : cutoff_idx]
            close_at_cutoff = closes_all[cutoff_idx]

            if close_at_cutoff <= 0 or len(window_closes) < self.dcb_window:
                continue

            avg_del = float(np.nanmean(window_del))
            mask = window_del > avg_del
            if int(np.sum(mask)) == 0:
                continue

            dcb = float(np.average(window_closes[mask], weights=window_del[mask]))
            if dcb <= 0:
                continue

            disc = (dcb - close_at_cutoff) / dcb * 100
            if -50 < disc < 100:  # sanity bounds
                discount_pcts.append(disc)

        if len(discount_pcts) < 3:
            return None, None, None

        arr = np.array(discount_pcts)
        return (
            self._sanitize_float(float(np.min(arr))),
            self._sanitize_float(float(np.median(arr))),
            self._sanitize_float(float(np.max(arr))),
        )

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
        # Fetch enough data: ~1 year + buffer for depth history
        total_calendar = int(max(self.dcb_window + 50, 365) * 1.8) + 20
        min_date = f"{(ref_date - pd.Timedelta(days=total_calendar)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        is_weekly = self.timeframe == "weekly"
        effective_window = self.dcb_window // 5 if is_weekly else self.dcb_window

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

                # Free-float filter
                free_float_mcap_cr = (mcap * ff_pct / 100.0) / 1e7
                if free_float_mcap_cr < self.min_ff_mcap:
                    continue

                # Weekly aggregation if needed
                if is_weekly:
                    work_df = self._get_weekly_data(df)
                    if len(work_df) < max(15, effective_window):
                        continue
                else:
                    work_df = df

                # DCB window: last effective_window TRADING rows
                window_df = work_df.iloc[-effective_window:]

                # Average delivery % in window
                avg_del = float(
                    np.nanmean(window_df["delivery_pct"].values.astype(float))
                )

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
                close = float(work_df["close"].values.astype(float)[-1])
                if close <= 0:
                    continue

                # Sanity check: DCB must be < sanity_mult * close
                if dcb > close * self.sanity_mult:
                    continue

                # Discount %
                discount_pct = (dcb - close) / dcb * 100
                if (
                    discount_pct < self.min_discount_pct
                    or discount_pct > self.max_discount_pct
                ):
                    continue

                # ADTV in ₹ Cr
                adtv_cr = (
                    float(
                        np.nanmean(
                            work_df["close"].values.astype(float)
                            * work_df["volume"].values.astype(float)
                        )
                    )
                    / 1e7
                )
                if adtv_cr < self.min_adtv_cr:
                    continue

                # Delivery absorption (last 20 rows of DAILY df — always daily)
                del_abs = self._compute_del_abs(df)
                if del_abs < self.min_del_abs:
                    continue

                # Depth tag
                depth = self._compute_depth_tag(discount_pct)

                # Spike+Deep (uses daily frame)
                spike_deep = self._check_spike_deep(df, discount_pct)

                # Score and tier
                score = discount_pct * 0.6 + del_abs * 0.4
                tier = "HIGH" if score >= 20 else ("MOD" if score >= 10 else "LOW")

                # Depth history (1-year DCB discount range)
                (
                    dcb_disc_min,
                    dcb_disc_median,
                    dcb_disc_max,
                ) = self._compute_depth_history(df)

                # Lower-circuit detection (uses daily df)
                is_lower_circuit = self._is_lower_circuit(df, len(df) - 1)
                circuit_days_last_5 = 0
                start_idx = max(0, len(df) - 5)
                for ci in range(start_idx, len(df)):
                    if self._is_lower_circuit(df, ci):
                        circuit_days_last_5 += 1

                candidates.append(
                    {
                        "symbol": symbol,
                        "sector": _sector_map.get(symbol, "Unknown"),
                        "close": round(close, 2),
                        "dcb": round(dcb, 2),
                        "discount_pct": round(discount_pct, 2),
                        "depth": depth,
                        "del_abs": round(del_abs, 2),
                        "adtv_cr": round(adtv_cr, 2),
                        "high_del_days": high_del_days,
                        "free_float_mcap_cr": round(free_float_mcap_cr, 2),
                        "spike_deep": spike_deep,
                        "is_lower_circuit": is_lower_circuit,
                        "circuit_days_last_5": circuit_days_last_5,
                        "dcb_disc_min": dcb_disc_min,
                        "dcb_disc_median": dcb_disc_median,
                        "dcb_disc_max": dcb_disc_max,
                        "score": round(score, 2),
                        "tier": tier,
                        "timeframe": self.timeframe,
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
            "dcb_disc_min",
            "dcb_disc_median",
            "dcb_disc_max",
            "circuit_days_last_5",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["score"], reverse=True)
        logger.info("DCB Bargain scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)
