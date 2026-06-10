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


class InvisibleHandScanner:
    def __init__(self, min_mcap=200, max_mcap=50000,
                 window=20, hist_window=60, min_ih_score=35):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.window = window           # recent window for all current metrics
        self.hist_window = hist_window # historical window for DER baseline
        self.min_ih_score = min_ih_score

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
                       COALESCE(f.market_cap, f.marketCap, 0) AS mcap
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

    @staticmethod
    def _compute_der(df: pd.DataFrame) -> float:
        """
        Delivery Efficiency Ratio = total delivery value (₹Cr) / max(|price drift %|, 0.5)
        High value = large capital absorbed with minimal price movement.
        """
        if len(df) < 2:
            return 0.0
        delivery_vals = df["delivery"].values.astype(float)
        closes        = df["close"].values.astype(float)
        delivery_value_cr = float(np.nansum(delivery_vals * closes)) / 1e7
        price_drift_abs   = abs(closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0.5
        return delivery_value_cr / max(price_drift_abs, 0.5)

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
        min_date = (ref_date - pd.Timedelta(days=self.window + self.hist_window + 10)).strftime("%Y-%m-%d")

        candidates: list[dict] = []

        for idx, (symbol, mcap) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date)
            if len(tech) < self.window + self.hist_window + 10:
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

            if len(df) < self.window + self.hist_window + 10:
                continue

            # Split into historical and current windows
            hist_df = df.iloc[:-self.window]
            curr_df = df.iloc[-self.window:]

            # Signal 1: Delivery Efficiency Ratio (DER)
            hist_der = self._compute_der(hist_df)
            curr_der = self._compute_der(curr_df)

            der_ratio = curr_der / hist_der if hist_der > 0.1 else 1.0
            der_score = min(100.0, max(0.0, (der_ratio - 1.0) / 2.0 * 100))

            # Signal 2: Down-Day Absorption Score (DDAS)
            closes = curr_df["close"].values.astype(float)
            prev_closes = np.roll(closes, 1)
            prev_closes[0] = closes[0]
            returns = (closes - prev_closes) / prev_closes * 100

            del_pcts = curr_df["delivery_pct"].values.astype(float)

            down_mask = returns < -0.2
            down_del_pcts = del_pcts[down_mask]

            if len(down_del_pcts) >= 4:
                ddas = float(np.nanmean(down_del_pcts))
            else:
                ddas = float(np.nanmean(del_pcts)) * 0.85

            ddas_score = min(100.0, max(0.0, ddas / 70.0 * 100))
            down_day_count = int(np.sum(down_mask))

            # Signal 3: Delivery Consistency Score (DCS)
            mean_del = float(np.nanmean(del_pcts))
            std_del = float(np.nanstd(del_pcts))

            dcs_raw = mean_del / (1.0 + std_del / 10.0)
            dcs_score = min(100.0, max(0.0, dcs_raw / 40.0 * 100))

            # Signal 4: Quiet Conviction Days (QCD)
            vols = curr_df["volume"].values.astype(float)
            avg_vol = float(np.nanmean(vols))

            qcd = 0
            for i in range(1, len(curr_df)):
                dp = del_pcts[i]
                ret = abs(returns[i])
                vol = vols[i]
                if dp > 50 and ret < 1.5 and 0.6 * avg_vol <= vol <= 1.4 * avg_vol:
                    qcd += 1

            qcd_score = min(100.0, max(0.0, qcd / 12.0 * 100))

            # Composite IH Score
            ih_score = (
                der_score * 0.35
                + ddas_score * 0.30
                + dcs_score * 0.20
                + qcd_score * 0.15
            )

            if ih_score >= 75:
                grade = "A"
            elif ih_score >= 55:
                grade = "B"
            elif ih_score >= 35:
                grade = "C"
            else:
                grade = "D"

            # 52-week position
            latest_close = float(closes[-1])
            high_52w = float(curr_df["high_52w"].iloc[-1]) if pd.notna(curr_df["high_52w"].iloc[-1]) \
                       else float(curr_df["high"].max())
            low_52w = float(curr_df["low_52w"].iloc[-1]) if pd.notna(curr_df["low_52w"].iloc[-1]) \
                     else float(curr_df["low"].min())
            wk52_pos = (latest_close - low_52w) / (high_52w - low_52w) * 100 \
                      if (high_52w - low_52w) > 0 else 50.0

            # Base duration: consecutive sessions where daily range < 3% of close
            base_duration = 0
            for i in range(len(curr_df) - 1, -1, -1):
                row = curr_df.iloc[i]
                daily_range_pct = (float(row["high"]) - float(row["low"])) / float(row["close"]) * 100 \
                                 if float(row["close"]) > 0 else 99
                if daily_range_pct < 3.0:
                    base_duration += 1
                else:
                    break

            # Filters before appending
            if der_ratio <= 1.2:
                continue
            if ddas <= 42:
                continue
            if mean_del <= 38:
                continue
            if ih_score < self.min_ih_score:
                continue
            if wk52_pos >= 88:
                continue

            candidates.append({
                "symbol": symbol,
                "sector": _sector_map.get(symbol, "Unknown"),
                "market_cap_cr": round(mcap / 1e7, 1),
                "der_ratio": round(der_ratio, 2),
                "der_score": round(der_score, 1),
                "ddas": round(ddas, 1),
                "ddas_score": round(ddas_score, 1),
                "mean_del_pct": round(mean_del, 1),
                "dcs_score": round(dcs_score, 1),
                "qcd": qcd,
                "qcd_score": round(qcd_score, 1),
                "ih_score": round(ih_score, 1),
                "grade": grade,
                "down_day_count": down_day_count,
                "base_duration": base_duration,
                "close": round(latest_close, 2),
                "wk52_pos": round(wk52_pos, 1),
            })

        float_fields = [
            "der_ratio", "der_score", "ddas", "ddas_score", "mean_del_pct",
            "dcs_score", "qcd_score", "ih_score", "close", "wk52_pos", "market_cap_cr",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["ih_score"], reverse=True)
        logger.info("Invisible Hand scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)