\nimport logging
\nimport math
\nfrom myra_app.strategies.scanner_utils import sanitize_float
\nimport sqlite3
\nimport os
\nimport numpy as np
\nimport pandas as pd
\nfrom datetime import date, timedelta
\nfrom myra_app.constants import DB_DIR
\nfrom myra_app.librarian_core import LibrarianCore
\nfrom myra_app.db.bulk_loader import (
\n    load_ohlcv_for_universe,
\n    rows_for_symbol,
\n    COLUMNS_13,
\n)
\n
\nlogger = logging.getLogger(__name__)
\n
\n# Weights feeding the Spring `spring_score` (NOT `e["quality"]` — for Spring,
\n# `quality` comes from `_event_quality("Spring", ...)` = del/75*50 + rec/5*50,
\n# a separate formula these weights do not control). Four base weights scale the
\n# four component scores (summing to 90), the two bonuses are added on top, and
\n# `_compute_spring_score` clamps the total to [0, 100].
\n#
\n# Calibration attempted 2026-08 via tools/calibrate_wyckoff_weights.py
\n# (random search, 400 symbols / 12 scan dates / 800 combos, seed 42):
\n# ABANDONED. On the fresh dataset no candidate passed the out-of-sample
\n# VALIDATION gate (best-on-train VAL Q5-Q1 -2.14% < this default's +11.21%).
\n# Re-verified 2026-08-29 on the full dataset (same params): outcome identical —
\n# best-on-train VAL Q5-Q1 -2.14% (gap -13.35%) vs default +11.21%, so the
\n# shipping weights are still optimal. These remain the shipping weights.
\nDEFAULT_SPRING_WEIGHTS = {
\n    "delivery_absorption": 30,
\n    "lower_wick": 30,
\n    "close_location": 20,
\n    "grab_depth": 10,
\n    "equal_low_bonus": 10,
\n    "two_candle_bonus": 5,
\n}
\n
\n
\nclass WyckoffAutomaton:
\n    _bulk_data = None
\n    _BULK_COLUMNS = COLUMNS_13
\n    # Class-level alias so both `WyckoffAutomaton.DEFAULT_SPRING_WEIGHTS` and the
\n    # module-level constant refer to the same dict.
\n    DEFAULT_SPRING_WEIGHTS = DEFAULT_SPRING_WEIGHTS
\n
\n    def __init__(
\n        self,
\n        min_mcap=510,
\n        max_mcap=530000,
\n        lookback_days=90,
\n        restrict_to_holdings=False,
\n        weights: dict | None = None,
\n        mcap_weight: float = 20,
\n    ):
\n        self.min_mcap = min_mcap
\n        self.max_mcap = max_mcap
\n        self.lookback_days = lookback_days
\n        self.restrict_to_holdings = bool(restrict_to_holdings)
\n        # Spring scoring weights. A partial override dict is merged over the
\n        # defaults, so `weights={"delivery_absorption": 50}` keeps every other
\n        # weight at its default. Detection gates/thresholds are NOT affected.
\n        self._weights = {**DEFAULT_SPRING_WEIGHTS, **(weights or {})}
\n
\n        # `mcap_weight` scales the price-adjusted market-cap factor added to the
\n        # Spring `quality` score (see `_event_quality`). Set to 0 to disable the
\n        # mcap factor entirely. Calibrated default: 20.
\n        self.mcap_weight = float(mcap_weight)
\n
\n        # Current-snapshot market cap per universe symbol (seeded in `scan()`).
\n        # `_get_historical_mcap` uses this as the "current mcap" numerator.
\n        self._current_mcap_map: dict[str, float | None] = {}
\n
\n        # Per-scan log-mcap normalisation range (min, max) over the universe,
\n        # computed in `scan()` from `_current_mcap_map` (point-in-time: only
\n        # data available on the scan date). Used to normalise the Spring mcap
\n        # factor to 0-1 so `mcap_weight` contributes on the same scale as the
\n        # other quality components (max 20) instead of saturating at 100.
\n        # None when no scan has run (direct `_detect_events` callers) — the
\n        # mcap factor is then skipped (plain base score).
\n        self._mcap_log_range: tuple[float, float] | None = None
\n
\n    def _db_path(self, key: str) -> str:
\n        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])
\n
\n    def _get_universe(self) -> list[tuple]:
\n        val_db = self._db_path("valuation")
\n        if not os.path.exists(val_db):
\n            return []
\n        with sqlite3.connect(val_db) as conn:
\n            rows = conn.execute(
\n                """
\n                SELECT f.symbol,
\n                       COALESCE(f.market_cap, 0) AS mcap,
\n                       COALESCE(f.free_float_pct, 40.0) AS ff_pct
\n                FROM fundamentals f
\n                INNER JOIN (
\n                    SELECT symbol, MAX(date) as max_date
\n                    FROM fundamentals
\n                    WHERE COALESCE(market_cap, 0) > 0
\n                    GROUP BY symbol
\n                ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
\n                WHERE COALESCE(f.market_cap, 0) / 1e7 BETWEEN ? AND ?
\n                """,
\n                (self.min_mcap, self.max_mcap),
\n            ).fetchall()
\n
\n        if self.restrict_to_holdings:
\n            from myra_app.utils.fund_utils import get_holding_symbols
\n
\n            holdings = get_holding_symbols()
\n            if not holdings:
\n                logger.warning(
\n                    "restrict_to_holdings=True but no holding data — "
\n                    "falling back to full universe (%d symbols)",
\n                    len(rows),
\n                )
\n            else:
\n                before = len(rows)
\n                rows = [r for r in rows if r[0].strip() in holdings]
\n                logger.info(
\n                    "Holdings filter: %d → %d symbols (latest month)",
\n                    before,
\n                    len(rows),
\n                )
\n        return rows
\n
\n    def _get_tech_data(
\n        self, symbol: str, min_date: str, max_date: str | None = None
\n    ) -> list[tuple]:
\n        max_date = max_date or date.today().isoformat()
\n        if self._bulk_data is not None:
\n            return rows_for_symbol(
\n                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
\n            )
\n        tech_db = self._db_path("technical")
\n        if not os.path.exists(tech_db):
\n            return []
\n        with sqlite3.connect(tech_db) as conn:
\n            try:
\n                rows = conn.execute(
\n                    """
\n                    SELECT date, open, high, low, close, volume, delivery,
\n                           delivery_pct, swing_low,
\n                           nifty_outperformance_score,
\n                           sma_50, high_52w, low_52w
\n                    FROM technical_data
\n                    WHERE symbol = ? AND date >= ? AND date <= ?
\n                    ORDER BY date ASC
\n                    """,
\n                    (symbol, min_date, max_date),
\n                ).fetchall()
\n            except sqlite3.OperationalError:
\n                rows = conn.execute(
\n                    """
\n                    SELECT date, open, high, low, close, volume, delivery,
\n                           delivery_pct, NULL AS swing_low,
\n                           nifty_outperformance_score,
\n                           NULL AS sma_50, NULL AS high_52w, NULL AS low_52w
\n                    FROM technical_data
\n                    WHERE symbol = ? AND date >= ? AND date <= ?
\n                    ORDER BY date ASC
\n                    """,
\n                    (symbol, min_date, max_date),
\n                ).fetchall()
\n        return rows
\n
\n    def _get_historical_mcap(
\n        self, df: pd.DataFrame, symbol: str, as_on_date: str | None
\n    ) -> float | None:
\n        """Resolve a leak-free, price-adjusted historical market cap.
\n
\n        Approximates the market cap as of ``as_on_date`` as
\n        ``current_mcap * (price_t / current_price)`` — i.e. the current
\n        fundamentals snapshot market cap scaled by how the price has moved
\n        between the event date and now. This is leak-free because it only uses
\n        historical price data (``price_t``) plus the current snapshot; it never
\n        consults data dated after the event.
\n
\n        Returns ``None`` (and the caller falls back to the plain score) when
\n        any required input is missing: no current mcap for the symbol, no price
\n        series, no price on the event date, or a non-positive price.
\n
\n        The price series is the caller's ``df`` (the same per-symbol frame the
\n        detection loop uses) — NOT ``_bulk_data`` — so the factor is identical
\n        whether the scanner ran through the bulk loader or the per-symbol DB
\n        path (the two paths are parity-tested to produce the same ``df``).
\n
\n        Caveat: this assumes shares outstanding are unchanged since the
\n        fundamentals snapshot. Stock splits, buybacks, or fresh issuance shift
\n        the per-share price without a proportional market-cap change, so the
\n        ratio is an approximation, not an exact point-in-time figure.
\n        """
\n        sym = symbol.strip()
\n        # 1. Current market cap (snapshot), cheapest source first: the seeded
\n        #    _current_mcap_map, then a lazy per-symbol query memoized into it.
\n        current = self._current_mcap_map.get(sym)
\n        if current is None:
\n            if sym in self._current_mcap_map:
\n                return None  # memoized missing
\n            if not os.path.exists(self._db_path("valuation")):
\n                self._current_mcap_map[sym] = None
\n                return None
\n            try:
\n                with sqlite3.connect(self._db_path("valuation")) as conn:
\n                    row = conn.execute(
\n                        "SELECT market_cap FROM fundamentals "
\n                        "WHERE symbol = ? AND market_cap IS NOT NULL "
\n                        "ORDER BY date DESC LIMIT 1",
\n                        (sym,),
\n                    ).fetchone()
\n                current = float(row[0]) if row and row[0] is not None else None
\n            except (sqlite3.Error, OSError):
\n                current = None
\n            self._current_mcap_map[sym] = current
\n        if current is None or current <= 0:
\n            return None
\n
\n        # 2. Price series: caller-supplied df (identical across bulk/DB paths).
\n        if df is None or df.empty or "close" not in df.columns:
\n            return None
\n
\n        # 3. Current price = latest close (frames are date-ascending).
\n        current_price = float(df["close"].iloc[-1])
\n        if current_price is None or current_price <= 0 or pd.isna(current_price):
\n            return None
\n
\n        # 4. Price on the event date.
\n        if not as_on_date:
\n            return None
\n        target = pd.Timestamp(as_on_date).date()
\n        dates = pd.to_datetime(df["date"]).dt.date
\n        idx = self._first_index_equal(dates, target)
\n        if idx is None:
\n            return None
\n        price_t = float(df["close"].iloc[idx])
\n        if price_t is None or price_t <= 0 or pd.isna(price_t):
\n            return None
\n
\n        # 5. Price-adjusted historical market cap.
\n        return current * (price_t / current_price)
\n
\n    @staticmethod
\n    def _first_index_equal(series, target) -> int | None:
\n        """First positional index in ``series`` equal to ``target``, else None."""
\n        for i, val in enumerate(series):
\n            if val == target:
\n                return i
\n        return None
\n
\n    def _normalise_mcap(self, hist_mcap: float | None) -> float | None:
\n        """Normalise a historical mcap to 0-1 against the scan-universe range.
\n
\n        Returns ``(ln(hist_mcap) - lo) / (hi - lo)`` clamped to [0, 1], where
\n        (lo, hi) is the per-scan ln(current-mcap) range set in ``scan()`` (the
\n        universe's min/max — point-in-time safe, scan-date data only).
\n
\n        Returns None (caller falls back to the plain base score) only when there
\n        is no historical mcap or no scan range (direct ``_detect_events``
\n        callers never ran ``scan()``). A degenerate range (all universe mcaps
\n        identical) returns 0.0 — the factor does nothing but stays non-None,
\n        matching the "robust to edge cases" requirement.
\n        """
\n        if hist_mcap is None or hist_mcap <= 0 or self._mcap_log_range is None:
\n            return None
\n        lo, hi = self._mcap_log_range
\n        if hi <= lo:
\n            return 0.0
\n        norm = (math.log(hist_mcap) - lo) / (hi - lo)
\n        return min(max(norm, 0.0), 1.0)
\n
\n    @staticmethod
\n    def _sanitize_float(value):
\n        return sanitize_float(value)
\n
\n    @staticmethod
\n    def _has_same_event(events, event_type: str, event_date: str) -> bool:
\n        """True if any event already exists with BOTH the same event type and
\n        the same event date (dedup is per-type, so different types on the
\n        same date are all preserved)."""
\n        return any(
\n            e.get("event") == event_type and e.get("event_date") == event_date
\n            for e in events
\n        )
\n
\n    @staticmethod
\n    def _delivery_absorption_score(
\n        del_abs: float, max_score: float = DEFAULT_SPRING_WEIGHTS["delivery_absorption"]
\n    ) -> float:
\n        """0-max_score. Linear: <=0 -> 0, +5 -> max_score/2, +10 -> max_score."""
\n        return round(min(max(del_abs, 0.0) / 10.0 * max_score, max_score), 1)
\n
\n    @staticmethod
\n    def _lower_wick_score(
\n        ratio: float, max_score: float = DEFAULT_SPRING_WEIGHTS["lower_wick"]
\n    ) -> float:
\n        """0-max_score. Piecewise-linear breakpoints anchored at the default 30
\n        scale ((0.20,0), (0.40,15), (0.60,22), (0.75,30)) and scaled by
\n        max_score/30 so the shape is identical at any weight."""
\n        scale = max_score / 30.0
\n        pts = [
\n            (0.20, 0.0),
\n            (0.40, 15.0 * scale),
\n            (0.60, 22.0 * scale),
\n            (0.75, float(max_score)),
\n        ]
\n        if ratio <= pts[0][0]:
\n            return 0.0
\n        if ratio >= pts[-1][0]:
\n            return float(max_score)
\n        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
\n            if x0 <= ratio < x1:
\n                return round(y0 + (ratio - x0) / (x1 - x0) * (y1 - y0), 1)
\n        return 0.0
\n
\n    @staticmethod
\n    def _close_location_score(
\n        ratio: float, max_score: float = DEFAULT_SPRING_WEIGHTS["close_location"]
\n    ) -> float:
\n        """0-max_score. Step: <0.5 -> max_score/4, 0.5-0.75 -> max_score/2,
\n        >0.75 -> max_score."""
\n        if ratio > 0.75:
\n            return float(max_score)
\n        if ratio >= 0.5:
\n            return round(max_score / 2.0, 1)
\n        return round(max_score / 4.0, 1)
\n
\n    @staticmethod
\n    def _grab_depth_score(
\n        depth_pct: float, max_score: float = DEFAULT_SPRING_WEIGHTS["grab_depth"]
\n    ) -> float:
\n        """0-max_score. Step: <0.5 -> max_score*0.7, 0.5-1.5 -> max_score,
\n        >1.5 -> max_score*0.5."""
\n        if depth_pct > 1.5:
\n            return round(max_score * 0.5, 1)
\n        if depth_pct >= 0.5:
\n            return float(max_score)
\n        return round(max_score * 0.7, 1)
\n
\n    @staticmethod
\n    def _spring_grade(score: float) -> str:
\n        """A+ >= 65, B >= 50, C >= 35, D < 35."""
\n        if score >= 65:
\n            return "A+"
\n        if score >= 50:
\n            return "B"
\n        if score >= 35:
\n            return "C"
\n        return "D"
\n
\n    @staticmethod
\n    def _compute_spring_score(
\n        del_score,
\n        wick_score,
\n        close_score,
\n        depth_score,
\n        equal_low_bonus,
\n        two_candle_confirm: bool = False,
\n        confirm_bonus: float = DEFAULT_SPRING_WEIGHTS["two_candle_bonus"],
\n    ) -> float:
\n        """Sum of factors + confirm_bonus if two_candle_confirm, clamped to [0, 100]."""
\n        total = del_score + wick_score + close_score + depth_score + equal_low_bonus
\n        if two_candle_confirm:
\n            total += confirm_bonus
\n        return round(min(max(total, 0.0), 100.0), 1)
\n
\n    def _event_quality(
\n        self,
\n        event_type: str,
\n        vol_ratio: float,
\n        del_pct: float,
\n        avg_del_pct: float,
\n        extra: dict | None = None,
\n    ) -> float:
\n        """
\n        Event-specific quality score (0–100).
\n        Each event type has a different definition of 'quality'.
\n        """
\n        extra = extra or {}
\n        if event_type == "SC":
\n            vol_score = min(vol_ratio / 4.0 * 50, 50)
\n            del_score = min(del_pct / 80.0 * 50, 50)
\n            return round(min(vol_score + del_score, 100), 1)
\n
\n        elif event_type == "AR":
\n            rally_pct = float(extra.get("rally_pct", 0))
\n            rally_score = min(rally_pct / 8.0 * 40, 40)
\n            vol_score = min(max(0, (1.0 - vol_ratio)) * 40, 40)
\n            del_score = min(del_pct / 60.0 * 20, 20)
\n            return round(min(rally_score + vol_score + del_score, 100), 1)
\n
\n        elif event_type == "ST":
\n            vol_score = min(max(0, (1.0 - vol_ratio)) / 0.5 * 50, 50)
\n            # avg_del_pct is the average delivery % over the window; a low
\n            # delivery_pct relative to that baseline confirms sellers have left.
\n            del_score = min(max(0, (1.0 - del_pct / avg_del_pct)) * 50, 50)
\n            return round(min(vol_score + del_score, 100), 1)
\n
\n        elif event_type == "Spring":
\n            recovery_pct = float(extra.get("recovery_pct", 0))
\n            del_score = min(del_pct / 75.0 * 50, 50)
\n            rec_score = min(recovery_pct / 5.0 * 50, 50)
\n            base = del_score + rec_score
\n            # Normalised market-cap factor (Spring-only): the caller passes the
\n            # 0-1 normalised log-mcap (see `_detect_events`) via
\n            # extra["norm_mcap"], where 0/1 are the min/max log-mcap across the
\n            # scan universe. `mcap_weight` therefore adds at most
\n            # `mcap_weight * 1` (20 with the default) — the same scale as the
\n            # other quality components — instead of `mcap_weight * log(mcap)`
\n            # which saturated every Spring at 100. Missing norm_mcap (no scan
\n            # range or unresolved historical mcap) => plain base score; a
\n            # degenerate range passes 0.0 (adds nothing, numerically identical
\n            # to base); `mcap_weight=0` disables the factor entirely.
\n            norm_mcap = extra.get("norm_mcap")
\n            if norm_mcap is not None and self.mcap_weight > 0:
\n                base = base + self.mcap_weight * float(norm_mcap)
\n            return round(min(base, 100), 1)
\n
\n        elif event_type == "SOS":
\n            close_pos = float(extra.get("close_position", 0.5))
\n            vol_score = min(vol_ratio / 3.0 * 40, 40)
\n            del_score = min(del_pct / 70.0 * 40, 40)
\n            pos_score = min(close_pos * 20, 20)
\n            return round(min(vol_score + del_score + pos_score, 100), 1)
\n
\n        return 0.0
\n
\n    def _detect_events(
\n        self, df: pd.DataFrame, symbol: str = "", as_on_date: str | None = None
\n    ) -> list[dict]:
\n        events = []
\n        n = len(df)
\n        if n < 55:
\n            return events
\n
\n        # Rolling baselines: expanding series so every signal only sees
\n        # information available up to its own candle (no look-ahead bias).
\n        # pandas expanding() skips NaN by default; delivery_pct may be
\n        # object dtype, so cast to float first — the old code used a single
\n        # global mean over the whole df (look-ahead) whose np .values mean
\n        # was NaN-poisoned by any NaN delivery_pct anywhere in the window.
\n        exp_avg_vol = df["volume"].expanding().mean()
\n        exp_avg_del = df["delivery_pct"].astype(float).expanding().mean()
\n        exp_range_low = df["low"].expanding().min()
\n        exp_range_high = df["high"].expanding().max()
\n
\n        # Scan the recent lookback window. tail(90) is hardcoded to match the
\n        # default lookback_days=90 (session count, not calendar days).
\n        scan_df = df.tail(90).reset_index(drop=True)
\n        for i in range(len(scan_df)):
\n            abs_i = i + n - len(scan_df)
\n            row = scan_df.iloc[i]
\n            row_date = str(row["date"])
\n            open_p = float(row["open"])
\n            high_p = float(row["high"])
\n            low_p = float(row["low"])
\n            close_p = float(row["close"])
\n            volume_p = float(row["volume"])
\n            del_pct = float(row["delivery_pct"])
\n
\n            if volume_p == 0:
\n                continue
\n            if float(exp_avg_vol.iloc[abs_i]) == 0:
\n                continue
\n
\n            row_avg_vol = float(exp_avg_vol.iloc[abs_i])
\n            row_avg_del = float(exp_avg_del.iloc[abs_i])
\n            row_range_low = float(exp_range_low.iloc[abs_i])
\n            row_range_high = float(exp_range_high.iloc[abs_i])
\n
\n            vol_ratio = volume_p / row_avg_vol if row_avg_vol > 0 else 0
\n
\n            # SC — Selling Climax
\n            is_sc = (
\n                volume_p > row_avg_vol * thresholds['volume_multiplier']
\n                and close_p > (low_p + (high_p - low_p) * thresholds['price_threshold_pct'])
\n                and del_pct > thresholds['delivery_threshold']
\n                and close_p <= row_range_low * thresholds['range_multiplier']
\n            )
\n
\n            if is_sc:
\n                quality = self._event_quality("SC", vol_ratio, del_pct, row_avg_del)
\n                events.append(  # noqa: PG-APPEND
\n                    {
\n                        "symbol": symbol,
\n                        "event": "SC",
\n                        "phase": "Phase A",
\n                        "phase_pct": 25,
\n                        "event_date": row_date,
\n                        "del_pct": del_pct,
\n                        "vol_ratio": vol_ratio,
\n                        "quality": quality,
\n                        "close": close_p,
\n                        "range_low_90": row_range_low,
\n                        "range_high_90": row_range_high,
\n                        "sc_reference_close": close_p,
\n                    }
\n                )
\n                continue
\n
\n            # Spring — Undercut & Recovery.
\n            # Undercut reference = prior running low (excludes the grab candle itself),
\n            # otherwise no candle could ever dip below the window's global minimum.
\n            prior_low = (
\n                float(df["low"].iloc[max(0, abs_i - 60) : abs_i].min())
\n                if abs_i > 0
\n                else float(exp_range_low.iloc[abs_i])
\n            )
\n            # Dynamic delivery threshold: must be 20% above the stock's own 50-day average
\n            # rather than an arbitrary absolute number that doesn't fit Indian markets
\n            avg_del_50d = float(
\n                df["delivery_pct"].iloc[max(0, abs_i - 50) : abs_i].mean()
\n            )
\n            del_threshold = max(25.0, avg_del_50d * thresholds['sos_volume_multiplier'])  # floor at 25% to avoid noise
\n            is_spring = (
\n                low_p < prior_low * 0.99
\n                and close_p > prior_low
\n                and del_pct > del_threshold
\n            )
\n
\n            if is_spring:
\n                try:
\n                    recovery_pct = (
\n                        (close_p - low_p) / (high_p - low_p) * 100
\n                        if (high_p - low_p) > 0
\n                        else 0.0
\n                    )
\n
\n                    # --- Structured Spring Scoring ---
\n                    # 1. Delivery absorption
\n                    start_idx = max(0, abs_i - 50)
\n                    del_slice = df["delivery_pct"].values.astype(float)[start_idx:abs_i]
\n                    avg_del_50 = (
\n                        float(np.nanmean(del_slice)) if len(del_slice) > 0 else del_pct
\n                    )
\n                    del_abs = del_pct - avg_del_50
\n                    del_score = self._delivery_absorption_score(
\n                        del_abs, self._weights["delivery_absorption"]
\n                    )
\n
\n                    # 2. Lower wick ratio + close location
\n                    denom = high_p - low_p
\n                    if denom > 0:
\n                        lower_wick_ratio = (min(open_p, close_p) - low_p) / denom
\n                        close_location = (close_p - low_p) / denom
\n                    else:
\n                        lower_wick_ratio = 0.5
\n                        close_location = 0.5
\n                    wick_score = self._lower_wick_score(
\n                        lower_wick_ratio, self._weights["lower_wick"]
\n                    )
\n                    close_score = self._close_location_score(
\n                        close_location, self._weights["close_location"]
\n                    )
\n
\n                    # 3. Grab depth (uses SMC swing_low at the grab candle)
\n                    has_swing = "swing_low" in df.columns
\n                    swing_low_val = None
\n                    if has_swing:
\n                        _sl = df["swing_low"].iloc[abs_i]
\n                        if pd.notna(_sl):
\n                            swing_low_val = float(_sl)
\n                    if swing_low_val is not None and swing_low_val > 0:
\n                        grab_depth_pct = (swing_low_val - low_p) / swing_low_val * 100
\n                    else:
\n                        grab_depth_pct = 0.0
\n                    depth_score = self._grab_depth_score(
\n                        grab_depth_pct, self._weights["grab_depth"]
\n                    )
\n
\n                    # 4. Equal-low detection (past + current only, no future
\n                    # look — rows after the grab candle must not influence it)
\n                    equal_low_zone = False
\n                    if swing_low_val is not None:
\n                        lo = max(0, abs_i - 20)
\n                        hi = min(n, abs_i + 1)
\n                        for j in range(lo, hi):
\n                            if j == abs_i:
\n                                continue
\n                            sl_j_raw = df["swing_low"].iloc[j] if has_swing else None
\n                            if sl_j_raw is not None and pd.notna(sl_j_raw):
\n                                sl_j = float(sl_j_raw)
\n                                low_j = float(df["low"].iloc[j])
\n                                if abs(low_j - sl_j) < 1e-9:
\n                                    if (
\n                                        abs(sl_j - swing_low_val) / swing_low_val
\n                                        <= 0.005
\n                                    ):
\n                                        equal_low_zone = True
\n                                        break
\n                    equal_low_bonus = (
\n                        self._weights["equal_low_bonus"] if equal_low_zone else 0.0
\n                    )
\n
\n                    # 5. Two-candle confirmation
\n                    two_candle_confirm = False
\n                    conf_date = None
\n                    if close_p < open_p and abs_i + 1 < n:
\n                        nrow = df.iloc[abs_i + 1]
\n                        nclose = float(nrow["close"])
\n                        nopen = float(nrow["open"])
\n                        ref_level = (
\n                            swing_low_val
\n                            if swing_low_val is not None
\n                            else float(prior_low)
\n                        )
\n                        if nclose > nopen and nclose > ref_level:
\n                            two_candle_confirm = True
\n                            # A confirmed Spring is dated on the confirmation
\n                            # candle: the signal is only actionable from then.
\n                            conf_date = str(nrow["date"])
\n
\n                    # 6. Compute score and grade
\n                    spring_score = self._compute_spring_score(
\n                        del_score,
\n                        wick_score,
\n                        close_score,
\n                        depth_score,
\n                        equal_low_bonus,
\n                        two_candle_confirm,
\n                        self._weights["two_candle_bonus"],
\n                    )
\n                    grade = self._spring_grade(spring_score)
\n
\n                    # 7. Skip grade D
\n                    if grade == "D":
\n                        continue
\n
\n                    # 8. Quality + price-adjusted historical market cap, both
\n                    # resolved as-of the event date (the confirmation candle for
\n                    # confirmed Springs) so the score can never see future data.
\n                    ev_date = conf_date if two_candle_confirm else row_date
\n                    # The price ratio uses the same per-symbol `df` the loop
\n                    # already holds — identical whether loaded via the bulk
\n                    # loader or the per-symbol DB path (parity-guaranteed).
\n                    hist_mcap = self._sanitize_float(
\n                        self._get_historical_mcap(df, symbol, ev_date)
\n                    )
\n                    # Normalise the mcap factor to 0-1 against the scan
\n                    # universe's log-mcap range, so `mcap_weight` contributes
\n                    # on the same scale as the other components (max 20) rather
\n                    # than saturating at 100. Degenerate range (all mcaps
\n                    # equal) or missing mcap/range (direct callers) -> skip
\n                    # the factor (plain base score).
\n                    norm_mcap = self._normalise_mcap(hist_mcap)
\n                    quality = self._event_quality(
\n                        "Spring",
\n                        vol_ratio,
\n                        del_pct,
\n                        row_avg_del,
\n                        extra={
\n                            "recovery_pct": recovery_pct,
\n                            "norm_mcap": norm_mcap,
\n                        },
\n                    )
\n
\n                    events.append(  # noqa: PG-APPEND
\n                        {
\n                            "symbol": symbol,
\n                            "event": "Spring",
\n                            "phase": "Phase C",
\n                            "phase_pct": 75,
\n                            "event_date": ev_date,
\n                            "del_pct": del_pct,
\n                            "vol_ratio": vol_ratio,
\n                            "quality": quality,
\n                            "close": close_p,
\n                            "range_low_90": row_range_low,
\n                            "range_high_90": row_range_high,
\n                            "sc_reference_close": close_p,
\n                            "spring_score": self._sanitize_float(spring_score),
\n                            "grade": grade,
\n                            "lower_wick_ratio": self._sanitize_float(
\n                                round(lower_wick_ratio, 3)
\n                            ),
\n                            "close_location": self._sanitize_float(
\n                                round(close_location, 3)
\n                            ),
\n                            "grab_depth_pct": self._sanitize_float(
\n                                round(grab_depth_pct, 2)
\n                            ),
\n                            "equal_low_zone": bool(equal_low_zone),
\n                            "two_candle_confirm": bool(two_candle_confirm),
\n                            "historical_mcap": hist_mcap,
\n                        }
\n                    )
\n                except (KeyError, IndexError, ValueError) as exc:
\n                    logger.warning(
\n                        "Spring scoring failed for %s: %s",
\n                        symbol,
\n                        exc,
\n                        exc_info=True,
\n                    )
\n                continue
\n
\n            # SOS — Sign of Strength
\n            is_sos = (
\n                close_p > (row_range_low + (row_range_high - row_range_low) * thresholds['sos_range_pct'])
\n                and volume_p > row_avg_vol * thresholds['sos_volume_multiplier']
\n                and del_pct >= row_avg_del * thresholds['sos_delivery_multiplier']
\n                and close_p > open_p
\n            )
\n
\n            if is_sos:
\n                close_position = (
\n                    (close_p - low_p) / (high_p - low_p)
\n                    if (high_p - low_p) > 0
\n                    else 0.5
\n                )
\n                quality = self._event_quality(
\n                    "SOS",
\n                    vol_ratio,
\n                    del_pct,
\n                    row_avg_del,
\n                    extra={"close_position": close_position},
\n                )
\n                events.append(  # noqa: PG-APPEND
\n                    {
\n                        "symbol": symbol,
\n                        "event": "SOS",
\n                        "phase": "Phase D",
\n                        "phase_pct": 90,
\n                        "event_date": row_date,
\n                        "del_pct": del_pct,
\n                        "vol_ratio": vol_ratio,
\n                        "quality": quality,
\n                        "close": close_p,
\n                        "range_low_90": row_range_low,
\n                        "range_high_90": row_range_high,
\n                        "sc_reference_close": close_p,
\n                    }
\n                )
\n                continue
\n
\n        # AR — Automatic Rally (post-SC) and ST — Secondary Test
\n        sc_events = [e for e in events if e["event"] == "SC"]
\n        for sc in sc_events:
\n            sc_idx = df[df["date"].astype(str) == sc["event_date"]]
\n            if sc_idx.empty:
\n                continue
\n            sc_pos = sc_idx.index[0]
\n            sc_close = sc["sc_reference_close"]
\n
\n            # Look within 10 sessions after SC
\n            post_sc = df.loc[sc_pos + 1 : sc_pos + 11]
\n            for _, nrow in post_sc.iterrows():  # noqa: PG-ITERROWS
\n                nclose = float(nrow["close"])
\n                nvol = float(nrow["volume"])
\n                ndel = float(nrow["delivery_pct"])
\n                # Rolling baselines at the nrow candle only (no future info).
\n                # df is a clean 0..n-1 RangeIndex (scan() resets it), so the
\n                # iterrows label equals the positional index.
\n                nrow_avg_vol = float(exp_avg_vol.iloc[nrow.name])
\n                nrow_avg_del = float(exp_avg_del.iloc[nrow.name])
\n
\n                # AR — Automatic Rally
\n                if nclose > sc_close * 1.03 and nvol <= nrow_avg_vol * 1.0:
\n                    ar_vol_ratio = nvol / nrow_avg_vol if nrow_avg_vol > 0 else 0
\n                    rally_pct = (
\n                        (nclose - sc_close) / sc_close * 100 if sc_close > 0 else 0.0
\n                    )
\n                    ar_quality = self._event_quality(
\n                        "AR",
\n                        ar_vol_ratio,
\n                        ndel,
\n                        nrow_avg_del,
\n                        extra={"rally_pct": rally_pct},
\n                    )
\n                    if not self._has_same_event(events, "AR", str(nrow["date"])):
\n                        events.append(  # noqa: PG-APPEND
\n                            {
\n                                "symbol": symbol,
\n                                "event": "AR",
\n                                "phase": "Phase A",
\n                                "phase_pct": 30,
\n                                "event_date": str(nrow["date"]),
\n                                "del_pct": ndel,
\n                                "vol_ratio": round(ar_vol_ratio, 1),
\n                                "quality": ar_quality,
\n                                "close": nclose,
\n                                "range_low_90": float(exp_range_low.iloc[nrow.name]),
\n                                "range_high_90": float(exp_range_high.iloc[nrow.name]),
\n                                "sc_reference_close": sc_close,
\n                            }
\n                        )
\n
\n                # ST — Secondary Test
\n                if (
\n                    abs(nclose - sc_close) / sc_close <= 0.05
\n                    and nvol < nrow_avg_vol * 0.7
\n                    and ndel <= nrow_avg_del
\n                ):
\n                    if not self._has_same_event(events, "ST", str(nrow["date"])):
\n                        st_vol_ratio = nvol / nrow_avg_vol if nrow_avg_vol > 0 else 0
\n                        st_quality = self._event_quality(
\n                            "ST", st_vol_ratio, ndel, nrow_avg_del
\n                        )
\n                        events.append(  # noqa: PG-APPEND
\n                            {
\n                                "symbol": symbol,
\n                                "event": "ST",
\n                                "phase": "Phase B",
\n                                "phase_pct": 50,
\n                                "event_date": str(nrow["date"]),
\n                                "del_pct": ndel,
\n                                "vol_ratio": round(st_vol_ratio, 1),
\n                                "quality": st_quality,
\n                                "close": nclose,
\n                                "range_low_90": float(exp_range_low.iloc[nrow.name]),
\n                                "range_high_90": float(exp_range_high.iloc[nrow.name]),
\n                                "sc_reference_close": sc_close,
\n                            }
\n                        )
\n
\n        # Recency relative to the as-of date (so backtests use the selected
\n        # scan date, not today). Falls back to today for live scans.
\n        ref_date = pd.Timestamp(as_on_date).date() if as_on_date else date.today()
\n        for e in events:
\n            e["days_since"] = (ref_date - pd.Timestamp(e["event_date"]).date()).days
\n
\n        return events
\n
\n    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
\n        rows = self._get_universe()
\n        if not rows:
\n            logger.warning(
\n                "No symbols found in universe (mcap %.0f-%.0f Cr)",
\n                self.min_mcap,
\n                self.max_mcap,
\n            )
\n            return pd.DataFrame()
\n
\n        _sector_map: dict[str, str] = {}
\n        try:
\n            val_db = self._db_path("valuation")
\n            with sqlite3.connect(val_db) as _sc:
\n                _sec_rows = _sc.execute(
\n                    """
\n                    SELECT f.symbol, f.sector
\n                    FROM fundamentals f
\n                    INNER JOIN (
\n                        SELECT symbol, MAX(date) as max_date
\n                        FROM fundamentals
\n                        WHERE sector IS NOT NULL
\n                        GROUP BY symbol
\n                    ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
\n                    WHERE f.sector IS NOT NULL
\n                    """
\n                ).fetchall()
\n                _sector_map = {r[0].strip(): r[1] for r in _sec_rows}
\n        except (sqlite3.Error, OSError) as exc:
\n            logger.warning("Could not load sector map for Wyckoff scan: %s", exc)
\n
\n        if as_on_date is None:
\n            as_on_date = date.today().isoformat()
\n
\n        ref_date = pd.Timestamp(as_on_date)
\n        min_date = f"{(ref_date - pd.Timedelta(days=self.lookback_days)):%Y-%m-%d}"
\n
\n        # Single bulk load replaces per-symbol sqlite connections. Restrict the
\n        # query to the already-filtered universe so small universes (e.g. mcap
\n        # or MF-held filters) don't load the entire market (~1841 symbols).
\n        symbols = [row[0].strip() for row in rows]
\n        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date, symbols=symbols)
\n
\n        # Seed the current-snapshot market cap per universe symbol — the
\n        # "current mcap" numerator used by `_get_historical_mcap` (price-ratio
\n        # adjustment), sourced from the universe itself (no extra queries).
\n        self._current_mcap_map = {
\n            row[0].strip(): float(row[1])
\n            for row in rows
\n            if row[1] is not None and float(row[1]) > 0
\n        }
\n
\n        # Normalisation range for the Spring mcap factor: (min, max) of
\n        # ln(current_mcap) over the universe. Point-in-time safe — built only
\n        # from the scan-date snapshot, never future data. A degenerate range
\n        # (all mcaps identical) yields norm 0 downstream.
\n        _logs = [
\n            math.log(mc)
\n            for mc in self._current_mcap_map.values()
\n            if mc is not None and mc > 0
\n        ]
\n        self._mcap_log_range = (min(_logs), max(_logs)) if _logs else None
\n
\n        candidates: list[dict] = []
\n
\n        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
\n            symbol = symbol.strip()
\n
\n            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
\n            if len(tech) < max(55, int(self.lookback_days * 0.6) + 5):
\n                continue
\n
\n            # 13-col schema is always produced: rows_for_symbol() (via the bulk
\n            # loader COLUMNS_13) and _get_tech_data() both return 13 columns.
\n            df = pd.DataFrame(
\n                tech,
\n                columns=[
\n                    "date",
\n                    "open",
\n                    "high",
\n                    "low",
\n                    "close",
\n                    "volume",
\n                    "delivery",
\n                    "delivery_pct",
\n                    "swing_low",
\n                    # The following four columns are loaded for compatibility
\n                    # with the shared bulk-loader schema but are NOT used by
\n                    # Wyckoff detection logic.
\n                    "nifty_outperformance_score",
\n                    "sma_50",
\n                    "high_52w",
\n                    "low_52w",
\n                ],
\n            )
\n            df["date"] = pd.to_datetime(df["date"])
\n            df = df.sort_values("date").reset_index(drop=True)
\n
\n            if len(df) < max(55, int(self.lookback_days * 0.6) + 5):
\n                continue
\n
\n            events = self._detect_events(df, symbol=symbol, as_on_date=as_on_date)
\n            if not events:
\n                continue
\n
\n            def _event_score(e: dict) -> float:
\n                recency_penalty = e.get("days_since", 0) / 3.0
\n                return e["phase_pct"] - recency_penalty + e["quality"] * 0.1
\n
\n            best = max(events, key=_event_score)
\n            days_since = best.get("days_since", 0)
\n
\n            candidates.append(  # noqa: PG-APPEND
\n                {
\n                    "symbol": symbol,
\n                    "sector": _sector_map.get(symbol, "Unknown"),
\n                    "market_cap_cr": round(mcap / 1e7, 1),
\n                    "wyckoff_event": best["event"],
\n                    "phase": best["phase"],
\n                    "phase_complete_pct": best["phase_pct"],
\n                    "event_date": best["event_date"],
\n                    "event_delivery_pct": round(best["del_pct"], 1),
\n                    "vol_ratio": round(best["vol_ratio"], 1),
\n                    "event_quality": best["quality"],
\n                    "range_low_90": round(best["range_low_90"], 2),
\n                    "range_high_90": round(best["range_high_90"], 2),
\n                    "close": round(best["close"], 2),
\n                    "days_since_event": days_since,
\n                    "spring_score": best.get("spring_score"),
\n                    "grade": best.get("grade"),
\n                    "lower_wick_ratio": best.get("lower_wick_ratio"),
\n                    "close_location": best.get("close_location"),
\n                    "grab_depth_pct": best.get("grab_depth_pct"),
\n                    "equal_low_zone": best.get("equal_low_zone", False),
\n                    "two_candle_confirm": best.get("two_candle_confirm", False),
\n                }
\n            )
\n
\n        float_fields = [
\n            "market_cap_cr",
\n            "event_delivery_pct",
\n            "vol_ratio",
\n            "event_quality",
\n            "range_low_90",
\n            "range_high_90",
\n            "close",
\n            "spring_score",
\n            "lower_wick_ratio",
\n            "close_location",
\n            "grab_depth_pct",
\n        ]
\n        for c in candidates:
\n            for f in float_fields:
\n                if f in c:
\n                    c[f] = self._sanitize_float(c[f])
\n
\n        candidates.sort(
\n            key=lambda x: (
\n                x.get("phase_complete_pct") or 0,
\n                x.get("event_quality") or 0,
\n            ),
\n            reverse=True,
\n        )
\n        logger.info("Wyckoff scan complete: %d candidates found", len(candidates))
\n        return pd.DataFrame(candidates)

    def _calculate_dynamic_thresholds(self, df: pd.DataFrame, symbol: str, abs_i: int) -> dict:
        \
