"""
MAE/MFE Analysis — Method 1 Backtest Post-Hoc Diagnostics
==========================================================
Computes Maximum Adverse Excursion (MAE) and Maximum Favorable Excursion
(MFE) for every trade across Method 1 signal variants, then runs a
retroactive stop-loss sensitivity sweep.

Output
------
  myra_reports/mae_mfe_trades.csv      — per-trade MAE/MFE enrichment
  myra_reports/stop_sensitivity.csv     — stop-level sweep table
  stdout                                — formatted summary + key findings

How to run
----------
    python tools/mae_mfe_analysis.py                                          # all variants, 7.5%
    python tools/mae_mfe_analysis.py --variants base_single,base_avg          # shipped config only
    python tools/mae_mfe_analysis.py --targets 5,7.5,10                       # all three targets
    python tools/mae_mfe_analysis.py --window holdout --targets 5,7.5,10      # holdout x 3 targets
    python tools/mae_mfe_analysis.py --pit                                    # use PIT universe (required for backtests)
"""
from __future__ import annotations

import argparse
import os
import sqlite3
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

# ── Method 1 variant definitions ────────────────────────────────────────────
VARIANTS: list[dict] = [
    {"signal": "kaushik_boh_m1", "average_in": False, "label": "base_single"},
    {"signal": "kaushik_boh_m1", "average_in": True, "label": "base_avg"},
    {
        "signal": "kaushik_boh_m1_delivery",
        "average_in": False,
        "label": "delivery_single",
    },
    {"signal": "kaushik_boh_m1_delivery", "average_in": True, "label": "delivery_avg"},
    {
        "signal": "kaushik_boh_m1_delivery_filter",
        "average_in": False,
        "label": "filter_single",
    },
    {
        "signal": "kaushik_boh_m1_delivery_filter",
        "average_in": True,
        "label": "filter_avg",
    },
]
VARIANT_MAP = {v["label"]: v for v in VARIANTS}


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


def _load_pit_universe() -> dict[str, list[str]]:
    """Load PIT universe from Phase 4 corrected JSON file.

    IMPORTANT: mcap_rank_daily is a live-only, current-snapshot table.
    NEVER use it to approximate historical universe membership.
    Historical/backtest work always uses this offline PIT reconstruction.
    """
    import json

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
    print(f"Loaded PIT universe from {os.path.basename(pit_path)}: {len(data)} dates")
    return data


def _summary_row(label: str, trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"variant": label, "n_trades": 0}
    winners = trades[trades["pnl_net"] > 0]
    losers = trades[trades["pnl_net"] <= 0]
    return {
        "variant": label,
        "n_trades": len(trades),
        "win_rate": f"{len(winners) / len(trades) * 100:.1f}%",
        "avg_pnl": f"{trades['pnl_net'].mean():.0f}",
        "mae_median": f"{trades['mae_pct'].median():+.2f}%",
        "mae_p10": f"{trades['mae_pct'].quantile(0.10):+.2f}%",
        "mae_p90": f"{trades['mae_pct'].quantile(0.90):+.2f}%",
        "mfe_median": f"{trades['mfe_pct'].median():+.2f}%",
        "mfe_median_losers": (
            f"{losers['mfe_pct'].median():+.2f}%" if not losers.empty else "N/A"
        ),
        "n_losers_with_mfe_gt5": (
            int((losers["mfe_pct"] > 5.0).sum()) if not losers.empty else 0
        ),
    }


def _print_capped_loss_analysis(trades: pd.DataFrame, label: str) -> None:
    """Re-examine capped-loss trades: were they underwater the whole time?"""
    capped = trades[trades["exit_reason"].str.contains("252d_cap|pt_252d", na=False)]
    if capped.empty:
        print(f"  {label}: no capped-loss trades found")
        return

    print(f"\n  {label} — capped-loss trades ({len(capped)} total):")
    for _, row in capped.iterrows():
        final_ret = (row["exit_price"] / row["entry_price"] - 1) * 100
        timing = (
            "underwater"
            if abs(row["mae_pct"]) > abs(final_ret) * 0.9
            else "late_failure"
        )
        print(
            f"    {row['symbol']:12s}  entry={row['entry_price']:.2f}  "
            f"exit={row['exit_price']:.2f}  final={final_ret:+.1f}%  "
            f"MAE={row['mae_pct']:+.1f}%  timing={timing}"
        )


