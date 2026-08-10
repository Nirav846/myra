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
    COLUMNS_12,
)

logger = logging.getLogger(__name__)


class OperatorFingerprintScanner:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    def __init__(self, min_mcap=200, max_mcap=50000, lookback_days=45):
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

        if as_on_date is None:
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = f"{(ref_date - pd.Timedelta(days=self.lookback_days + 30)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < max(35, int(self.lookback_days * 0.6) + 5):
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

            if len(df) < max(35, int(self.lookback_days * 0.6) + 5):
                continue

            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            volumes = df["volume"].values.astype(float)
            del_pcts = df["delivery_pct"].values.astype(float)
            latest_close = float(closes[-1])

            # ATR Compression: older window vs recent window
            lookback = self.lookback_days
            # Older window: sessions [-45:-31] (last 15 of the older half)
            older = df.iloc[-lookback:-30]
            # Recent window: sessions [-14:]
            newer = df.iloc[-14:]

            def _mean_daily_range(sub_df: pd.DataFrame) -> float:
                if sub_df.empty:
                    return 0.0
                h = sub_df["high"].values.astype(float)
                l = sub_df["low"].values.astype(float)
                ranges = (h - l) / l * 100
                return float(np.nanmean(ranges))

            atr_old_pct = _mean_daily_range(older)
            atr_new_pct = _mean_daily_range(newer)
            compression_ratio = atr_new_pct / atr_old_pct if atr_old_pct > 0 else 1.0

            # Delivery drift (linear slope over last 20 sessions)
            del_last20 = del_pcts[-20:]
            del_clean = del_last20[~np.isnan(del_last20)]
            if len(del_clean) >= 2:
                x = np.arange(len(del_clean))
                delivery_drift = float(np.polyfit(x, del_clean, 1)[0])
            else:
                delivery_drift = 0.0

            # Quiet accumulation days (last 20 sessions)
            last20_df = df.tail(20)
            quiet_accum_days = 0
            avg_del_session = float(np.nanmean(del_pcts))
            for _, row in last20_df.iterrows():
                del_pct = (
                    float(row["delivery_pct"]) if pd.notna(row["delivery_pct"]) else 0
                )
                prev_close = (
                    float(df[df["date"] < row["date"]]["close"].iloc[-1])
                    if len(df[df["date"] < row["date"]]) > 0
                    else float(row["close"])
                )
                price_change_pct = (
                    abs(float(row["close"]) - prev_close) / prev_close * 100
                    if prev_close > 0
                    else 0
                )
                if del_pct > avg_del_session and price_change_pct < 1.5:
                    quiet_accum_days += 1

            # Volume staircase: 3 blocks of 5 sessions each
            vol_block_1 = float(np.nanmean(volumes[-5:])) if len(volumes) >= 5 else 0
            vol_block_2 = (
                float(np.nanmean(volumes[-10:-5])) if len(volumes) >= 10 else 0
            )
            vol_block_3 = (
                float(np.nanmean(volumes[-15:-10])) if len(volumes) >= 15 else 0
            )
            volume_staircase = (
                vol_block_1 > vol_block_2 > vol_block_3 and vol_block_3 > 0
            )

            # Base duration: count sessions where ATR < atr_old_pct
            base_duration_days = 0
            if atr_old_pct > 0:
                for i in range(len(df)):
                    sub = df.iloc[max(0, i - 14) : i + 1]
                    if len(sub) >= 5:
                        sub_range = _mean_daily_range(sub)
                        if sub_range < atr_old_pct:
                            base_duration_days += 1

            # Coil Tension Score (0-100)
            compression_component = max(0, (1 - compression_ratio)) * 40
            drift_component = max(0, delivery_drift) * 20
            quiet_component = quiet_accum_days * 2
            staircase_bonus = 8 if volume_staircase else 0
            coil_tension_score = min(
                100,
                compression_component
                + drift_component
                + quiet_component
                + staircase_bonus,
            )

            # Filters
            if compression_ratio >= 0.80:
                continue
            if delivery_drift <= 0:
                continue
            if coil_tension_score < 20:
                continue

            if coil_tension_score >= 75:
                grade = "A"
            elif coil_tension_score >= 55:
                grade = "B"
            elif coil_tension_score >= 35:
                grade = "C"
            else:
                grade = "D"

            mcap_cr = mcap / 1e7

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap_cr, 1),
                    "compression_ratio": round(compression_ratio, 3),
                    "delivery_drift": round(delivery_drift, 4),
                    "quiet_accum_days": quiet_accum_days,
                    "volume_staircase": volume_staircase,
                    "coil_tension_score": round(coil_tension_score, 1),
                    "grade": grade,
                    "close": round(latest_close, 2),
                    "atr_old_pct": round(atr_old_pct, 2),
                    "atr_new_pct": round(atr_new_pct, 2),
                    "base_duration_days": base_duration_days,
                }
            )

        float_fields = [
            "market_cap_cr",
            "compression_ratio",
            "delivery_drift",
            "coil_tension_score",
            "close",
            "atr_old_pct",
            "atr_new_pct",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["coil_tension_score"], reverse=True)
        logger.info(
            "Operator Fingerprint scan complete: %d candidates found", len(candidates)
        )
        return pd.DataFrame(candidates)