\\Calculate
dynamic
thresholds
based
on
historical
data
to
replace
hardcoded
values\\\
        # Get historical data up to current point (avoiding look-ahead bias)
        historical_df = df.iloc[:abs_i+1] if abs_i < len(df) else df

        if len(historical_df) < 20:  # Need minimum data for meaningful statistics
            return self._get_default_thresholds()

        # Calculate dynamic thresholds
        thresholds = {}

        # Volume threshold: use volume percentile instead of fixed 1.8
        volume_series = historical_df['volume']
        if len(volume_series) >= 10:
            volume_median = volume_series.median()
            volume_80th = volume_series.quantile(0.8)
            # Dynamic multiplier: if volume is spiking, use higher threshold
            if volume_median > 0:
                vol_ratio_80th = volume_80th / volume_median
                thresholds['volume_multiplier'] = max(1.5, min(3.0, vol_ratio_80th * 1.2))
            else:
                thresholds['volume_multiplier'] = 1.8  # fallback
        else:
            thresholds['volume_multiplier'] = 1.8  # fallback

        # Price threshold: use ATR-based percentage instead of fixed 0.35
        if 'high' in historical_df.columns and 'low' in historical_df.columns and len(historical_df) >= 10:
            historical_df = historical_df.copy()
            historical_df['tr'] = np.maximum(
                historical_df['high'] - historical_df['low'],
                np.maximum(
                    abs(historical_df['high'] - historical_df['close'].shift(1)),
                    abs(historical_df['low'] - historical_df['close'].shift(1))
                )
            )
            atr = historical_df['tr'].rolling(window=min(14, len(historical_df)), min_periods=1).mean().iloc[-1]
            avg_price = (historical_df['high'].iloc[-1] + historical_df['low'].iloc[-1] + historical_df['close'].iloc[-1]) / 3
            if avg_price > 0:
                atr_percentage = atr / avg_price
                thresholds['price_threshold_pct'] = max(0.1, min(0.5, atr_percentage * 2))  # Adaptive based on volatility
            else:
                thresholds['price_threshold_pct'] = 0.35  # fallback
        else:
            thresholds['price_threshold_pct'] = 0.35  # fallback

        # Delivery threshold: use historical delivery percentiles instead of fixed 40
        delivery_series = historical_df['delivery_pct']
        if len(delivery_series) >= 10:
            delivery_median = delivery_series.median()
            delivery_80th = delivery_series.quantile(0.8)
            if delivery_median > 0:
                thresholds['delivery_threshold'] = max(20, min(60, delivery_80th * 0.8))  # Adaptive
            else:
                thresholds['delivery_threshold'] = 40.0  # fallback
        else:
            thresholds['delivery_threshold'] = 40.0  # fallback

        # Range threshold: use historical range behavior instead of fixed 1.15
        range_series = (historical_df['high'] - historical_df['low'])
        if len(range_series) >= 10:
            range_median = range_series.median()
            range_80th = range_series.quantile(0.8)
            if range_median > 0:
                range_ratio = range_80th / range_median
                thresholds['range_multiplier'] = max(1.0, min(1.5, 1.0 + (range_ratio - 1) * 0.5))  # Adaptive
            else:
                thresholds['range_multiplier'] = 1.15  # fallback
        else:
            thresholds['range_multiplier'] = 1.15  # fallback

        # SOS-specific thresholds
        thresholds['sos_range_pct'] = max(0.3, min(0.6, 0.45 + (thresholds['price_threshold_pct'] - 0.35) * 0.5))  # Adaptive around 0.45
        thresholds['sos_volume_multiplier'] = max(1.0, min(2.0, thresholds['volume_multiplier'] * 0.8))  # Adaptive around 1.2
        thresholds['sos_delivery_multiplier'] = max(0.8, min(1.5, thresholds['delivery_threshold'] / 40.0))  # Adaptive around 1.0

        return thresholds

    def _get_default_thresholds(self) -> dict:
        \
\\Return
default
thresholds
when
insufficient
data
for
dynamic
calculation\\\
        return {
            'volume_multiplier': 1.8,
            'price_threshold_pct': 0.35,
            'delivery_threshold': 40.0,
            'range_multiplier': 1.15,
            'sos_range_pct': 0.45,
            'sos_volume_multiplier': 1.2,
            'sos_delivery_multiplier': 1.0,
            'spring_undercut_pct': 0.01
        }