def run_analysis(args: argparse.Namespace) -> None:
    conn = _open_conn()
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # Load PIT universe if --pit specified
    pit_universe = None
    if args.pit:
        pit_universe = _load_pit_universe()
        if not pit_universe:
            print("ERROR: Could not load PIT universe. Aborting.")
            return

    # Resolve variants
    if args.variants:
        labels = [v.strip() for v in args.variants.split(",")]
        variants = [VARIANT_MAP[l] for l in labels if l in VARIANT_MAP]
        if not variants:
            print(f"No valid variants. Available: {list(VARIANT_MAP.keys())}")
            return
    else:
        variants = VARIANTS

    # Resolve targets (user enters percentages like "5,7.5,10", convert to decimal)
    targets = (
        [float(t) / 100.0 for t in args.targets.split(",")] if args.targets else [0.075]
    )

    all_trades: list[pd.DataFrame] = []
    sweep_results: list[pd.DataFrame] = []
    summary_rows: list[dict] = []

    print("=" * 80)
    print("MAE/MFE Analysis -- Method 1 Backtest Post-Hoc Diagnostics")
    print("=" * 80)
    print(f"Window: {args.window}  |  Targets: {[f'{t*100:.1f}%' for t in targets]}")
    print(f"Variants: {[v['label'] for v in variants]}")
    universe_desc = "PIT (Phase 4 corrected)" if args.pit else "FULL (no cap filter)"
    print(f"Universe: {universe_desc}")
    print(f"Stop sweep: {args.stop_start}%-{args.stop_end}% in {args.stop_step}% steps")
    print()

    for target_pct in targets:
        target_label = f"{target_pct*100:.1f}%"
        print(f"\n{'=' * 80}")
        print(f"  PROFIT TARGET: {target_label}")
        print(f"{'=' * 80}")

        for variant in variants:
            label = variant["label"]
            sig = variant["signal"]
            avg = variant["average_in"]

            cfg = BacktestConfig(
                signal=sig,
                exit_mode="profit_target",
                profit_target_pct=target_pct,
                window=args.window,
                average_in=avg,
                max_tranches=3,
            )

            print(f"  Running {label}...", end=" ", flush=True)
            result = run_backtest(conn, cfg, pit_universe=pit_universe)
            trades = result.trades
            print(f"{len(trades)} trades")

            if trades.empty:
                continue

            # Compute MAE/MFE
            enriched = compute_mae_mfe(trades, conn)
            enriched["variant"] = label
            enriched["target"] = target_label
            all_trades.append(enriched)

            # Stop sweep
            sweep = retrospective_stop_sweep(
                enriched,
                start_pct=args.stop_start,
                end_pct=args.stop_end,
                step_pct=args.stop_step,
            )
            sweep["variant"] = label
            sweep["target"] = target_label
            sweep_results.append(sweep)

            # Summary
            summary_rows.append(_summary_row(f"{label} (pt={target_label})", enriched))

            # Capped-loss timing analysis
            _print_capped_loss_analysis(enriched, f"{label} (pt={target_label})")

    conn.close()

    if not all_trades:
        print("\nNo trades found across any variant/target combination.")
        return

    # ── Combine and save ─────────────────────────────────────────────────────
    combined_trades = pd.concat(all_trades, ignore_index=True)
    combined_sweep = pd.concat(sweep_results, ignore_index=True)

    trades_path = os.path.join(REPORTS_DIR, "mae_mfe_trades.csv")
    sweep_path = os.path.join(REPORTS_DIR, "stop_sensitivity.csv")
    combined_trades.to_csv(trades_path, index=False)
    combined_sweep.to_csv(sweep_path, index=False)

    # ── Print summary table ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("MAE/MFE Summary by Variant × Target")
    print("=" * 80)
    summary_df = pd.DataFrame(summary_rows)
    print(summary_df.to_string(index=False))

    # ── Print stop-sensitivity table ─────────────────────────────────────────
    print("\n" + "=" * 80)
    print("Stop-Loss Sensitivity — FULL POPULATION (stopped trades replaced")
    print("with stop-level loss, averaged over ALL trades, same N as baseline)")
    print("=" * 80)
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

            # No-stop baseline: avg PnL across ALL trades (rupees)
            baseline_avg_pnl = float(vtrades["pnl_net"].mean())
            n_total = len(vtrades)

            print(f"\n    {variant_label} ({n_total} trades):")
            print(
                f"    {'Stop%':>6s}  {'Stopped':>8s}  {'Killed':>8s}  {'Reduced':>8s}  {'AvgPnL(Rs)':>12s}  {'vs Base':>10s}"
            )
            print(
                f"    {'------':>6s}  {'--------':>8s}  {'--------':>8s}  {'--------':>8s}  {'----------':>12s}  {'---------':>10s}"
            )

            # No-stop baseline row
            print(
                f"    {'None':>6s}  {0:>8d}  {0:>8d}  {n_total:>8d}  "
                f"{baseline_avg_pnl:>+12.0f}  {'(baseline)':>10s}"
            )

            # Stopped rows
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

    # ── Print MFE-for-losers analysis ────────────────────────────────────────
    print("\n" + "=" * 80)
    print("MFE for Losing Trades (how many were profitable at some point?)")
    print("=" * 80)
    for target_label in combined_trades["target"].unique():
        print(f"\n  Target: {target_label}")
        for variant_label in combined_trades["variant"].unique():
            vtrades = combined_trades[
                (combined_trades["variant"] == variant_label)
                & (combined_trades["target"] == target_label)
            ]
            losers = vtrades[vtrades["pnl_net"] <= 0]
            if losers.empty:
                continue
            profitable_at_some_point = (losers["mfe_pct"] > 0).sum()
            mfe_gt5 = (losers["mfe_pct"] > 5.0).sum()
            mfe_gt10 = (losers["mfe_pct"] > 10.0).sum()
            print(
                f"    {variant_label:25s}  {len(losers):>4d} losers  |  "
                f"{profitable_at_some_point:>3d} were +ve at peak ({profitable_at_some_point/len(losers)*100:.0f}%)  |  "
                f"{mfe_gt5:>3d} peaked >5%  |  {mfe_gt10:>3d} peaked >10%"
            )

    print(f"\nOutput saved to:")
    print(f"  {trades_path}")
    print(f"  {sweep_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MAE/MFE analysis for Method 1 backtest"
    )
    parser.add_argument(
        "--window",
        choices=["train", "holdout", "all"],
        default="all",
        help="Backtest window (default: all)",
    )
    parser.add_argument(
        "--targets",
        type=str,
        default=None,
        help="Comma-separated profit targets as percentages (e.g. '5,7.5,10'). Default: 7.5",
    )
    parser.add_argument(
        "--variants",
        type=str,
        default=None,
        help=f"Comma-separated variant labels. Available: {list(VARIANT_MAP.keys())}",
    )
    parser.add_argument(
        "--stop-start",
        type=float,
        default=2.0,
        help="Stop sweep start percentage (default: 2.0)",
    )
    parser.add_argument(
        "--stop-end",
        type=float,
        default=30.0,
        help="Stop sweep end percentage (default: 30.0)",
    )
    parser.add_argument(
        "--stop-step",
        type=float,
        default=1.0,
        help="Stop sweep step percentage (default: 1.0)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print config and exit")
    parser.add_argument(
        "--pit",
        action="store_true",
        help="Use PIT (point-in-time) universe from Phase 4 corrected JSON. "
        "Required for accurate historical backtest universe membership.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("Dry run — config:")
        print(
            f"  window={args.window}  targets={args.targets}  variants={args.variants}"
        )
        print(f"  stop_sweep: {args.stop_start}–{args.stop_end} step {args.stop_step}")
        return

    run_analysis(args)


if __name__ == "__main__":
    main()
