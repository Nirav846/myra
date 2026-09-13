"""
Modified Super Breakout — full 6-configuration backtest matrix.

2 ranking variants × 3 exit variants, train + holdout each.
Plus matched random control, fire frequency, MAE/MFE analysis.

Usage:
    python tools/backtest_super_breakout.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from myra_app.backtest_engine import (
    HOLDOUT_END,
    TRAIN_END,
    TRAIN_START_PRICE_ONLY,
    BacktestResult,
    compute_mae_mfe,
    total_round_trip_costs,
    POSITION_VALUE_INR,
)
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.strategies.super_breakout import (
    SuperBreakoutConfig,
    run_super_breakout_backtest,
)


def _load_pit_universe() -> dict[str, list[str]]:
    pit_path = (
        Path(__file__).resolve().parents[1]
        / ".backtest_scratch"
        / "_phase4_mcap_top500_corrected.json"
    )
    with open(pit_path) as f:
        return json.load(f)


def _open_conn() -> sqlite3.Connection:
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    meta_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
    inst_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["institutional"])
    cal_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["calendar"])
    conn = sqlite3.connect(tech_db)
    conn.execute(f"ATTACH DATABASE '{meta_db}' AS meta")
    conn.execute(f"ATTACH DATABASE '{inst_db}' AS inst")
    if os.path.exists(cal_db):
        conn.execute(f"ATTACH DATABASE '{cal_db}' AS cal")
    return conn


def fire_frequency(
    conn: sqlite3.Connection,
    pit_universe: dict[str, list[str]],
    ranking: str,
) -> dict:
    """Count signals by day, by symbol, total unique dates, etc."""
    from myra_app.strategies.super_breakout import detect_super_breakout_signals
    from myra_app.backtest_engine import _trading_days, _preload_universe_by_date

    start = TRAIN_START_PRICE_ONLY
    end = HOLDOUT_END
    trading_days = _trading_days(conn, start, end)
    universe_by_date = _preload_universe_by_date(
        conn, trading_days, pit_universe=pit_universe
    )
    t0 = time.time()
    raw_signals = detect_super_breakout_signals(conn, universe_by_date, ranking)
    elapsed = time.time() - t0

    # Filter to PIT universe
    total_signal_days = 0
    total_entries = 0
    unique_symbols = set()
    daily_counts = []
    for day_iso, candidates in raw_signals.items():
        eligible = set(universe_by_date.get(day_iso, []))
        filtered = [(s, sc) for s, sc in candidates if s in eligible]
        if filtered:
            total_signal_days += 1
            total_entries += len(filtered)
            unique_symbols.update(s for s, _ in filtered)
            daily_counts.append((day_iso, len(filtered)))

    # Train / holdout split
    train_days = sum(1 for d, _ in daily_counts if d <= TRAIN_END)
    holdout_days = sum(1 for d, _ in daily_counts if d > TRAIN_END)
    train_entries = sum(
        n for d, n in daily_counts if d <= TRAIN_END
    )
    holdout_entries = sum(
        n for d, n in daily_counts if d > TRAIN_END
    )

    return {
        "ranking": ranking,
        "elapsed_seconds": round(elapsed, 1),
        "total_signal_days": total_signal_days,
        "total_entries": total_entries,
        "unique_symbols": len(unique_symbols),
        "train_signal_days": train_days,
        "train_entries": train_entries,
        "holdout_signal_days": holdout_days,
        "holdout_entries": holdout_entries,
        "avg_entries_per_signal_day": (
            round(total_entries / total_signal_days, 2)
            if total_signal_days > 0
            else 0
        ),
    }


def run_matrix(conn: sqlite3.Connection, pit_universe: dict[str, list[str]]):
    """Run the full 6-configuration matrix."""
    rankings = ["self_relative", "raw_delivery"]
    exits = ["fixed", "ma_trail", "atr_trail"]
    windows = ["train", "holdout"]

    results = {}
    for ranking in rankings:
        for exit_mode in exits:
            for window in windows:
                label = f"{ranking}_{exit_mode}_{window}"
                cfg = SuperBreakoutConfig(
                    ranking=ranking,
                    exit_mode=exit_mode,
                    window=window,
                )
                t0 = time.time()
                res = run_super_breakout_backtest(conn, cfg, pit_universe)
                elapsed = time.time() - t0
                results[label] = {
                    "config": {
                        "ranking": ranking,
                        "exit_mode": exit_mode,
                        "window": window,
                    },
                    "summary": res.summary,
                    "n_trades": len(res.trades),
                    "elapsed": round(elapsed, 1),
                }
                print(
                    f"  {label:50s}  trades={res.summary['total_trades']:4d}  "
                    f"win={res.summary['win_rate']:.1%}  "
                    f"avg_pnl={res.summary['avg_return']:+.0f}  "
                    f"total={res.summary['total_pnl_net']:+.0f}  "
                    f"({elapsed:.1f}s)"
                )
    return results


def random_control(
    conn: sqlite3.Connection,
    pit_universe: dict[str, list[str]],
    n_sims: int = 50,
):
    """Generate matched random control for the same universe/window."""
    from myra_app.strategies.random_control import RandomControl
    from myra_app.backtest_engine import BacktestConfig, run_backtest

    results = []
    for seed in range(n_sims):
        cfg = BacktestConfig(
            signal="random",
            exit_mode="fixed",
            fixed_hold_days=60,
            window="all",
        )
        # Override the random signal factory to use this seed
        from myra_app import backtest_engine
        orig = backtest_engine.SIGNAL_REGISTRY.get("random")
        backtest_engine.SIGNAL_REGISTRY["random"] = lambda: RandomControl(seed)
        try:
            res = run_backtest(conn, cfg, pit_universe=pit_universe)
            results.append(res.summary)
        finally:
            if orig is not None:
                backtest_engine.SIGNAL_REGISTRY["random"] = orig

    # Aggregate
    avg_pnl = np.mean([r["avg_return"] for r in results])
    avg_win = np.mean([r["win_rate"] for r in results])
    avg_total = np.mean([r["total_pnl_net"] for r in results])
    return {
        "n_sims": n_sims,
        "avg_pnl_per_trade": round(float(avg_pnl), 2),
        "avg_win_rate": round(float(avg_win), 4),
        "avg_total_pnl": round(float(avg_total), 2),
        "pnl_5th_pct": round(float(np.percentile([r["total_pnl_net"] for r in results], 5)), 2),
        "pnl_95th_pct": round(float(np.percentile([r["total_pnl_net"] for r in results], 95)), 2),
    }


def hand_verify_example(
    conn: sqlite3.Connection,
    pit_universe: dict[str, list[str]],
):
    """Trace one signal end-to-end for manual verification."""
    from myra_app.strategies.super_breakout import detect_super_breakout_signals
    from myra_app.backtest_engine import _trading_days, _preload_universe_by_date

    # Use train window
    trading_days = _trading_days(conn, TRAIN_START_PRICE_ONLY, TRAIN_END)
    universe_by_date = _preload_universe_by_date(
        conn, trading_days, pit_universe=pit_universe
    )
    signals = detect_super_breakout_signals(conn, universe_by_date, "self_relative")

    # Find first signal day
    for day_iso in sorted(signals.keys()):
        candidates = signals[day_iso]
        eligible = set(universe_by_date.get(day_iso, []))
        filtered = [(s, sc) for s, sc in candidates if s in eligible]
        if not filtered:
            continue
        # Pick highest-scored
        best_sym, best_score = max(filtered, key=lambda x: x[1])

        # Load full data for this symbol around the signal date
        day_idx = trading_days.index(day_iso) if day_iso in trading_days else -1
        if day_idx < 200:
            continue

        lookback_start = trading_days[max(0, day_idx - 250)]
        fwd_end = trading_days[min(len(trading_days) - 1, day_idx + 260)]

        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, "
            "COALESCE(delivery_qty, delivery, 0) as del_qty "
            "FROM technical_data "
            "WHERE symbol=? AND date BETWEEN ? AND ? "
            "ORDER BY date",
            (best_sym, lookback_start, fwd_end),
        ).fetchall()

        if not rows:
            continue

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "del_qty"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # Find the signal row
        signal_row = df[df["date"] == pd.Timestamp(day_iso)]
        if signal_row.empty:
            continue
        sig_idx = signal_row.index[0]

        # Compute SMAs at signal
        c = df["close"].values
        sma5 = pd.Series(c).rolling(5).mean().values
        sma10 = pd.Series(c).rolling(10).mean().values
        sma15 = pd.Series(c).rolling(15).mean().values
        sma50 = pd.Series(c).rolling(50).mean().values
        sma200 = pd.Series(c).rolling(200).mean().values

        sig_close = c[sig_idx]
        sig_prev_close = c[sig_idx - 1] if sig_idx > 0 else None

        print("=" * 70)
        print(f"HAND-VERIFIED SIGNAL: {best_sym} on {day_iso}")
        print(f"  Score (self-relative delivery): {best_score:.4f}")
        print(f"  Close: {sig_close:.2f}")
        print(f"  Prev close: {sig_prev_close:.2f}")
        print(f"  SMA(5):  {sma5[sig_idx]:.2f}  |  close > SMA5? {sig_close > sma5[sig_idx]}")
        print(f"  SMA(10): {sma10[sig_idx]:.2f}  |  close > SMA10? {sig_close > sma10[sig_idx]}")
        print(f"  SMA(15): {sma15[sig_idx]:.2f}  |  close > SMA15? {sig_close > sma15[sig_idx]}")
        print(f"  SMA(50): {sma50[sig_idx]:.2f}  |  close < SMA50? {sig_close < sma50[sig_idx]} (context)")
        print(f"  SMA(200): {sma200[sig_idx]:.2f}  |  close < SMA200? {sig_close < sma200[sig_idx]}")
        print(f"  SMA(50) < SMA(200)? {sma50[sig_idx] < sma200[sig_idx]}")
        print(f"  Prev close < SMA50(prev)? {sig_prev_close < sma50[sig_idx - 1] if sig_idx > 0 else 'N/A'}")
        print(f"  Trigger check: prev < SMA50 AND today >= SMA50? {sig_prev_close < sma50[sig_idx - 1] if sig_idx > 0 else False} AND {sig_close >= sma50[sig_idx]}")

        # Delivery data
        dval = sig_close * df["del_qty"].iloc[sig_idx]
        baseline = df["close"].iloc[max(0, sig_idx - 20):sig_idx].values * df["del_qty"].iloc[max(0, sig_idx - 20):sig_idx].values
        baseline_mean = baseline.mean() if len(baseline) > 0 else 0
        print(f"  Delivery value (close * del_qty): {dval:.0f}")
        print(f"  20d baseline mean: {baseline_mean:.0f}")
        print(f"  Self-relative score: {dval / baseline_mean if baseline_mean > 0 else 0:.4f}")

        # Forward performance
        fwd = df.iloc[sig_idx:sig_idx + 60]
        if len(fwd) > 1:
            max_fwd = fwd["close"].max()
            min_fwd = fwd["close"].min()
            ret_5d = (fwd["close"].iloc[min(5, len(fwd) - 1)] / sig_close - 1) * 100
            ret_20d = (fwd["close"].iloc[min(20, len(fwd) - 1)] / sig_close - 1) * 100
            ret_60d = (fwd["close"].iloc[-1] / sig_close - 1) * 100
            print(f"\n  Forward performance (from signal):")
            print(f"    5d return:  {ret_5d:+.2f}%")
            print(f"    20d return: {ret_20d:+.2f}%")
            print(f"    60d return: {ret_60d:+.2f}%")
            print(f"    Max in 60d: {max_fwd:.2f} ({(max_fwd/sig_close - 1)*100:+.2f}%)")
            print(f"    Min in 60d: {min_fwd:.2f} ({(min_fwd/sig_close - 1)*100:+.2f}%)")

        print("=" * 70)
        return {
            "symbol": best_sym,
            "date": day_iso,
            "score": best_score,
            "close": sig_close,
        }

    print("No qualifying signal found for hand verification.")
    return None


def main():
    print("Loading PIT universe...")
    pit_universe = _load_pit_universe()
    print(f"  {len(pit_universe)} dates loaded")

    print("Opening database connection...")
    conn = _open_conn()

    try:
        # ── Fire frequency ──
        print("\n" + "=" * 70)
        print("FIRE FREQUENCY ANALYSIS")
        print("=" * 70)
        for ranking in ["self_relative", "raw_delivery"]:
            freq = fire_frequency(conn, pit_universe, ranking)
            print(f"\n  {ranking}:")
            for k, v in freq.items():
                if k != "ranking":
                    print(f"    {k}: {v}")

        # ── Full matrix ──
        print("\n" + "=" * 70)
        print("FULL 6-CONFIGURATION MATRIX")
        print("=" * 70)
        matrix_results = run_matrix(conn, pit_universe)

        # ── Random control ──
        print("\n" + "=" * 70)
        print("MATCHED RANDOM CONTROL (50 simulations)")
        print("=" * 70)
        rc = random_control(conn, pit_universe, n_sims=50)
        for k, v in rc.items():
            print(f"  {k}: {v}")

        # ── Side-by-side comparison ──
        print("\n" + "=" * 70)
        print("SIDE-BY-SIDE: STRATEGY vs RANDOM CONTROL")
        print("=" * 70)
        for key, res in matrix_results.items():
            s = res["summary"]
            print(
                f"  {key:50s}  avg_pnl={s['avg_return']:+.0f}  "
                f"total={s['total_pnl_net']:+.0f}  "
                f"win_rate={s['win_rate']:.1%}"
            )
        print(f"  {'random_control':50s}  avg_pnl={rc['avg_pnl_per_trade']:+.0f}  "
              f"total={rc['avg_total_pnl']:+.0f}  "
              f"win_rate={rc['avg_win_rate']:.1%}")

        # ── Hand verification ──
        print("\n" + "=" * 70)
        print("HAND-VERIFIED EXAMPLE")
        print("=" * 70)
        hand_verify_example(conn, pit_universe)

        # ── MAE/MFE for best config ──
        print("\n" + "=" * 70)
        print("MAE/MFE ANALYSIS (best config by total_pnl)")
        print("=" * 70)
        best_key = max(
            matrix_results.keys(),
            key=lambda k: matrix_results[k]["summary"]["total_pnl_net"],
        )
        best_cfg_dict = matrix_results[best_key]["config"]
        best_cfg = SuperBreakoutConfig(
            ranking=best_cfg_dict["ranking"],
            exit_mode=best_cfg_dict["exit_mode"],
            window="all",
        )
        best_res = run_super_breakout_backtest(conn, best_cfg, pit_universe)
        if not best_res.trades.empty:
            enriched = compute_mae_mfe(best_res.trades, conn)
            if not enriched.empty:
                print(f"\n  Best config: {best_key}")
                print(f"  Trades: {len(enriched)}")
                print(f"  MAE% mean: {enriched['mae_pct'].mean():.2f}%")
                print(f"  MAE% median: {enriched['mae_pct'].median():.2f}%")
                print(f"  MFE% mean: {enriched['mfe_pct'].mean():.2f}%")
                print(f"  MFE% median: {enriched['mfe_pct'].median():.2f}%")
                # P&L by exit reason
                print(f"\n  Exit reason breakdown:")
                for reason, group in enriched.groupby("exit_reason"):
                    print(
                        f"    {reason:30s}  n={len(group):4d}  "
                        f"avg_pnl={group['pnl_net'].mean():+.0f}  "
                        f"win_rate={(group['pnl_net'] > 0).mean():.1%}"
                    )

        # Save results
        output_path = (
            Path(__file__).resolve().parents[1]
            / ".backtest_scratch"
            / "super_breakout_results.json"
        )
        with open(output_path, "w") as f:
            json.dump(
                {
                    "matrix": matrix_results,
                    "random_control": rc,
                },
                f,
                indent=2,
                default=str,
            )
        print(f"\nResults saved to {output_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
