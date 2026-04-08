#!/usr/bin/env python
"""
MYRA - Myra Yield & Research Analytics
The official entry point. (TRILOGY ERA v4.0 Alpha)
"""
import os
import sys
import argparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme
import threading

# Add current dir to path so imports work
sys.path.append(os.getcwd())

from myra_app.screener import MYRAScreener
from myra_app.menu_navigation import MenuNavigator
from myra_app.telegram_notifier import TelegramNotifier
from myra_app.UI_Manager import draw_dashboard
from myra_app.ml_engine import TrendForecaster

# Define High-Vibe Dark Theme
custom_theme = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "stock": "bold yellow",
        "stat": "bold magenta",
    }
)

console = Console(theme=custom_theme, style="white on black")

GLOSSARY = """
[bold cyan]IAS (Institutional Activity Score):[/bold cyan]
 [green]Score[/green]: 0-10 rank of smart money accumulation.
 [green]Elite[/green]: IAS >= 8.5 (Strong institutional footprint).
 [yellow]Tags[/yellow]: Classification (e.g., STRONG_ACCUMULATION).

[bold cyan]SMC-1 (Institutional Footprint):[/bold cyan]
 [green]D-POC[/green]: Base price of institutional accumulation.
 [yellow]Absorption[/yellow]: Intensity of institutional buying.

[bold cyan]ML-1 (AEON Evolutionary Agent):[/bold cyan]
 [magenta]Conviction[/magenta]: AI-ranked load levels (25%, 50%, 100%).
 [magenta]Stars[/magenta]: ML-Confidence rank.
"""


def show_glossary():
    console.print(
        Panel(
            GLOSSARY,
            title="[bold white]MYRA Intelligence Glossary[/bold white]",
            border_style="dim",
            style="on black",
        )
    )


