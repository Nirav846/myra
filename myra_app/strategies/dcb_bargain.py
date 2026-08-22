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
        # min_discount = 15% is backtest-validated (511 symbols, 19 dates, cost-adj)
        # yields +18.2% net 60d, 77.8% win rate (n=54)
        min_discount_pct=15.0,
        max_discount_pct=60.0,
        min_del_abs=-2.0,
        min_adtv_cr=1.0,
        min_high_del_days=10,  # TODO: validate with backtest
        sanity_mult=5.0,  # TODO: validate with backtest — DCB must be < 5x current close
        timeframe="daily",
        min_ff_mcap=600.0,
        corporate_actions_exclude_days=0,
        traction_window=1,
        traction_aggregation="latest",
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
        self.corporate_actions_exclude_days = corporate_actions_exclude_days
        self.traction_window = traction_window
        self.traction_aggregation = traction_aggregation

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
                       f.free_float_pct AS ff_pct
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
                # Fallback for older DBs without delivery_pct column
                if max_date:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               NULL AS delivery_pct
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
                               NULL AS delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date),
                    ).fetchall()
        return rows

    @staticmethod
    def _sanitize_float(value):
        return sanitize_float(value)

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
    def _tier_from_score(score: float) -> str:
        """Fallback tier assignment when pool < 10 candidates.

        Magic number thresholds (HIGH >= 20, MOD >= 10) are unvalidated
        fallback values — actual tier cutoffs should be derived from
        backtest performance when the candidate pool is large enough for
        percentile-based assignment."""
        if score >= 20:  # TODO: validate with backtest — unvalidated fallback threshold
            return "HIGH"
        elif score >= 10:  # TODO: validate with backtest — unvalidated fallback threshold
            return "MOD"
        return "LOW"

    @staticmethod
    def _compute_depth_tag(
        discount_pct: float, values: list[float] | None = None
    ) -> str:
        """Return DEEP / MID / SHALLOW based on discount percentage.
        If >= 5 historical values provided, uses stock-specific percentile ranking.
        Otherwise falls back to universal thresholds (>20 DEEP, >10 MID)."""
        if values and len(values) >= 5:
            arr = np.array(values)
            rank = float((arr <= discount_pct).mean()) * 100
            if rank > 80:
                return "DEEP"
            elif rank >= 50:
                return "MID"
            return "SHALLOW"
        if discount_pct > 20:
            return "DEEP"
        elif discount_pct > 10:
            return "MID"
        return "SHALLOW"

    def _is_lower_circuit(self, df: pd.DataFrame, idx: int) -> bool:
        """Check if the candle at idx was a lower-circuit day.

        HEURISTIC: Uses a 5% price-drop threshold as a proxy for NSE circuit
        bands, which are not currently available via API.  Actual circuit limits
        vary by stock (typically 2%, 5%, 10%, or 20%) and are published by NSE
        daily.  Replace this heuristic with real circuit-band data when
        available (e.g. from NSE's daily bhavcopy or a dedicated API).
        """
        if idx < 1:
            return False
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]
        close = float(row["close"])
        low = float(row["low"])
        prev_close = float(prev["close"])
        # Close pinned at the low (within 1%) AND dropped 5%+ from previous close
        is_pinned = close <= low * 1.01
        is_significant_drop = close < prev_close * 0.95  # TODO: validate with backtest — 5% is a heuristic; actual circuit bands vary by stock
        return is_pinned and is_significant_drop

    @staticmethod
    def _is_likely_circuit_lock(df: pd.DataFrame, idx: int) -> bool:
        """Detect likely circuit-lock: consecutive lower-circuit days with
        severely reduced volume (lock-up symptom).

        HEURISTIC: This method relies on the 5% drop threshold from
        ``_is_lower_circuit`` to approximate NSE circuit bands.  The streak
        length (3 days) and volume-collapse threshold (20%) are unvalidated
        magic numbers — replace with real circuit-band data and proper
        backtest validation when NSE circuit limits become available.

        Uses .values numpy slicing (no .iloc in loop — perf guard safe)."""
        if idx < 2:
            return False
        closes = df["close"].values.astype(float)
        lows = df["low"].values.astype(float)
        volumes = df["volume"].values.astype(float)

        # Walk back to find the start of the trailing lower-circuit streak
        streak = 0
        i = idx
        while i >= 1:
            close_i = closes[i]
            low_i = lows[i]
            prev_close_i = closes[i - 1]
            is_pinned = close_i <= low_i * 1.01
            is_drop = close_i < prev_close_i * 0.95  # TODO: validate with backtest — same 5% heuristic as _is_lower_circuit
            if is_pinned and is_drop:
                streak += 1
                i -= 1
            else:
                break

        if streak < 3:  # TODO: validate with backtest — 3-day minimum streak
            return False

        first_idx = i + 1  # first circuit day in the streak
        avg_circuit_vol = float(volumes[first_idx : idx + 1].mean())
        pre_start = max(0, first_idx - 20)
        avg_pre_vol = (
            float(volumes[pre_start:first_idx].mean()) if pre_start < first_idx else 0.0
        )
        return avg_pre_vol > 0 and avg_circuit_vol < 0.2 * avg_pre_vol  # TODO: validate with backtest — 20% volume collapse threshold

    @staticmethod
    def _check_spike_deep(df_daily: pd.DataFrame, discount_pct: float) -> bool:
        """Return True if today's delivery_pct >= 1.3x 50-day avg AND
        close_loc >= 0.6 AND discount_pct > 20. Uses daily frame.

        Magic numbers: 1.3x delivery spike, 0.6 close location, 20% discount
        threshold — all TODO: validate with backtest."""
        if len(df_daily) < 20:
            return False
        del_avg = df_daily["delivery_pct"].tail(50).mean()
        if pd.isna(del_avg) or del_avg <= 0:
            return False
        last = df_daily.iloc[-1]
        if last["delivery_pct"] < 1.3 * del_avg:  # TODO: validate with backtest — 1.3x delivery spike threshold
            return False
        high, low, close = float(last["high"]), float(last["low"]), float(last["close"])
        if high == low:
            clr = 1.0 if close == high else 0.0
        else:
            clr = (close - low) / (high - low)
        return clr >= 0.6 and discount_pct > 20  # TODO: validate with backtest — 0.6 close location, 20% discount threshold

    def _compute_depth_history(self, df_daily: pd.DataFrame) -> dict:
        """Compute 1-year DCB discount range from historical cutoffs.
        Returns dict with 'values' list, 'min', 'median', 'max'.
        Uses the daily frame with strided windows for efficiency."""
        empty = {"values": [], "min": None, "median": None, "max": None}
        if len(df_daily) < self.dcb_window + 20:
            return empty

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
                cutoff_indices.append(i)  # noqa: PG-APPEND
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
                discount_pcts.append(disc)  # noqa: PG-APPEND

        if len(discount_pcts) < 3:
            return empty

        arr = np.array(discount_pcts)
        return {
            "values": discount_pcts,
            "min": self._sanitize_float(float(np.min(arr))),
            "median": self._sanitize_float(float(np.median(arr))),
            "max": self._sanitize_float(float(np.max(arr))),
        }

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
        except Exception as e:
            logger.warning("DCB sector map load failed: %s", e)

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
                if self.min_ff_mcap > 0 and ff_pct is None:
                    logger.debug(
                        "Skipping %s: free_float_pct missing, cannot apply FF filter",
                        symbol,
                    )
                    continue
                if self.min_ff_mcap <= 0:
                    ff_data_quality = "missing"
                    free_float_mcap_cr = None
                else:
                    ff_data_quality = "measured"
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

                # Depth history (1-year DCB discount range) — compute BEFORE depth tag
                depth_hist = self._compute_depth_history(df)
                discount_values = depth_hist["values"]

                # Depth tag (stock-specific if enough history, else universal)
                depth = self._compute_depth_tag(discount_pct, discount_values)
                depth_basis = "historical" if len(discount_values) >= 5 else "universal"

                # Spike+Deep (uses daily frame)
                spike_deep = self._check_spike_deep(df, discount_pct)

                # Score (tier assigned after pool pass)
                score = discount_pct * 0.6 + del_abs * 0.4  # TODO: validate with backtest — 60/40 weighting is unvalidated

                # Lower-circuit detection (uses daily df)
                is_lower_circuit = self._is_lower_circuit(df, len(df) - 1)
                circuit_days_last_5 = 0
                start_idx = max(0, len(df) - 5)
                for ci in range(start_idx, len(df)):
                    if self._is_lower_circuit(df, ci):
                        circuit_days_last_5 += 1

                # Circuit streak: consecutive lower-circuit days ending at last row
                circuit_streak = 0
                last_idx = len(df) - 1
                for ci in range(last_idx, 0, -1):
                    if self._is_lower_circuit(df, ci):
                        circuit_streak += 1
                    else:
                        break

                # Circuit lock detection
                is_circuit_lock = self._is_likely_circuit_lock(df, last_idx)

                candidates.append(  # noqa: PG-APPEND
                    {
                        "symbol": symbol,
                        "sector": _sector_map.get(symbol, "Unknown"),
                        "close": round(close, 2),
                        "dcb": round(dcb, 2),
                        "discount_pct": round(discount_pct, 2),
                        "depth": depth,
                        "depth_basis": depth_basis,
                        "del_abs": round(del_abs, 2),
                        "adtv_cr": round(adtv_cr, 2),
                        "high_del_days": high_del_days,
                        "free_float_mcap_cr": round(free_float_mcap_cr, 2)
                        if free_float_mcap_cr is not None
                        else None,
                        "ff_data_quality": ff_data_quality,
                        "spike_deep": spike_deep,
                        "is_lower_circuit": is_lower_circuit,
                        "circuit_days_last_5": circuit_days_last_5,
                        "circuit_streak": circuit_streak,
                        "is_circuit_lock": is_circuit_lock,
                        "dcb_disc_min": depth_hist["min"],
                        "dcb_disc_median": depth_hist["median"],
                        "dcb_disc_max": depth_hist["max"],
                        "score": round(score, 2),
                        "tier": "LOW",  # placeholder, reassigned below
                        "timeframe": self.timeframe,
                    }
                )
            except Exception:
                logger.exception("DCB scan failed for %s", symbol)
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
            "circuit_streak",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Dynamic tier assignment: percentile-based when pool >= 10, else fallback
        n = len(candidates)
        if n >= 10:
            high_cut = math.ceil(0.2 * n)
            mod_cut = math.ceil(0.5 * n)
            for i, c in enumerate(candidates):
                if i < high_cut:
                    c["tier"] = "HIGH"
                elif i < mod_cut:
                    c["tier"] = "MOD"
                else:
                    c["tier"] = "LOW"
        else:
            for c in candidates:
                c["tier"] = self._tier_from_score(c["score"])

        logger.info("DCB Bargain scan complete: %d candidates found", n)

        # Corporate action filter
        candidates = self._filter_corporate_actions(candidates, as_on_date)

        return pd.DataFrame(candidates)

    def _filter_corporate_actions(self, candidates: list[dict], as_on_date: str) -> list[dict]:
        """Remove symbols with bonus/split/rights in the last N days.

        Performs case-insensitive matching on action_type to handle
        inconsistent casing in the corporate_actions table.
        """
        if self.corporate_actions_exclude_days <= 0 or not candidates:
            return candidates
        try:
            inst_db = self._db_path("institutional")
            if not os.path.exists(inst_db):
                return candidates
            cutoff_dt = pd.Timestamp(as_on_date) - pd.Timedelta(days=self.corporate_actions_exclude_days)
            cutoff = f"{cutoff_dt:%Y-%m-%d}"
            syms = [c["symbol"] for c in candidates]
            placeholders = ",".join("?" for _ in syms)
            # Filter list — lowercase for case-insensitive matching
            exclude_types = {"bonus", "split", "rights", "bonus issue",
                             "stock split", "rights issue"}
            with sqlite3.connect(inst_db) as conn:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT symbol, action_type FROM corporate_actions
                    WHERE symbol IN ({placeholders})
                      AND ex_date >= ?
                    """,
                    (*syms, cutoff),
                ).fetchall()
            # Case-insensitive matching: compare lowercased action_type
            excluded: set[str] = set()
            for symbol, action_type in rows:
                if action_type and action_type.strip().lower() in exclude_types:
                    excluded.add(symbol)
                elif action_type:
                    logger.warning(
                        "CA filter: unrecognised action_type '%s' for %s — "
                        "not excluded (add to filter list if needed)",
                        action_type, symbol,
                    )
            if excluded:
                logger.info("CA filter: excluding %d symbols with recent actions", len(excluded))
            return [c for c in candidates if c["symbol"] not in excluded]
        except Exception as e:
            logger.warning("CA filter failed: %s", e)
            return candidates
