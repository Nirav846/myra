"""
Backtest harness for Bottom Hunter overlays.

Generates Bottom Hunter candidates across a historical sample, then applies
two overlays independently and measures forward returns:
  1. Delivery-spike confirmation
  2. Second-chance re-entry (break-and-recover pattern from Climax Accumulation)

Usage:
    python tools/_test_bh_overlays.py
"""

import logging
import math
import os
import random
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("myra.bh_overlay_backtest")

random.seed(42)
np.random.seed(42)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

LOOKBACK_DAYS = 260          # same as BottomHunter default
UNIVERSE_SAMPLE = 400        # target symbol count (300-500)
N_DATES = 10                 # historical scan dates
SPIKE_CONFIRM_WINDOW = 10    # max days to look for delivery spike
SECOND_CHANCE_WINDOW = 20    # max days to wait for break-and-recover
FWD_WINDOWS = [20, 40]       # forward return windows (trading days)
SPIKE_DELIVERY_MULT = 1.3    # delivery_pct >= 1.3x rolling 50d mean
SPIKE_PRICE_PERCENTILE = 0.6  # close in upper 60% of day's range


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def get_available_dates() -> list[str]:
    """Return all distinct dates in technical_data, sorted ascending."""
    if not os.path.exists(TECH_DB):
        return []
    with sqlite3.connect(TECH_DB) as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM technical_data ORDER BY date ASC"
        ).fetchall()
    return [r[0] for r in rows]


def get_universe_symbols() -> list[str]:
    """Return symbols from valuation DB with market_cap in 200-50000 Cr."""
    if not os.path.exists(VAL_DB):
        return []
    with sqlite3.connect(VAL_DB) as conn:
        rows = conn.execute(
            """
            SELECT f.symbol
            FROM fundamentals f
            INNER JOIN (
                SELECT symbol, MAX(date) as max_date
                FROM fundamentals
                WHERE COALESCE(market_cap, 0) > 0
                GROUP BY symbol
            ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
            WHERE COALESCE(f.market_cap, 0) / 1e7 BETWEEN 200 AND 50000
            """
        ).fetchall()
    return [r[0].strip() for r in rows]


