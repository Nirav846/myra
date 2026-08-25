import logging
import math
from myra_app.strategies.scanner_utils import sanitize_float
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from typing import Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_12,
)

logger = logging.getLogger(__name__)


def compute_sector_momentum_tiers() -> dict[str, str]:
    """Compute 6-month ROC per sector and tier into TOP / MID / BOTTOM.

    Returns a dict mapping sector name → tier string.
    Falls back to returning an empty dict if data is insufficient.
    """
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

    if not os.path.exists(tech_db) or not os.path.exists(val_db):
        return {}

    # Step 1: Build symbol → sector map from fundamentals
    symbol_sector: dict[str, str] = {}
    try:
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
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
            symbol_sector = {r[0].strip(): r[1] for r in rows}
    except Exception:
        return {}

    if not symbol_sector:
        return {}

    # Step 2: Batch load close data for all symbols at once (no N+1)
    sector_rocs: dict[str, list[float]] = {}
    symbols = list(symbol_sector.keys())

    try:
        with sqlite3.connect(tech_db) as conn:
            # Single query: get latest and ~126th-close for all symbols
            placeholders = ",".join("?" for _ in symbols)
            rows = conn.execute(
                f"""
                SELECT symbol, close, date FROM (
                    SELECT symbol, close, date,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                    FROM technical_data
                    WHERE symbol IN ({placeholders})
                ) WHERE rn IN (1, 126)
                """,
                symbols,
            ).fetchall()

        # Group by symbol: rn=1 is latest, rn=126 is ~6mo ago
        sym_latest: dict[str, float] = {}
        sym_old: dict[str, float] = {}
        for sym, close, _dt in rows:
            # We get two rows per symbol; the one with rn=1 has the latest date
            # Since we can't easily distinguish rn here, use a dict approach
            if sym not in sym_latest:
                sym_latest[sym] = float(close)
            else:
                sym_old[sym] = float(close)

        for sym in symbols:
            latest_close = sym_latest.get(sym, 0)
            old_close = sym_old.get(sym, 0)
            if old_close <= 0 or latest_close <= 0:
                continue
            roc = (latest_close - old_close) / old_close
            sector = symbol_sector[sym]
            sector_rocs.setdefault(sector, []).append(roc)  # noqa: PG-APPEND
    except Exception:
        return {}

    # Step 3: Equal-weight mean ROC per sector
    sector_avg_roc: dict[str, float] = {}
    for sec, rocs in sector_rocs.items():
        if rocs:
            sector_avg_roc[sec] = sum(rocs) / len(rocs)

    if len(sector_avg_roc) < 5:
        # Not enough sectors to tier meaningfully
        return {}

    # Step 4: Percentile-rank sectors
    sorted_sectors = sorted(sector_avg_roc.items(), key=lambda x: x[1], reverse=True)
    n = len(sorted_sectors)
    top_cutoff = max(1, int(n * 0.2))
    bottom_cutoff = max(1, int(n * 0.2))
    bottom_start = n - bottom_cutoff

    result: dict[str, str] = {}
    for i, (sec, _) in enumerate(sorted_sectors):
        if i < top_cutoff:
            result[sec] = "TOP"
        elif i >= bottom_start:
            result[sec] = "BOTTOM"
        else:
            result[sec] = "MID"

    return result