def main():
    parser = argparse.ArgumentParser(description="MYRA")
    parser.add_argument("--options", help="Startup shortcuts (e.g., 26:1)")
    parser.add_argument(
        "--daily", action="store_true", help="Run Daily Routine and Exit"
    )
    args, _ = parser.parse_known_args()

    # Initialize Core Components
    # --- Phase 3: Smart Gatekeeper (ETF Purge & Daily Sanitization) ---
    try:
        from myra_app.librarian_core import LibrarianCore
        from myra_app.gatekeeper import Gatekeeper

        Gatekeeper.smart_gatekeeper(LibrarianCore.DB_MAP, console=console)
    except Exception as e:
        console.print(f"[warning][!] Gatekeeper initialization failed: {e}[/]")

    screener = MYRAScreener(console)
    nav = MenuNavigator(console)
    tg = TelegramNotifier()

    # Automated Modular Sync (Atomic Trilogy)
    console.print("[dim][*] Initiating Background Data Sync...[/dim]")
    screener.lib.start_background_sync()

    # Model Warmup Thread
    forecaster = TrendForecaster(screener.lib)
    forecast_data = {"result": None}

    def warmup():
        try:
            if forecaster.setup_engine():
                forecast_data["result"] = forecaster.get_forecast()
        except Exception:
            pass

    warmup_thread = threading.Thread(target=warmup, daemon=True)
    warmup_thread.start()

    if args.daily:
        console.print("[bold green][*] Running Automated Daily Routine...[/bold green]")
        # 1. Elite Whale Tracker
        res_whale = screener.execute_scan(
            "whale_tracker", "Elite Whale Tracker (Daily)"
        )
        if res_whale:
            tg.send_scan_results("Elite Whale Tracker", res_whale)
        # 2. Super-Scan
        res_super = screener.execute_scan("super_setup", "Super-Scan (Daily)")
        if res_super:
            tg.send_scan_results("Super-Scan", res_super)
        screener.close()
        return

    strategies = {
        "1": ("technicals", "Classical Technicals", None),
        "2": ("delivery_spikes", "Delivery Spikes", "Spike"),
        "3": ("rs_rating", "RS Rating", "RS_Raw"),
        "4": ("bb_squeeze", "BB Squeeze", "BB_Width"),
        "5": ("momentum", "MACD Momentum", "MACD"),
        "6": ("breakouts", "Breakouts", None),
        "7": ("candlesticks", "Reversal Patterns", None),
        "8": ("value", "Value", "ROE"),
        "9": ("vsa_momentum", "Volume Spread Analysis (VSA)", "Rel_Vol"),
        "12": ("super_setup", "Super-Scan (Growth+Mom)", "RS_Raw"),
        "13": ("123", "Graham Deep Value (Intrinsic)", "RS_Raw"),
        "14": ("ml_signals", "ML-Based Signals", "ML_ProbUp"),
        "15": ("whale_tracker", "Elite Whale Tracker (ML)", "Whale_Conf"),
        "16": ("large_deal_momentum", "Institutional Deals", "Inst_Intensity"),
        "23": ("smart_money", "Smart Money Accumulation", "Deliv_Grow"),
        "24": (
            "crash_resilience",
            "Crash Resilience (Underwater Ball)",
            "Absorp_Ratio",
        ),
        "25": ("insider_signals", "Insider Conviction Radar", "Insider_Buy"),
        "27": ("bottom_hunter", "Multi-Year Bottom Hunter", None),
        "28": ("rs_momentum", "RS Momentum & Phelps Base", "Type"),
        "29": ("fakeout_analyzer", "Morning Fakeout Radar", "Type"),
        "30": ("smart_money_ignition", "Smart Money Ignition (SMC-1)", "Ignition_Dist"),
        "31": ("aeon_agent_signals", "AEON Agent Signals (ML-1)", "AEON_Conviction"),
        "32": ("dilated_cnn_forecast", "Dilated CNN Forecast (ML-2)", "Forecast_Move%"),
        "33": (
            "institutional_structural_flow",
            "Institutional Structural Flow (SMC-2)",
            "Structure",
        ),
        "34": ("surpriver_v2", "NSE Surpriver v2 (Quant-Anomaly)", "Anomaly_Score"),
        "35": (
            "multibagger_early",
            "Multibagger Early Detection (Quant)",
            ["Score", "RS_Raw", "Compression", "VWAP_Reclaim", "Divergence"],
        ),
        "A1": ("alpha_vcp", "VCP Base Breakout", "Tightness"),
        "A2": ("alpha_bear_trap", "Weekly Bear Trap Reversal", "Absorption"),
        "A3": ("alpha_rs_leader", "RS Leadership (Stage 2)", "RS_Rating"),
        "A4": ("alpha_earnings_drift", "Post-Earnings Alpha Drift", "Gap_Pct"),
        "A5": ("alpha_delivery_cluster", "Delivery Cluster Accumulation", "High_Days"),
        "A6": ("alpha_stage2_cont", "Stage 2 Trend Continuation", "Relative_Vol"),
        "A7": (
            "alpha_supply_absorption",
            "Supply Absorption (Quiet Buying)",
            "Vol_Ratio",
        ),
        "A8": ("alpha_liquidity_vacuum", "Liquidity Vacuum Move", "Breakout_Vol"),
        "A0": ("alpha_ranker", "Multi-Factor Alpha Ranker (IAS)", "IAS"),
        "T": ("ias_timing_engine", "IAS + Entry Timing Engine", "Score"),
    }

    startup_choice, startup_sub = (
        nav.handle_shortcut(args.options) if args.options else (None, None)
    )
    breadth_text = "↗ 0 | ↘ 0"
    last_intel_update = 0

    while True:
        try:
            now_ts = datetime.now().timestamp()
            if now_ts - last_intel_update > 60:
                breadth = screener.lib.index_engine.get_market_breadth(screener.lib)
                breadth_text = (
                    f"↗ {breadth['advances']} | ↘ {breadth['declines']}"
                    if breadth
                    else "↗ 0 | ↘ 0"
                )
                last_intel_update = now_ts

            if not startup_choice:
                nav.push("Home")
                console.print(
                    draw_dashboard(
                        screener.lib, breadth_text, forecast=forecast_data["result"]
                    )
                )
                raw_choice = console.input(
                    "\n[bold yellow]Select Option (or code e.g. 's SBIN') > [/bold yellow]"
                ).strip()

                # Interactive Short-codes (v4.0 Alpha)
                if " " in raw_choice:
                    parts = raw_choice.split(" ")
                    cmd = parts[0].lower()
                    arg = parts[1].upper()

                    if cmd == "s":  # Quick Search/Deep-Dive
                        console.print(f"[info][*] Direct Deep-Dive for {arg}...[/info]")
                        res = screener.execute_scan(
                            "technicals", f"Deep Dive: {arg}", portfolio_symbols=[arg]
                        )
                        if res:
                            screener.rm.display_discovery_table(
                                res, f"Deep Dive: {arg}", "technicals", []
                            )
                            screener.rm.run_institutional_deep_dive(
                                res, screener.lib.conn, screener.fetcher
                            )
                        nav.pop()
                        continue
                    elif cmd == "b":  # Quick Breadth
                        console.print(f"[info][*] Breadth Radar for {arg}...[/info]")
                        res = screener.execute_scan("all_pass", f"Breadth: {arg}")
                        if res:
                            screener.rm.display_discovery_table(
                                res, f"Breadth: {arg}", "all_pass", []
                            )
                        nav.pop()
                        continue

                choice = raw_choice.upper()
            else:
                choice = startup_choice
                startup_choice = None

        except Exception as e:
            console.print(f"[error][!] UI Error: {e}[/error]")
            choice = "Z"

        if choice == "Z":
            break
        elif choice == "M":
            # Watchdog Logic...
            nav.pop()
            continue
        elif choice == "A":
            nav.push("Alpha Intelligence")
            alpha_opts = [
                "1 > VCP Base Breakout",
                "2 > Weekly Bear Trap",
                "3 > RS Leadership (Stage 2)",
                "4 > Post-Earnings Alpha Drift",
                "5 > Delivery Cluster Accumulation",
                "6 > Stage 2 Trend Continuation",
                "7 > Supply Absorption (Quiet Buying)",
                "8 > Liquidity Vacuum Move",
                "0 > Multi-Factor Alpha Ranker (IAS)",
            ]
            a_choice = nav.render_menu("Alpha Intelligence Discovery", alpha_opts)
            if a_choice:
                choice = f"A{a_choice}"
            else:
                nav.pop()
                continue

        # Generic Strategy Handler
        if choice in strategies:
            s_id, s_name, s_sort = strategies[choice]
            pd_in = console.input(
                "\n[info]Backtest Date? (YYYY-MM-DD) [Enter for Today] > [/info]"
            )
            u_choice = console.input(
                "[info]Universe? [Enter for Institutional Core, 1 for Full Market] > [/info]"
            )

            try:
                res = screener.execute_scan(
                    s_id,
                    s_name,
                    as_of_date=pd_in if pd_in else None,
                    scan_all=(u_choice == "1"),
                )
                if res:
                    screener.rm.display_discovery_table(res, s_name, s_id, [])
                    screener.rm.archive_results(res, s_name, strategy_id=s_id)
                    show_glossary()
                else:
                    console.print(
                        f"[warning][!] No stocks found matching '{s_name}'.[/warning]"
                    )
            except Exception as e:
                console.print(f"[error][!] Scan Failed: {e}[/error]")
        elif choice == "1":
            # Nested Technicals...
            pass

        nav.pop()

    screener.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