def fetch_symbol_df(symbol: str, min_date: str, max_date: str) -> pd.DataFrame | None:
    """Fetch OHLCV+delivery data for a symbol. Returns None on failure."""
    if not os.path.exists(TECH_DB):
        return None
    with sqlite3.connect(TECH_DB) as conn:
        try:
            rows = conn.execute(
                """
                SELECT date, open, high, low, close, volume, delivery, delivery_pct
                FROM technical_data
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
                """,
                (symbol, min_date, max_date),
            ).fetchall()
        except sqlite3.OperationalError:
            return None
    if not rows or len(rows) < 30:
        return None
    df = pd.DataFrame(
        rows,
        columns=["date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"],
    )
    for col in ["open", "high", "low", "close", "volume", "delivery", "delivery_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df if len(df) >= 30 else None


# ---------------------------------------------------------------------------
# Bottom Hunter signal generation (simplified from scanner class)
# ---------------------------------------------------------------------------

def detect_bh_signals(df: pd.DataFrame) -> dict | None:
    """Detect a Bottom Hunter signal at the last row of df.

    Returns a dict with signal metadata or None if not triggered.
    Reimplements the core logic of BottomHunter.scan for a single symbol.
    """
    if len(df) < max(30, int(LOOKBACK_DAYS * 0.6) + 5):
        return None

    last_20 = df.tail(20)
    if len(last_20) < 20:
        return None

    # Delivery absorption
    up_days = last_20[last_20["close"] > last_20["open"]]
    down_days = last_20[last_20["close"] < last_20["open"]]
    up_del_avg = float(up_days["delivery_pct"].mean()) if len(up_days) > 0 else 0.0
    down_del_avg = float(down_days["delivery_pct"].mean()) if len(down_days) > 0 else 0.0
    delivery_absorption = up_del_avg - down_del_avg

    if delivery_absorption < 5.0:
        return None

    # ADTV
    adtv_cr = float(((last_20["close"] * last_20["volume"]) / 1e7).mean())
    if adtv_cr < 1.0:
        return None

    # 52-week high/low
    latest_close = float(last_20["close"].iloc[-1])
    high_52w = float(df["high"].max())
    low_52w = float(df["low"].min())
    pct_above_52w_low = ((latest_close - low_52w) / low_52w * 100) if low_52w > 0 else 0.0

    # ATR
    prev_close = last_20["close"].shift(1)
    tr = pd.concat([
        last_20["high"] - last_20["low"],
        (last_20["high"] - prev_close).abs(),
        (last_20["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_20d = float(tr.mean())
    swing_low_20d = float(last_20["low"].min())

    sl_base = latest_close - 2 * atr_20d
    if swing_low_20d < latest_close and swing_low_20d > sl_base:
        sl_price = swing_low_20d - atr_20d * 0.5
    else:
        sl_price = sl_base

    return {
        "close": latest_close,
        "sl_price": sl_price,
        "swing_low_20d": swing_low_20d,
        "delivery_absorption": delivery_absorption,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_above_52w_low": pct_above_52w_low,
    }


# ---------------------------------------------------------------------------
# Overlay logic
# ---------------------------------------------------------------------------

def apply_delivery_spike(
    df: pd.DataFrame, signal_idx: int, window: int = SPIKE_CONFIRM_WINDOW
) -> tuple[bool, float | None]:
    """Look forward from signal_idx for a delivery-spike confirmation day.

    Conditions:
      - delivery_pct >= 1.3 * rolling_50d_mean(delivery_pct) as of that day
      - close is in the upper 60% of the day's high-low range

    Returns (confirmed, confirmation_close).
    """
    n = len(df)
    # Pre-compute rolling 50d mean of delivery_pct
    if "delivery_pct" not in df.columns:
        return False, None
    roll50 = df["delivery_pct"].rolling(window=50, min_periods=20).mean()

    for offset in range(1, window + 1):
        idx = signal_idx + offset
        if idx >= n:
            break
        row = df.iloc[idx]
        dp = row.get("delivery_pct")
        r50 = roll50.iloc[idx]
        if dp is None or r50 is None or math.isnan(dp) or math.isnan(r50):
            continue
        if dp < SPIKE_DELIVERY_MULT * r50:
            continue
        # Check price in upper 60% of range
        h, l = row["high"], row["low"]
        if h == l:
            continue
        price_pos = (row["close"] - l) / (h - l)
        if price_pos >= (1.0 - SPIKE_PRICE_PERCENTILE):  # upper 60%
            return True, float(row["close"])
    return False, None


def apply_second_chance(
    df: pd.DataFrame,
    signal_idx: int,
    ref_level: float,
    window: int = SECOND_CHANCE_WINDOW,
) -> bool:
    """Check for break-and-recover (second-chance) pattern after signal.

    Price dips below ref_level (swing_low_20d or sl_price) at some point
    after signal_idx and then recovers back above it within `window` days.
    Pattern mirrors Climax Accumulation's second-chance detection.
    """
    n = len(df)
    for offset in range(1, window + 1):
        idx = signal_idx + offset
        if idx >= n:
            break
        if df.iloc[idx]["low"] < ref_level:
            # Found a break — now check for recovery within remaining window
            for j in range(offset, window + 1):
                jdx = signal_idx + j
                if jdx >= n:
                    break
                if df.iloc[jdx]["high"] >= ref_level:
                    return True
            return False  # broke but never recovered
    return False  # never broke


# ---------------------------------------------------------------------------
# Forward return measurement
# ---------------------------------------------------------------------------

def forward_return(df: pd.DataFrame, signal_idx: int, entry_price: float, days: int) -> float | None:
    """Compute forward return from entry_price over `days` trading days."""
    target_idx = signal_idx + days
    if target_idx >= len(df):
        return None
    exit_price = float(df.iloc[target_idx]["close"])
    if entry_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("=== Bottom Hunter Overlay Backtest ===")

    # Phase 0: Data freshness
    dates = get_available_dates()
    if not dates:
        logger.error("No dates in technical_data — DB may be missing or empty.")
        sys.exit(1)
    latest = dates[-1]
    logger.info("Latest date in DB: %s  |  Total dates: %d", latest, len(dates))

    # Sample historical dates evenly
    # Exclude the most recent LOOKBACK_DAYS+40 trading days (need forward data)
    safe_end = max(0, len(dates) - LOOKBACK_DAYS - 40)
    if safe_end < N_DATES:
        logger.warning(
            "Not enough historical dates for %d scan points. Using %d dates.",
            N_DATES, safe_end,
        )
    step = max(1, safe_end // N_DATES) if safe_end > 0 else 1
    scan_dates = dates[::step][:N_DATES]
    # Ensure we don't use dates too close to the end (need 40d forward data)
    scan_dates = [d for d in scan_dates if dates.index(d) <= len(dates) - 41]
    if not scan_dates:
        scan_dates = [dates[len(dates) // 2]]  # fallback: mid-point
    logger.info("Scan dates (%d): %s", len(scan_dates), scan_dates)

    # Sample symbols
    all_symbols = get_universe_symbols()
    if not all_symbols:
        logger.error("No symbols in valuation universe.")
        sys.exit(1)
    sampled = random.sample(all_symbols, min(UNIVERSE_SAMPLE, len(all_symbols)))
    logger.info("Sampled %d / %d symbols", len(sampled), len(all_symbols))

    # Cache DataFrames per symbol
    df_cache: dict[str, pd.DataFrame] = {}

    def get_df(symbol: str) -> pd.DataFrame | None:
        if symbol in df_cache:
            return df_cache[symbol]
        # Fetch wide enough window for lookback + forward
        earliest = scan_dates[0] if scan_dates else dates[0]
        earliest_idx = max(0, dates.index(earliest) - LOOKBACK_DAYS - 60) if earliest in dates else 0
        min_date = dates[earliest_idx]
        max_date = dates[-1]
        df = fetch_symbol_df(symbol, min_date, max_date)
        if df is not None:
            df_cache[symbol] = df
        return df

    # --- Collect all candidates across all dates ---
    all_candidates: list[dict] = []

    for scan_date in scan_dates:
        logger.info("Scanning date: %s", scan_date)
        count = 0
        for symbol in sampled:
            df = get_df(symbol)
            if df is None:
                continue
            # Find the row index for scan_date
            mask = df["date"] == pd.Timestamp(scan_date)
            if mask.sum() == 0:
                continue
            scan_idx = int(df[mask].index[0])

            # Need enough data before scan_idx for lookback
            if scan_idx < max(30, int(LOOKBACK_DAYS * 0.6) + 5):
                continue

            # Build a view from start to scan_idx (inclusive) for signal detection
            df_view = df.iloc[: scan_idx + 1].copy().reset_index(drop=True)
            sig = detect_bh_signals(df_view)
            if sig is None:
                continue

            # Apply overlays using the full DataFrame from scan_idx
            spike_confirmed, spike_close = apply_delivery_spike(df, scan_idx)
            second_chance = apply_second_chance(
                df, scan_idx, sig["swing_low_20d"]
            )

            all_candidates.append({
                "symbol": symbol,
                "scan_date": scan_date,
                "signal_idx": scan_idx,
                "entry_close": sig["close"],
                "sl_price": sig["sl_price"],
                "swing_low_20d": sig["swing_low_20d"],
                "delivery_absorption": sig["delivery_absorption"],
                "spike_confirmed": spike_confirmed,
                "spike_close": spike_close,
                "second_chance": second_chance,
            })
            count += 1
        logger.info("  >> %d candidates on %s", count, scan_date)

    if not all_candidates:
        logger.error("No candidates generated. Cannot run backtest.")
        sys.exit(1)

    logger.info("Total candidates: %d", len(all_candidates))

    # --- Compute forward returns for each subset ---
    subsets = {
        "Baseline": [c for c in all_candidates],
        "Delivery-Spike": [c for c in all_candidates if c["spike_confirmed"]],
        "Second-Chance": [c for c in all_candidates if c["second_chance"]],
        "Spike + Second-Chance": [
            c for c in all_candidates if c["spike_confirmed"] and c["second_chance"]
        ],
    }

    results: dict[str, dict] = {}

    for subset_name, candidates in subsets.items():
        if not candidates:
            results[subset_name] = {"count": 0}
            continue

        returns_20d: list[float] = []
        returns_40d: list[float] = []

        for c in candidates:
            symbol = c["symbol"]
            scan_date = c["scan_date"]
            df = get_df(symbol)
            if df is None:
                continue
            mask = df["date"] == pd.Timestamp(scan_date)
            if mask.sum() == 0:
                continue
            signal_idx = int(df[mask].index[0])

            # Determine entry price
            if subset_name == "Delivery-Spike" and c["spike_close"] is not None:
                entry = c["spike_close"]
                # For spike subset, entry is at the confirmation close
                # The confirmation day is signal_idx + some offset
                # We need to find that index in the full df
                # Re-run the spike detection to find the actual confirmation index
                _, conf_close = apply_delivery_spike(df, signal_idx)
                if conf_close is not None:
                    # Find the confirmation index
                    roll50 = df["delivery_pct"].rolling(50, min_periods=20).mean()
                    for offset in range(1, SPIKE_CONFIRM_WINDOW + 1):
                        idx = signal_idx + offset
                        if idx >= len(df):
                            break
                        row = df.iloc[idx]
                        dp = row.get("delivery_pct")
                        r50 = roll50.iloc[idx]
                        if dp is None or r50 is None or math.isnan(dp) or math.isnan(r50):
                            continue
                        if dp < SPIKE_DELIVERY_MULT * r50:
                            continue
                        h, l = row["high"], row["low"]
                        if h == l:
                            continue
                        price_pos = (row["close"] - l) / (h - l)
                        if price_pos >= (1.0 - SPIKE_PRICE_PERCENTILE):
                            entry = float(row["close"])
                            # Measure forward returns from the confirmation index
                            fwd20 = forward_return(df, idx, entry, 20)
                            fwd40 = forward_return(df, idx, entry, 40)
                            if fwd20 is not None:
                                returns_20d.append(fwd20)
                            if fwd40 is not None:
                                returns_40d.append(fwd40)
                            break
                    continue  # already measured from confirmation index

            if subset_name == "Second-Chance":
                # For second-chance, entry is at the recovery close (lowest point)
                # Find the break point and then the lowest close before recovery
                ref_level = c["swing_low_20d"]
                entry_found = False
                for offset in range(1, SECOND_CHANCE_WINDOW + 1):
                    idx = signal_idx + offset
                    if idx >= len(df):
                        break
                    if df.iloc[idx]["low"] < ref_level:
                        # Found break — find the lowest close before recovery
                        lowest_close = float(df.iloc[idx]["close"])
                        lowest_idx = idx
                        for j in range(offset + 1, SECOND_CHANCE_WINDOW + 1):
                            jdx = signal_idx + j
                            if jdx >= len(df):
                                break
                            if df.iloc[jdx]["close"] < lowest_close:
                                lowest_close = float(df.iloc[jdx]["close"])
                                lowest_idx = jdx
                            if df.iloc[jdx]["high"] >= ref_level:
                                # Recovery — use the lowest close as entry
                                fwd20 = forward_return(df, lowest_idx, lowest_close, 20)
                                fwd40 = forward_return(df, lowest_idx, lowest_close, 40)
                                if fwd20 is not None:
                                    returns_20d.append(fwd20)
                                if fwd40 is not None:
                                    returns_40d.append(fwd40)
                                entry_found = True
                                break
                        if entry_found:
                            break
                if entry_found:
                    continue
                # fallback to baseline entry
                entry = c["entry_close"]
            else:
                entry = c["entry_close"]

            # Default: measure from signal_idx
            if subset_name not in ("Delivery-Spike", "Second-Chance"):
                fwd20 = forward_return(df, signal_idx, entry, 20)
                fwd40 = forward_return(df, signal_idx, entry, 40)
                if fwd20 is not None:
                    returns_20d.append(fwd20)
                if fwd40 is not None:
                    returns_40d.append(fwd40)

        if returns_20d or returns_40d:
            r20 = np.array(returns_20d) if returns_20d else np.array([])
            r40 = np.array(returns_40d) if returns_40d else np.array([])
            results[subset_name] = {
                "count": len(candidates),
                "measured_20d": len(r20),
                "measured_40d": len(r40),
                "mean_20d": float(r20.mean()) if len(r20) > 0 else None,
                "median_20d": float(np.median(r20)) if len(r20) > 0 else None,
                "win_20d": float((r20 > 0).mean()) if len(r20) > 0 else None,
                "mean_40d": float(r40.mean()) if len(r40) > 0 else None,
                "median_40d": float(np.median(r40)) if len(r40) > 0 else None,
                "win_40d": float((r40 > 0).mean()) if len(r40) > 0 else None,
            }
        else:
            results[subset_name] = {"count": len(candidates)}

    # --- Output comparison table ---
    print("\n" + "=" * 80)
    print("BOTTOM HUNTER OVERLAY BACKTEST - COMPARISON TABLE")
    print("=" * 80)
    header = f"{'Subset':<24} {'Count':>6} {'Meas20':>7} {'Mean20':>8} {'Med20':>8} {'Win%20':>7} {'Meas40':>7} {'Mean40':>8} {'Med40':>8} {'Win%40':>7}"
    print(header)
    print("-" * 80)

    for subset_name in ["Baseline", "Delivery-Spike", "Second-Chance", "Spike + Second-Chance"]:
        r = results.get(subset_name, {})
        count = r.get("count", 0)
        m20 = r.get("measured_20d", 0)
        m40 = r.get("measured_40d", 0)
        mean20 = f"{r['mean_20d'] * 100:+.1f}%" if r.get("mean_20d") is not None else "N/A"
        med20 = f"{r['median_20d'] * 100:+.1f}%" if r.get("median_20d") is not None else "N/A"
        w20 = f"{r['win_20d'] * 100:.1f}%" if r.get("win_20d") is not None else "N/A"
        mean40 = f"{r['mean_40d'] * 100:+.1f}%" if r.get("mean_40d") is not None else "N/A"
        med40 = f"{r['median_40d'] * 100:+.1f}%" if r.get("median_40d") is not None else "N/A"
        w40 = f"{r['win_40d'] * 100:.1f}%" if r.get("win_40d") is not None else "N/A"
        print(
            f"{subset_name:<24} {count:>6} {m20:>7} {mean20:>8} {med20:>8} {w20:>7} "
            f"{m40:>7} {mean40:>8} {med40:>8} {w40:>7}"
        )

    print("-" * 80)

    # --- Decision gate ---
    print("\n=== DECISION GATE ===")
    baseline = results.get("Baseline", {})
    baseline_mean20 = baseline.get("mean_20d")
    baseline_mean40 = baseline.get("mean_40d")
    baseline_win20 = baseline.get("win_20d")
    baseline_win40 = baseline.get("win_40d")

    for overlay_name in ["Delivery-Spike", "Second-Chance", "Spike + Second-Chance"]:
        ov = results.get(overlay_name, {})
        if ov.get("count", 0) == 0:
            print(f"  {overlay_name}: SKIP — no candidates")
            continue

        ov_mean20 = ov.get("mean_20d")
        ov_mean40 = ov.get("mean_40d")
        ov_win20 = ov.get("win_20d")
        ov_win40 = ov.get("win_40d")

        passes = False
        reason = ""

        if baseline_mean40 is not None and ov_mean40 is not None:
            if ov_mean40 > baseline_mean40:
                passes = True
                reason = f"mean 40d {ov_mean40*100:+.1f}% > baseline {baseline_mean40*100:+.1f}%"
            else:
                reason = f"mean 40d {ov_mean40*100:+.1f}% <= baseline {baseline_mean40*100:+.1f}%"

        if baseline_win40 is not None and ov_win40 is not None:
            if ov_win40 >= baseline_win40 + 0.05:
                passes = True
                reason += f" | win% 40d {ov_win40*100:.1f}% >= baseline+5% ({baseline_win40*100:.1f}%)"
            elif not passes:
                reason += f" | win% 40d {ov_win40*100:.1f}% < baseline+5% ({baseline_win40*100:.1f}%)"

        verdict = "PASS" if passes else "FAIL"
        print(f"  {overlay_name}: [{verdict}] {reason}")

    print("=" * 80)
    print("Done.")


if __name__ == "__main__":
    main()
