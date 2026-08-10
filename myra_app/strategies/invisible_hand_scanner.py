import logging
import math
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from typing import Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.strategies.bottom_hunter import compute_sector_momentum_tiers
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_12,
)

logger = logging.getLogger(__name__)


class InvisibleHandScanner:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        window=20,
        hist_window=60,
        min_ih_score=35,
        target_date: Optional[str] = None,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.window = window
        self.hist_window = hist_window
        self.min_ih_score = min_ih_score
        self.target_date = target_date

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
        self, symbol: str, min_date: str, max_date: Optional[str] = None
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
                               delivery_pct, nifty_outperformance_score,
                               sma_50, high_52w, low_52w
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
                               delivery_pct, nifty_outperformance_score,
                               sma_50, high_52w, low_52w
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
                               delivery_pct, nifty_outperformance_score,
                               NULL AS sma_50, NULL AS high_52w, NULL AS low_52w
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
        closes = df["close"].values.astype(float)
        delivery_value_cr = float(np.nansum(delivery_vals * closes)) / 1e7
        price_drift_abs = (
            abs(closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0.5
        )
        return delivery_value_cr / max(price_drift_abs, 0.5)

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

        # Compute sector momentum tiers
        try:
            _sector_mom_tier: dict[str, str] = compute_sector_momentum_tiers()
        except Exception:
            _sector_mom_tier = {}

        # Load quality-factor map (net_margin, pe, promoter_holding_pct)
        _quality_map: dict[str, dict] = {}
        try:
            val_db_q = self._db_path("valuation")
            with sqlite3.connect(val_db_q) as _qc:
                _q_rows = _qc.execute(
                    """
                    SELECT f.symbol, f.net_margin, f.pe, f.promoter_holding_pct
                    FROM fundamentals f
                    INNER JOIN (
                        SELECT symbol, MAX(date) as max_date
                        FROM fundamentals
                        GROUP BY symbol
                    ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                    """
                ).fetchall()
                for r in _q_rows:
                    _quality_map[r[0].strip()] = {
                        "net_margin": r[1],
                        "pe": r[2],
                        "promoter_holding_pct": r[3],
                    }
        except Exception:
            pass

        if as_on_date is None:
            as_on_date = self.target_date or date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        lookback_calendar_days = int((self.window + self.hist_window) * 1.8) + 10
        min_date = f"{(ref_date - pd.Timedelta(days=lookback_calendar_days)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=self.target_date)
            if len(tech) < self.window + self.hist_window + 10:
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

            if len(df) < self.window + self.hist_window + 10:
                continue

            # Split into historical and current windows
            hist_df = df.iloc[: -self.window]
            curr_df = df.iloc[-self.window :]

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

            opens = curr_df["open"].values.astype(float)
            deliveries = curr_df["delivery"].values.astype(float)

            # Delivery Spoofing Gate (hard rejection)
            del_vol_5d = float(np.nanmean(deliveries[-5:]))
            del_vol_20d = float(np.nanmean(deliveries[-20:]))
            del_pct_5d_avg = float(np.nanmean(del_pcts[-5:]))
            del_pct_20d_avg = float(np.nanmean(del_pcts[-20:]))
            if del_vol_5d < del_vol_20d * 0.8 and del_pct_5d_avg > del_pct_20d_avg:
                continue

            # Enhancement 1: Delivery Momentum (20 points)
            del_pct_5d = float(curr_df["delivery_pct"].iloc[-5:].mean())
            del_pct_20d = float(curr_df["delivery_pct"].iloc[-20:].mean())
            price_ret_5d = (
                (float(closes[-1]) / float(closes[-6]) - 1)
                if len(curr_df) >= 6
                else 0.0
            )
            del_momentum_score = 0.0
            if del_pct_20d > 0 and del_pct_5d > del_pct_20d:
                ratio = min(del_pct_5d / del_pct_20d, 2.0)
                del_momentum_score = (ratio - 1.0) * 20.0
                if price_ret_5d > 0:
                    del_momentum_score *= 0.5

            # Enhancement 2: Delivery Value Efficiency (25 points)
            del_values = deliveries * closes
            total_del_value = float(np.nansum(del_values))
            price_change_pct = (
                abs(float(closes[-1]) / float(closes[0]) - 1) * 100
                if closes[0] > 0
                else 1.0
            )
            efficiency = total_del_value / 1e7 / max(price_change_pct, 0.1)
            del_efficiency_score = min(efficiency / 50.0 * 25.0, 25.0)

            # Enhancement 3: Delivery Consistency (15 points)
            consistency_score = 0.0
            if mean_del > 40 and mean_del > 0:
                cv = std_del / mean_del
                consistency_score = max(0, (1.0 - cv) * 15.0)

            # Enhancement 4: Down-Day Delivery Bonus (5 points max)
            down_day_del_count = 0
            for i in range(-min(10, len(curr_df)), 0):
                if closes[i] < opens[i] and del_pcts[i] > mean_del:
                    down_day_del_count += 1
            down_day_bonus = min(down_day_del_count, 5) * 1.0

            # Composite IH Score
            ih_composite = (
                der_score * 0.20
                + ddas_score * 0.20
                + dcs_score * 0.10
                + qcd_score * 0.10
                + del_momentum_score * 0.20
                + del_efficiency_score * 0.25
                + consistency_score * 0.15
                + down_day_bonus
            )
            ih_score = min(ih_composite, 100.0)

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
            high_52w = (
                float(curr_df["high_52w"].iloc[-1])
                if pd.notna(curr_df["high_52w"].iloc[-1])
                else float(curr_df["high"].max())
            )
            low_52w = (
                float(curr_df["low_52w"].iloc[-1])
                if pd.notna(curr_df["low_52w"].iloc[-1])
                else float(curr_df["low"].min())
            )
            wk52_pos = (
                (latest_close - low_52w) / (high_52w - low_52w) * 100
                if (high_52w - low_52w) > 0
                else 50.0
            )

            # Base duration: consecutive sessions where daily range < 3% of close
            base_duration = 0
            for i in range(len(curr_df) - 1, -1, -1):
                row = curr_df.iloc[i]
                daily_range_pct = (
                    (float(row["high"]) - float(row["low"])) / float(row["close"]) * 100
                    if float(row["close"]) > 0
                    else 99
                )
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

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "sector_mom_tier": _sector_mom_tier.get(
                        _sector_map.get(symbol, ""), "Unknown"
                    ),
                    "quality_score": None,
                    "market_cap_cr": round(mcap / 1e7, 1),
                    "der_ratio": round(der_ratio, 2),
                    "der_score": round(der_score, 1),
                    "ddas": round(ddas, 1),
                    "ddas_score": round(ddas_score, 1),
                    "mean_del_pct": round(mean_del, 1),
                    "dcs_score": round(dcs_score, 1),
                    "qcd": qcd,
                    "qcd_score": round(qcd_score, 1),
                    "del_momentum_score": round(del_momentum_score, 1),
                    "del_efficiency_score": round(del_efficiency_score, 1),
                    "consistency_score": round(consistency_score, 1),
                    "down_day_bonus": round(down_day_bonus, 1),
                    "ih_score": round(ih_score, 1),
                    "grade": grade,
                    "down_day_count": down_day_count,
                    "base_duration": base_duration,
                    "close": round(latest_close, 2),
                    "wk52_pos": round(wk52_pos, 1),
                }
            )

        float_fields = [
            "der_ratio",
            "der_score",
            "ddas",
            "ddas_score",
            "mean_del_pct",
            "dcs_score",
            "qcd_score",
            "del_momentum_score",
            "del_efficiency_score",
            "consistency_score",
            "down_day_bonus",
            "ih_score",
            "close",
            "wk52_pos",
            "market_cap_cr",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        # Compute cross-sectional quality score (0-100)
        # Formula: 0.4 * pct_rank(net_margin) + 0.3 * pct_rank(promoter) + 0.3 * pct_rank(1/pe)
        if len(candidates) > 0:
            cand_df = pd.DataFrame(candidates)
            _nm = []
            _ph = []
            _inv_pe = []
            for sym in cand_df["symbol"]:
                qf = _quality_map.get(sym, {})
                nm = qf.get("net_margin")
                ph = qf.get("promoter_holding_pct")
                pe = qf.get("pe")
                _nm.append(
                    nm
                    if nm is not None and not (isinstance(nm, float) and math.isnan(nm))
                    else None
                )
                _ph.append(
                    ph
                    if ph is not None and not (isinstance(ph, float) and math.isnan(ph))
                    else None
                )
                if (
                    pe is not None
                    and pe > 0
                    and not (isinstance(pe, float) and math.isnan(pe))
                ):
                    _inv_pe.append(1.0 / pe)
                else:
                    _inv_pe.append(None)

            cand_df["_nm"] = _nm
            cand_df["_ph"] = _ph
            cand_df["_inv_pe"] = _inv_pe

            nm_rank = (
                cand_df["_nm"]
                .apply(lambda x: x if x is not None else np.nan)
                .rank(pct=True, ascending=True)
            )
            ph_rank = (
                cand_df["_ph"]
                .apply(lambda x: x if x is not None else np.nan)
                .rank(pct=True, ascending=True)
            )
            pe_rank = (
                cand_df["_inv_pe"]
                .apply(lambda x: x if x is not None else np.nan)
                .rank(pct=True, ascending=True)
            )

            nm_valid = cand_df["_nm"].notna()
            ph_valid = cand_df["_ph"].notna()
            pe_valid = cand_df["_inv_pe"].notna()

            # Default weights: 0.4, 0.3, 0.3
            w_nm, w_ph, w_pe = 0.4, 0.3, 0.3

            qscores = []
            for i in range(len(cand_df)):
                has_nm = nm_valid.iloc[i]
                has_ph = ph_valid.iloc[i]
                has_pe = pe_valid.iloc[i]
                active_w = 0.0
                if has_nm:
                    active_w += w_nm
                if has_ph:
                    active_w += w_ph
                if has_pe:
                    active_w += w_pe
                if active_w == 0:
                    qscores.append(None)
                    continue
                qscore = 0.0
                if has_nm:
                    qscore += (w_nm / active_w) * nm_rank.iloc[i] * 100
                if has_ph:
                    qscore += (w_ph / active_w) * ph_rank.iloc[i] * 100
                if has_pe:
                    qscore += (w_pe / active_w) * pe_rank.iloc[i] * 100
                qscores.append(round(qscore, 1))

            for idx_c, qs in enumerate(qscores):
                candidates[idx_c]["quality_score"] = self._sanitize_float(qs)

        candidates.sort(key=lambda x: x["ih_score"], reverse=True)
        logger.info(
            "Invisible Hand scan complete: %d candidates found", len(candidates)
        )
        return pd.DataFrame(candidates)
