"""
Method 2 Validation — Kaushik BOH 40% Recovery
================================================
Full validation matrix for Method 2 (40% recovery threshold, targets 15-20%)
using the same PIT universe and rigor as Method 1.

Covers:
  1. Full validation matrix (fire freq, win rate, avg PnL, cap rate)
  2. Direct Method 1 vs Method 2 comparison
  3. Stress-period analysis (2018 IL&FS, 2020 COVID)
  4. Corrected full-population stop-loss analysis

Usage:
    python tools/method2_validation.py --window train --pit
    python tools/method2_validation.py --window holdout --pit
    python tools/method2_validation.py --window all --pit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

import pandas as pd

sys.path.insert(0, os.getcwd())

from myra_app.backtest_engine import (  # noqa: E402
    BacktestConfig,
    compute_mae_mfe,
    retrospective_stop_sweep,
    run_backtest,
)
from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "myra_reports")

# ── Variant definitions ────────────────────────────────────────────────────
METHOD2_VARIANTS: list[dict] = [
    {"signal": "kaushik_boh_m2", "average_in": False, "label": "m2_single"},
    {"signal": "kaushik_boh_m2", "average_in": True, "label": "m2_avg"},
]

# Method 1 for comparison (same variants already accepted)
METHOD1_VARIANTS: list[dict] = [
    {"signal": "kaushik_boh_m1", "average_in": False, "label": "m1_single"},
    {"signal": "kaushik_boh_m1", "average_in": True, "label": "m1_avg"},
]

RANDOM_VARIANTS: list[dict] = [
    {"signal": "random", "average_in": False, "label": "random_single"},
    {"signal": "random", "average_in": True, "label": "random_avg"},
]


def _open_conn():
    import sqlite3
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


def _load_pit_universe():
    pit_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        ".backtest_scratch",
        "_phase4_mcap_top500_corrected.json",
    )
    if not os.path.exists(pit_path):
        print(f"ERROR: PIT universe file not found at {pit_path}")
        return {}
    with open(pit_path) as f:
        data = json.load(f)
    print(f"Loaded PIT universe: {len(data)} dates")
    return data


def _compute_summary(label: str, trades: pd.DataFrame, window: str) -> dict:
    """Compute key summary metrics for a config."""
    if trades.empty:
        return {"variant": label, "window": window, "n_trades": 0}

    winners = trades[trades["pnl_net"] > 0]
    losers = trades[trades["pnl_net"] <= 0]
    n = len(trades)

    # Cap rate: trades exiting via 252d cap
    cap_trades = trades[trades["exit_reason"].str.contains("252d_cap|pt_252d", na=False)]
    cap_rate = len(cap_trades) / n * 100 if n else 0

    # Win rate
    win_rate = len(winners) / n * 100 if n else 0

    # Avg PnL
    avg_pnl = trades["pnl_net"].mean()

    # Avg PnL for winners and losers separately
    avg_winners = winners["pnl_net"].mean() if not winners.empty else 0
    avg_losers = losers["pnl_net"].mean() if not losers.empty else 0

    # Profit factor
    gross_profit = winners["pnl_net"].sum() if not winners.empty else 0
    gross_loss = abs(losers["pnl_net"].sum()) if not losers.empty else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "variant": label,
        "window": window,
        "n_trades": n,
        "win_rate": f"{win_rate:.1f}%",
        "avg_pnl": f"{avg_pnl:.0f}",
        "avg_winner": f"{avg_winners:.0f}",
        "avg_loser": f"{avg_losers:.0f}",
        "cap_rate": f"{cap_rate:.1f}%",
        "profit_factor": f"{profit_factor:.2f}",
    }


def _run_configs(
    conn, variants, targets, window, pit_universe, label_prefix=""
) -> tuple[list[pd.DataFrame], list[dict], list[pd.DataFrame]]:
    """Run all variant × target combos, return (all_trades, summary_rows, sweep_results)."""
    all_trades = []
    summary_rows = []
    sweep_results = []

    for target_pct in targets:
        target_label = f"{target_pct*100:.1f}%"
        for variant in variants:
            label = f"{label_prefix}{variant['label']}"
            cfg = BacktestConfig(
                signal=variant["signal"],
                exit_mode="profit_target",
                profit_target_pct=target_pct,
                window=window,
                average_in=variant["average_in"],
                max_tranches=3,
                recovery_mult=1.40 if variant["signal"] == "kaushik_boh_m2" else 1.20,
            )

            print(f"  Running {label} (pt={target_label})...", end=" ", flush=True)
            result = run_backtest(conn, cfg, pit_universe=pit_universe)
            trades = result.trades
            print(f"{len(trades)} trades")

            if trades.empty:
                continue

            enriched = compute_mae_mfe(trades, conn)
            enriched["variant"] = label
            enriched["target"] = target_label
            all_trades.append(enriched)

            summary_rows.append(_compute_summary(label, trades, window))

            sweep = retrospective_stop_sweep(enriched)
            sweep["variant"] = label
            sweep["target"] = target_label
            sweep_results.append(sweep)

    return all_trades, summary_rows, sweep_results


def _print_summary_table(summary_rows: list[dict], title: str) -> None:
    if not summary_rows:
        return
    print(f"\n{'=' * 100}")
    print(title)
    print("=" * 100)
    df = pd.DataFrame(summary_rows)
    print(df.to_string(index=False))


def _print_comparison_table(m1_summaries: list[dict], m2_summaries: list[dict]) -> None:
    """Side-by-side M1 vs M2 comparison."""
    print(f"\n{'=' * 120}")
    print("METHOD 1 vs METHOD 2 — Direct Comparison")
    print("=" * 120)
    print(f"{'Config':<35s} {'M1 Trades':>10s} {'M1 WinR':>8s} {'M1 AvgPnL':>10s} {'M1 CapR':>8s}  |  "
          f"{'M2 Trades':>10s} {'M2 WinR':>8s} {'M2 AvgPnL':>10s} {'M2 CapR':>8s}")
    print("-" * 120)

    # Group by (variant_base, target)
    m1_map = {}
    for s in m1_summaries:
        key = s["variant"].replace("m1_", "") + "|" + s.get("window", "")
        m1_map[key] = s
    m2_map = {}
    for s in m2_summaries:
        key = s["variant"].replace("m2_", "") + "|" + s.get("window", "")
        m2_map[key] = s

    for key in m1_map:
        m1 = m1_map[key]
        m2 = m2_map.get(key, {})
        base_variant = key.split("|")[0]
        m1_trades = str(m1.get('n_trades', 0))
        m2_trades = str(m2.get('n_trades', 0))
        print(f"{base_variant:<35s} "
              f"{m1_trades:>10s} {m1.get('win_rate', 'N/A'):>8s} "
              f"{m1.get('avg_pnl', 'N/A'):>10s} {m1.get('cap_rate', 'N/A'):>8s}  |  "
              f"{m2_trades:>10s} {m2.get('win_rate', 'N/A'):>8s} "
              f"{m2.get('avg_pnl', 'N/A'):>10s} {m2.get('cap_rate', 'N/A'):>8s}")


def _stress_period_analysis(
    conn, variants, targets, pit_universe
) -> None:
    """Analyze Method 1 vs Method 2 during stress periods."""
    # Stress period definitions
    stress_periods = {
        "ILFS_NBFC_2018": ("2018-08-01", "2019-03-31"),
        "COVID_2020": ("2020-01-15", "2020-06-30"),
    }

    print(f"\n{'=' * 100}")
    print("STRESS-PERIOD ANALYSIS — Method 1 vs Method 2")
    print("=" * 100)
    print("Periods: IL&FS/NBFC crisis (Aug 2018 - Mar 2019), COVID crash (Jan 15 - Jun 2020)")
    print("Showing trades with entry_date within each stress window")
    print()

    for period_name, (start, end) in stress_periods.items():
        print(f"\n--- {period_name} ({start} to {end}) ---")

        for method_label, method_variants in [("Method 1", METHOD1_VARIANTS), ("Method 2", METHOD2_VARIANTS)]:
            for variant in method_variants:
                for target_pct in targets:
                    target_label = f"{target_pct*100:.1f}%"
                    cfg = BacktestConfig(
                        signal=variant["signal"],
                        exit_mode="profit_target",
                        profit_target_pct=target_pct,
                        window="all",
                        average_in=variant["average_in"],
                        max_tranches=3,
                        recovery_mult=1.40 if variant["signal"] == "kaushik_boh_m2" else 1.20,
                    )

                    result = run_backtest(conn, cfg, pit_universe=pit_universe)
                    trades = result.trades
                    if trades.empty:
                        continue

                    # Filter to trades entered during stress period
                    stress_trades = trades[
                        (trades["entry_date"] >= start) & (trades["entry_date"] <= end)
                    ]

                    if stress_trades.empty:
                        continue

                    n = len(stress_trades)
                    winners = stress_trades[stress_trades["pnl_net"] > 0]
                    win_rate = len(winners) / n * 100 if n else 0
                    avg_pnl = stress_trades["pnl_net"].mean()

                    cap_trades = stress_trades[
                        stress_trades["exit_reason"].str.contains("252d_cap|pt_252d", na=False)
                    ]
                    cap_rate = len(cap_trades) / n * 100 if n else 0

                    print(
                        f"  {method_label} {variant['label']} pt={target_label}: "
                        f"{n} trades | win {win_rate:.0f}% | avg ₹{avg_pnl:.0f} | cap {cap_rate:.0f}%"
                    )


def _stop_loss_analysis(
    all_trades: list[pd.DataFrame], sweep_results: list[pd.DataFrame]
) -> None:
    """Print corrected full-population stop-loss analysis."""
    if not all_trades or not sweep_results:
        return

    combined_trades = pd.concat(all_trades, ignore_index=True)
    combined_sweep = pd.concat(sweep_results, ignore_index=True)

    print(f"\n{'=' * 100}")
    print("STOP-LOSS SENSITIVITY — FULL POPULATION (Method 2)")
    print("=" * 100)

    key_levels = [10.0, 15.0, 20.0, 25.0]

    for target_label in combined_sweep["target"].unique():
        print(f"\n  Target: {target_label}")
        for variant_label in combined_sweep["variant"].unique():
            vsweep = combined_sweep[
                (combined_sweep["variant"] == variant_label)
                & (combined_sweep["target"] == target_label)
            ]
            vtrades = combined_trades[
                (combined_trades["variant"] == variant_label)
                & (combined_trades["target"] == target_label)
            ]
            if vtrades.empty:
                continue

            baseline_avg_pnl = float(vtrades["pnl_net"].mean())
            n_total = len(vtrades)

            print(f"\n    {variant_label} ({n_total} trades):")
            print(f"    {'Stop%':>6s}  {'Stopped':>8s}  {'Killed':>8s}  {'Reduced':>8s}  {'AvgPnL(Rs)':>12s}  {'vs Base':>10s}")
            print(f"    {'------':>6s}  {'--------':>8s}  {'--------':>8s}  {'--------':>8s}  {'----------':>12s}  {'---------':>10s}")

            print(
                f"    {'None':>6s}  {0:>8d}  {0:>8d}  {n_total:>8d}  "
                f"{baseline_avg_pnl:>+12.0f}  {'(baseline)':>10s}"
            )

            for lvl in key_levels:
                row = vsweep[vsweep["stop_pct"] == lvl]
                if row.empty:
                    continue
                r = row.iloc[0]
                avg_pnl = r["avg_pnl_full_pop"]
                delta = avg_pnl - baseline_avg_pnl
                print(
                    f"    {r['stop_pct']:>5.0f}%  {int(r['n_stopped']):>8d}  "
                    f"{int(r['winners_killed']):>8d}  {int(r['losers_stopped']):>8d}  "
                    f"{avg_pnl:>+12.0f}  {delta:>+10.0f}"
                )


def main():
    parser = argparse.ArgumentParser(description="Method 2 Validation")
    parser.add_argument("--window", default="train", choices=["train", "holdout", "all"])
    parser.add_argument("--pit", action="store_true", help="Use PIT universe")
    parser.add_argument("--targets", default="15,17.5,20", help="Profit targets as pct (e.g. 15,17.5,20)")
    parser.add_argument("--skip-stress", action="store_true", help="Skip stress-period analysis")
    parser.add_argument("--skip-random", action="store_true", help="Skip random control")
    args = parser.parse_args()

    conn = _open_conn()
    os.makedirs(REPORTS_DIR, exist_ok=True)

    pit_universe = None
    if args.pit:
        pit_universe = _load_pit_universe()
        if not pit_universe:
            print("ERROR: Could not load PIT universe. Aborting.")
            return

    targets = [float(t) / 100.0 for t in args.targets.split(",")]

    print("=" * 100)
    print("METHOD 2 VALIDATION — Kaushik BOH 40% Recovery")
    print("=" * 100)
    print(f"Window: {args.window}  |  Targets: {[f'{t*100:.1f}%' for t in targets]}")
    print(f"Variants: {[v['label'] for v in METHOD2_VARIANTS]}")
    universe_desc = "PIT (Phase 4 corrected)" if args.pit else "FULL"
    print(f"Universe: {universe_desc}")
    print()

    # ── 1. Full validation matrix ───────────────────────────────────────────
    print("\n" + "=" * 100)
    print("PART 1: FULL VALIDATION MATRIX — Method 2")
    print("=" * 100)

    m2_trades, m2_summaries, m2_sweeps = _run_configs(
        conn, METHOD2_VARIANTS, targets, args.window, pit_universe
    )
    _print_summary_table(m2_summaries, "Method 2 Summary")

    # ── 2. Fresh random control ─────────────────────────────────────────────
    if not args.skip_random:
        print("\n" + "=" * 100)
        print("PART 2: FRESH RANDOM CONTROL (same universe, same targets)")
        print("=" * 100)

        # Random control: use 7.5% target (standard for comparison)
        rnd_trades, rnd_summaries, _ = _run_configs(
            conn, RANDOM_VARIANTS, [0.075], args.window, pit_universe, label_prefix=""
        )
        _print_summary_table(rnd_summaries, "Random Control Summary (7.5% target)")

    # ── 3. Method 1 vs Method 2 comparison ──────────────────────────────────
    print("\n" + "=" * 100)
    print("PART 3: METHOD 1 vs METHOD 2 COMPARISON")
    print("=" * 100)

    # Run Method 1 on same universe for fair comparison
    m1_trades, m1_summaries, _ = _run_configs(
        conn, METHOD1_VARIANTS, targets, args.window, pit_universe
    )
    _print_comparison_table(m1_summaries, m2_summaries)

    # ── 4. Stress-period analysis ───────────────────────────────────────────
    if not args.skip_stress and args.window in ("train", "all"):
        _stress_period_analysis(conn, METHOD1_VARIANTS + METHOD2_VARIANTS, targets, pit_universe)

    # ── 5. Stop-loss analysis on Method 2 ───────────────────────────────────
    if m2_trades and m2_sweeps:
        _stop_loss_analysis(m2_trades, m2_sweeps)

    # ── Save results ────────────────────────────────────────────────────────
    if m2_trades:
        combined = pd.concat(m2_trades, ignore_index=True)
        combined.to_csv(os.path.join(REPORTS_DIR, "method2_trades.csv"), index=False)
    if m2_sweeps:
        combined_sweep = pd.concat(m2_sweeps, ignore_index=True)
        combined_sweep.to_csv(os.path.join(REPORTS_DIR, "method2_stop_sensitivity.csv"), index=False)

    print(f"\nResults saved to {REPORTS_DIR}/")
    conn.close()


if __name__ == "__main__":
    main()
