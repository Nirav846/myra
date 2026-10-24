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


class _ColumnarWindow:
    """Per-column arrays over one symbol slice, materialized per request.

    The scan needs a small DataFrame per symbol, but it used to build it the
    long way round: ``rows_for_symbol`` projected twelve columns into a tuple
    list (with a NaN->None sweep), ``pd.DataFrame`` re-inferred every column
    from Python objects, and the date strings were parsed and sorted per
    symbol. One pass over the slice replaces all of it.

    ``from_frames`` concatenates each column once and parses the date column a
    single time, so a request is twelve slice views and one small frame.

    Columns missing from a frame are padded with ``None`` for that frame only:
    every consumer indexes by position, so a dropped column would shift the
    rest. This mirrors ``rows_for_symbol``'s ``_pad_rows`` fallback, which pads
    the same columns to the same twelve-value shape.

    Rows keep snapshot order. ``load_ohlcv_for_universe`` emits ``(symbol,
    date)`` ordered frames, but a caller's frame may still be unsorted, so the
    accessor sorts when (and only when) the dates are not already monotonic.

    The window is private to one scan: ``frame`` returns a fresh DataFrame whose
    blocks are copies, so a caller mutating what it receives cannot reach the
    arrays or another symbol.
    """

    __slots__ = ("columns", "arrays", "spans")

    def __init__(
        self,
        columns: tuple[str, ...],
        arrays: dict[str, np.ndarray],
        spans: dict[str, tuple[int, int]],
    ):
        self.columns = columns
        self.arrays = arrays
        self.spans = spans

    @classmethod
    def from_frames(
        cls, frames: dict[str, pd.DataFrame], columns: tuple[str, ...]
    ) -> "_ColumnarWindow":
        spans: dict[str, tuple[int, int]] = {}
        lengths: list[int] = []
        kept: list[pd.DataFrame] = []
        position = 0
        for symbol, df in frames.items():
            length = len(df)
            # An empty symbol is absent from the window, exactly like a symbol
            # the snapshot never returned.
            if not length:
                continue
            # ``span`` binary-searches the symbol's date run, so the run must be
            # chronological. The loader already orders by (symbol, date); this
            # only pays for a frame that arrived unsorted.
            if "date" in df.columns and not df["date"].is_monotonic_increasing:
                df = df.sort_values("date").reset_index(drop=True)
            spans[symbol] = (position, position + length)
            position += length
            lengths.append(length)
            kept.append(df)

        if not kept:
            return cls(
                columns,
                {column: np.empty(0, dtype=object) for column in columns},
                spans,
            )

        # One concat beats 12 x N column extractions (22,968 numpy conversions
        # on the live universe) and keeps the tuple path's dtypes: a column
        # missing from *some* frames becomes float64 NaN, which is exactly what
        # ``_pad_rows`` + ``pd.DataFrame`` infer from a mix of floats and None.
        wide = pd.concat(kept, ignore_index=True)
        arrays: dict[str, np.ndarray] = {}
        for column in columns:
            if column in wide.columns:
                values = wide[column].to_numpy()
            else:
                # Absent from every frame: an all-None object array, matching
                # the tuple path's fully-padded column.
                values = np.full(position, None, dtype=object)
            # Parsed once here instead of once per symbol in the scan.
            if column == "date":
                values = pd.to_datetime(values).to_numpy()
            arrays[column] = values
        return cls(columns, arrays, spans)

    def span(
        self, symbol: str, min_date: str, max_date: str | None
    ) -> tuple[int, int] | None:
        """Row bounds for *symbol* clipped to ``[min_date, max_date]``."""
        bounds = self.spans.get(symbol)
        if bounds is None:
            return None
        start, end = bounds
        dates = self.arrays["date"]
        # A date the parser cannot read leaves the window unclipped, which is
        # what the previous string comparison did for a malformed bound.
        try:
            lower = np.datetime64(min_date, "ns") if min_date else None
            upper = np.datetime64(max_date, "ns") if max_date else None
        except ValueError:
            return start, end
        if lower is not None and lower > dates[start]:
            start += int(np.searchsorted(dates[start:end], lower, "left"))
        if upper is not None and end > start and upper < dates[end - 1]:
            end = start + int(np.searchsorted(dates[start:end], upper, "right"))
        if end <= start:
            return None
        return start, end

    def frame(
        self, symbol: str, min_date: str, max_date: str | None
    ) -> pd.DataFrame | None:
        """The symbol's rows as a new DataFrame, clipped like ``rows_for_symbol``."""
        bounds = self.span(symbol, min_date, max_date)
        if bounds is None:
            return None
        start, end = bounds
        return pd.DataFrame(
            {column: self.arrays[column][start:end] for column in self.columns}
        )


class FloatExhaustionScanner:
    _bulk_data = None
    _columnar = None
    _BULK_COLUMNS = COLUMNS_12

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

    def _get_tech_df(
        self, symbol: str, min_date: str, max_date: str | None = None
    ) -> pd.DataFrame:
        """The symbol's bars as a chronologically ordered frame.

        This is the attribute the web layer's progress wrapper patches, so it
        keeps the ``(symbol, min_date, max_date)`` call shape. The window path
        slices pre-built arrays; without a window the frame is built from the
        rows accessor exactly as the scan used to build it (including the
        nine-column shape of an old schema, padded back to twelve).
        """
        columns = list(self._BULK_COLUMNS)
        max_date = max_date or date.today().isoformat()
        if self._columnar is not None:
            df = self._columnar.frame(symbol, min_date, max_date)
            if df is None:
                return pd.DataFrame(columns=columns)
            if not df["date"].is_monotonic_increasing:
                df = df.sort_values("date").reset_index(drop=True)
            return df

        rows = self._get_tech_data(symbol, min_date, max_date)
        if not rows:
            return pd.DataFrame(columns=columns)
        if len(rows[0]) >= 12:
            df = pd.DataFrame(rows, columns=columns)
        else:
            df = pd.DataFrame(rows, columns=columns[:9])
            df["sma_50"] = None
            df["high_52w"] = None
            df["low_52w"] = None
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    @staticmethod
    def _sanitize_float(value):
        return sanitize_float(value)

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
                _sec_rows = _sc.execute("""
                    SELECT f.symbol, f.sector
                    FROM fundamentals f
                    INNER JOIN (
                        SELECT symbol, MAX(date) as max_date
                        FROM fundamentals
                        WHERE sector IS NOT NULL
                        GROUP BY symbol
                    ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                    WHERE f.sector IS NOT NULL
                    """).fetchall()
                _sector_map = {r[0].strip(): r[1] for r in _sec_rows}
        except Exception:
            pass

        if as_on_date is None:
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = f"{(ref_date - pd.Timedelta(days=self.window_days + 10)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections. Only the
        # universe this loop visits is materialized; the loop strips before
        # lookup, so the selector does too.
        bulk = load_ohlcv_for_universe(
            min_date, as_on_date, [r[0].strip() for r in rows]
        )
        if bulk:
            # One columnar window per scan; the dictionary only ever held this
            # same slice, so releasing it keeps a single representation live.
            self._columnar = _ColumnarWindow.from_frames(bulk, self._BULK_COLUMNS)
            self._bulk_data = None
        else:
            # No rows at all: keep the empty mapping so the rows accessor still
            # answers "nothing" instead of falling through to the SQL path.
            self._columnar = None
            self._bulk_data = bulk

        min_rows = max(14, int(self.window_days * 0.7) + 2)
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

            df = self._get_tech_df(symbol, min_date, as_on_date)
            if len(df) < min_rows:
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

            candidates.append(  # noqa: PG-APPEND
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
