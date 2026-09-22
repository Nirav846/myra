"""Detect Float Turnover signals — vectorized.

Float Turnover(N) on day t = SUM(delivery[t-N+1..t] * close[t-N+1..t])
                               / free_float_market_cap * 100

This mirrors the DAR formula used by darvas_box_scanner / accumulation_base_scanner:

    dar = delivery * close / ff_mcap * 100   (daily)
    turnover(N) = rolling_sum(delivery*close, N) / ff_mcap * 100

Free-float fallback (same as scanners):
    ff_mcap = free_float_market_cap if >0 else market_cap * free_float_pct/100

Crossing logic: first day turnover crosses *above* threshold (avoid
re-triggering while it stays above), identical to the accumulation
streak "first crossing" pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FloatTurnoverSignal:
    date: str  # YYYY-MM-DD of the crossing day
    turnover_pct: float  # Float Turnover(N) on that day
    window: int
    threshold: float
    ff_mcap: float


def compute_float_turnover_series(
    df: pd.DataFrame,
    ff_mcap: float,
    window: int,
) -> np.ndarray:
    """Return float array of length len(df) with turnover values.

    First window-1 entries are NaN (insufficient history).
    Returns array of NaN if ff_mcap <=0 or df too short.
    """
    n = len(df)
    if n == 0 or ff_mcap is None or ff_mcap <= 0 or window <= 0:
        return np.full(n, np.nan, dtype=float)
    if n < window:
        return np.full(n, np.nan, dtype=float)
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    delivery = pd.to_numeric(df["delivery"], errors="coerce").to_numpy(dtype=float)
    delivery = np.nan_to_num(delivery, nan=0.0)
    close = np.nan_to_num(close, nan=0.0)
    # delivery * close can be 0 for NaN cases; close already 0 for NaN
    dv = delivery * close
    # rolling sum N — use pandas for correctness with NaN handling
    # min_periods=window ensures first window-1 are NaN
    s = pd.Series(dv)
    rolling = s.rolling(window, min_periods=window).sum().to_numpy(dtype=float)
    turnover = rolling / ff_mcap * 100.0
    return turnover


def detect_float_turnover(
    df: pd.DataFrame,
    ff_mcap: float,
    window: int = 20,
    threshold: float = 10.0,
) -> list[FloatTurnoverSignal]:
    """Vectorized Float Turnover crossing detection.

    Parameters
    ----------
    df : DataFrame with columns date, close, delivery (sorted ascending by date)
    ff_mcap : free-float market cap (raw rupees, not Cr), post-fallback
    window : lookback N (days)
    threshold : X% threshold for crossing

    Returns list of first-crossing signals, sorted by date ascending.
    """
    n = len(df)
    if n < window or ff_mcap is None or ff_mcap <= 0:
        return []
    dates = df["date"].astype(str).str[:10].to_numpy()
    turnover = compute_float_turnover_series(df, ff_mcap, window)
    # first-crossing mask: turnover >= threshold and previous < threshold
    # treat NaN previous as -inf (so first valid window can trigger if >= threshold)
    prev = np.roll(turnover, 1)
    prev[0] = np.nan
    # previous NaN -> treat as below threshold
    prev_below = np.isnan(prev) | (prev < threshold)
    curr_above = turnover >= threshold
    # also need turnover not NaN
    valid = ~np.isnan(turnover)
    mask = valid & curr_above & prev_below
    # Also need contiguous? No, simple crossing.
    signals: list[FloatTurnoverSignal] = []
    for idx in np.where(mask)[0]:
        # ensure close/delivery valid on that day? Turnover already requires df length, but check close>0
        try:
            c = float(df.iloc[idx]["close"])
        except Exception:
            c = 0
        if c <= 0:
            continue
        signals.append(
            FloatTurnoverSignal(
                date=str(dates[idx]),
                turnover_pct=round(float(turnover[idx]), 4),
                window=window,
                threshold=threshold,
                ff_mcap=float(ff_mcap),
            )
        )
    return signals


def detect_float_turnover_multi(
    df: pd.DataFrame,
    ff_mcap: float,
    windows: list[int],
    thresholds: list[float],
) -> dict[tuple[int, float], list[FloatTurnoverSignal]]:
    """Detect across multiple (window, threshold) combos — for grid search."""
    out: dict[tuple[int, float], list[FloatTurnoverSignal]] = {}
    for w in windows:
        for t in thresholds:
            out[(w, t)] = detect_float_turnover(df, ff_mcap, w, t)
    return out