class BottomHunter:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        min_delivery_absorption=5.0,
        adtv_min_cr=1.0,
        lookback_days=260,
        timeframe: str = "daily",
        restrict_to_holdings=False,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_delivery_absorption = min_delivery_absorption
        self.adtv_min_cr = adtv_min_cr
        self.lookback_days = lookback_days
        self.timeframe = timeframe if timeframe in ("daily", "weekly") else "daily"
        self.restrict_to_holdings = bool(restrict_to_holdings)

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

        if self.restrict_to_holdings:
            from myra_app.utils.fund_utils import get_holding_symbols

            holdings = get_holding_symbols()
            if not holdings:
                logger.warning(
                    "restrict_to_holdings=True but no holding data — "
                    "falling back to full universe (%d symbols)",
                    len(rows),
                )
            else:
                before = len(rows)
                rows = [r for r in rows if r[0].strip() in holdings]
                logger.info(
                    "Holdings filter: %d → %d symbols (latest month)",
                    before,
                    len(rows),
                )

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

    def _get_weekly_data(self, df_daily: pd.DataFrame) -> pd.DataFrame:
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
    def _check_delivery_spike(df: pd.DataFrame) -> bool:
        """Return True if today's delivery % is ≥1.3× the 50-day avg AND
        close is in the upper 40% of the day's range (CLR ≥ 0.6)."""
        if len(df) < 20:
            return False
        del_avg = df["delivery_pct"].tail(50).mean()
        if pd.isna(del_avg) or del_avg <= 0:
            return False
        last = df.iloc[-1]
        # Condition A: delivery spike
        if last["delivery_pct"] < 1.3 * del_avg:
            return False
        # Condition B: close location ratio ≥ 0.6
        high, low, close = float(last["high"]), float(last["low"]), float(last["close"])
        if high == low:
            clr = 1.0 if close == high else 0.0
        else:
            clr = (close - low) / (high - low)
        return clr >= 0.6

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
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = f"{(ref_date - pd.Timedelta(days=self.lookback_days + 30)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < max(30, int(self.lookback_days * 0.6) + 5):
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

            if len(df) < max(30, int(self.lookback_days * 0.6) + 5):
                continue

            # Weekly aggregation
            if self.timeframe == "weekly":
                df_for_scoring = self._get_weekly_data(df)
                if df_for_scoring.empty or len(df_for_scoring) < 8:
                    continue
            else:
                df_for_scoring = df

            # Get last N periods for calculations
            window_n = 8 if self.timeframe == "weekly" else 20
            last_n = df_for_scoring.tail(window_n)
            if len(last_n) < window_n:
                continue

            # Separate up and down days (close > open = up, close < open = down)
            up_days = last_n[last_n["close"] > last_n["open"]]
            down_days = last_n[last_n["close"] < last_n["open"]]

            # Calculate delivery absorption
            up_del_avg = up_days["delivery_pct"].mean() if len(up_days) > 0 else 0
            down_del_avg = down_days["delivery_pct"].mean() if len(down_days) > 0 else 0
            delivery_absorption = up_del_avg - down_del_avg

            # Calculate ADTV (average daily turnover in Cr) over last N periods
            adtv_cr = ((last_n["close"] * last_n["volume"]) / 1e7).mean()

            # Apply filters
            if adtv_cr < self.adtv_min_cr:
                continue
            if delivery_absorption < self.min_delivery_absorption:
                continue

            # Calculate % above 52w low
            latest_close = float(last_n["close"].iloc[-1])
            if "high_52w" in last_n.columns and pd.notna(last_n["high_52w"].iloc[-1]):
                high_52w = float(last_n["high_52w"].iloc[-1])
            else:
                high_52w = float(df["high"].max())
            if "low_52w" in last_n.columns and pd.notna(last_n["low_52w"].iloc[-1]):
                low_52w = float(last_n["low_52w"].iloc[-1])
            else:
                low_52w = float(df["low"].min())
            pct_above_52w_low = (
                ((latest_close - low_52w) / low_52w) * 100 if low_52w > 0 else 0
            )

            # Entry signal based on recovery from 52-week low
            if pct_above_52w_low >= 10:
                entry_signal = "Above 10% of 52W Low"
            elif pct_above_52w_low >= 5:
                entry_signal = "Near 52W Low (5-10%)"
            else:
                entry_signal = "At 52W Low (<5%)"

            # Stop-loss: anchor to entry price, not historical lows
            # Compute ATR
            prev_close = last_n["close"].shift(1)
            tr = pd.concat(
                [
                    last_n["high"] - last_n["low"],
                    (last_n["high"] - prev_close).abs(),
                    (last_n["low"] - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr_n = float(tr.mean())
            swing_low_n = float(last_n["low"].min())

            # Base SL: 2x ATR below entry — always tight and volatility-adjusted
            sl_base = latest_close - 2 * atr_n
            sl_type = "Entry - 2×ATR"

            # If a relevant swing low exists between entry and 2xATR, use that instead
            if swing_low_n < latest_close and swing_low_n > sl_base:
                sl_price = swing_low_n - atr_n * 0.5
                sl_type = "Below Window Swing Low"
                sl_base = swing_low_n
            else:
                sl_price = sl_base

            spike_result = self._check_delivery_spike(df_for_scoring)

            candidates.append(  # noqa: PG-APPEND
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "sector_mom_tier": _sector_mom_tier.get(
                        _sector_map.get(symbol, ""), "Unknown"
                    ),
                    "quality_score": None,
                    "delivery_spike_conf": spike_result,
                    "timeframe": self.timeframe,
                    "close": latest_close,
                    "market_cap_cr": mcap / 1e7,
                    "delivery_absorption": delivery_absorption,
                    "pct_above_52w_low": pct_above_52w_low,
                    "adtv_cr": adtv_cr,
                    "entry_signal": entry_signal,
                    "sl_price": round(sl_price, 2),
                    "sl_type": sl_type,
                    "swing_low_n": round(swing_low_n, 2),
                }
            )

        # Now calculate percentile rank of delivery_absorption for the composite score
        if len(candidates) > 0:
            candidate_df = pd.DataFrame(candidates)
            candidate_df["score"] = (
                candidate_df["delivery_absorption"].rank(pct=True, ascending=True) * 100
            )
            # Assign tier: HIGH >=80, MOD >=50, LOW <50
            candidate_df["tier"] = pd.cut(
                candidate_df["score"],
                bins=[-1, 50, 80, 101],
                labels=["LOW", "MOD", "HIGH"],
                right=False,
            ).astype(str)

            # Compute cross-sectional quality score (0-100)
            # Formula: 0.4 * pct_rank(net_margin) + 0.3 * pct_rank(promoter) + 0.3 * pct_rank(1/pe)
            _nm = []
            _ph = []
            _inv_pe = []
            for sym in candidate_df["symbol"]:
                qf = _quality_map.get(sym, {})
                nm = qf.get("net_margin")
                ph = qf.get("promoter_holding_pct")
                pe = qf.get("pe")
                _nm.append(  # noqa: PG-APPEND
                    nm
                    if nm is not None and not (isinstance(nm, float) and math.isnan(nm))
                    else None
                )
                _ph.append(  # noqa: PG-APPEND
                    ph
                    if ph is not None and not (isinstance(ph, float) and math.isnan(ph))
                    else None
                )
                if (
                    pe is not None
                    and pe > 0
                    and not (isinstance(pe, float) and math.isnan(pe))
                ):
                    _inv_pe.append(1.0 / pe)  # noqa: PG-APPEND
                else:
                    _inv_pe.append(None)  # noqa: PG-APPEND

            candidate_df["_nm"] = _nm
            candidate_df["_ph"] = _ph
            candidate_df["_inv_pe"] = _inv_pe

            nm_rank = pd.to_numeric(candidate_df["_nm"], errors="coerce").rank(
                pct=True, ascending=True
            )
            ph_rank = pd.to_numeric(candidate_df["_ph"], errors="coerce").rank(
                pct=True, ascending=True
            )
            pe_rank = pd.to_numeric(candidate_df["_inv_pe"], errors="coerce").rank(
                pct=True, ascending=True
            )

            nm_valid = candidate_df["_nm"].notna()
            ph_valid = candidate_df["_ph"].notna()
            pe_valid = candidate_df["_inv_pe"].notna()

            # Default weights: 0.4, 0.3, 0.3
            w_nm, w_ph, w_pe = 0.4, 0.3, 0.3

            # Vectorized quality score computation
            has_nm = nm_valid.values.astype(float)
            has_ph = ph_valid.values.astype(float)
            has_pe = pe_valid.values.astype(float)
            active_w = has_nm * w_nm + has_ph * w_ph + has_pe * w_pe
            qscores = [None] * len(candidate_df)
            nonzero = active_w > 0
            nm_vals = nm_rank.values
            ph_vals = ph_rank.values
            pe_vals = pe_rank.values
            for idx_c in np.where(nonzero)[0]:
                aw = active_w[idx_c]
                sc = 0.0
                if has_nm[idx_c]:
                    sc += (w_nm / aw) * nm_vals[idx_c] * 100
                if has_ph[idx_c]:
                    sc += (w_ph / aw) * ph_vals[idx_c] * 100
                if has_pe[idx_c]:
                    sc += (w_pe / aw) * pe_vals[idx_c] * 100
                qscores[idx_c] = round(sc, 1)

            candidate_df["quality_score"] = qscores
            candidate_df.drop(columns=["_nm", "_ph", "_inv_pe"], inplace=True)
            # Sanitize floats — vectorized
            float_fields = [
                "close",
                "market_cap_cr",
                "delivery_absorption",
                "pct_above_52w_low",
                "adtv_cr",
                "score",
                "quality_score",
                "sl_price",
                "swing_low_n",
            ]
            for field in float_fields:
                if field in candidate_df.columns:
                    candidate_df[field] = candidate_df[field].map(self._sanitize_float)
            # Sort by score descending
            candidate_df = candidate_df.sort_values(
                "score", ascending=False
            ).reset_index(drop=True)
        else:
            candidate_df = pd.DataFrame(
                columns=[
                    "symbol",
                    "sector",
                    "sector_mom_tier",
                    "quality_score",
                    "delivery_spike_conf",
                    "timeframe",
                    "close",
                    "market_cap_cr",
                    "delivery_absorption",
                    "pct_above_52w_low",
                    "adtv_cr",
                    "score",
                    "tier",
                ]
            )

        logger.info(
            "Bottom Hunter scan complete: %d candidates found", len(candidate_df)
        )
        return candidate_df
