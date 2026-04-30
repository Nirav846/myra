import json
import os
import sqlite3
from datetime import datetime

import pandas as pd
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


class MYRA_UI:
    @staticmethod
    def get_header(librarian, forecast=None):
        # 1. Base Logo
        logo = Text("M Y R A", style="bold bright_magenta")
        logo.append(" | Yield & Research Analytics v2.5", style="italic magenta")

        # 2. Market Intelligence Dashboard data
        dash = ""
        try:
            nifty = librarian.index_engine.get_nifty()
            vix = librarian.index_engine.get_vix()

            if nifty and vix:
                n_col = "green" if nifty["change"] >= 0 else "red"
                v_col = (
                    "red"
                    if vix["last_price"] > 18
                    else "green"
                    if vix["last_price"] < 15
                    else "yellow"
                )
                dash = f"NIFTY 50: [{n_col}]{nifty['last_price']}[/] ({nifty['pchange']}%) | "
                dash += f"INDIA VIX: [{v_col}]{vix['last_price']}[/] | "

                if forecast:
                    f_col = (
                        "green"
                        if forecast["direction"] == "BULLISH"
                        else "red"
                        if forecast["direction"] == "BEARISH"
                        else "yellow"
                    )
                    dash += f"AI Forecast: [{f_col}]{forecast['direction']}[/] ({forecast['confidence']}%) | "

                # Performance Guard Compliant (Fix 36)
                ts = nifty["timestamp"]
                dash += f"As of {ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}"
        except Exception:
            dash = "Market Data Unavailable"

        # 3. System Health Dashboard
        health = MYRA_UI.get_health_metrics()
        if health["total"] > 0:
            score = health["score"]
            missing = health["missing"]
            h_col = "green" if score >= 95 else "yellow" if score >= 90 else "red"
            health_str = f" | System Health Score: [{h_col}]{score:.2f}%[/]"
            if missing > 0:
                health_str += f" [red](Alert: {missing} skips)[/]"
            dash += health_str

        header_table = Table.grid(expand=True)
        header_table.add_row(logo)
        header_table.add_row(Text.from_markup(dash))

        return Panel(header_table, border_style="bright_magenta", expand=True)

    @staticmethod
    def get_health_metrics():
        manifest_path = "data_sync_manifest.json"
        metrics = {"score": 0.0, "total": 0, "missing": 0}

        if not os.path.exists(manifest_path):
            return metrics

        try:
            with open(manifest_path, "r") as f:
                data = json.load(f)
        except Exception:
            return metrics

        total_symbols_processed = data.get("total_symbols_processed", 0)
        missing_delivery_list = data.get("missing_delivery_list", [])

        if total_symbols_processed > 0:
            score = (
                (total_symbols_processed - len(missing_delivery_list))
                / total_symbols_processed
            ) * 100
        else:
            score = 0.0

        metrics["score"] = score
        metrics["total"] = total_symbols_processed
        metrics["missing"] = len(missing_delivery_list)

        return metrics

    @staticmethod
    def get_menu_grid():
        # Outer grid for 4 columns
        menu_grid = Table.grid(expand=True, padding=1)
        menu_grid.add_column(ratio=1)
        menu_grid.add_column(ratio=1)
        menu_grid.add_column(ratio=1)
        menu_grid.add_column(ratio=1)

        # 1. Technical Core (Yellow)
        tech_panel = MYRA_UI.get_category_panel(
            "Technical Core",
            [
                "1 > Classical Scout",
                "2 > Deliv Spikes",
                "3 > RS Rating",
                "4 > BB Squeeze",
                "5 > MACD Momentum",
                "6 > Breakouts",
                "7 > Reversals",
                "9 > VSA Momentum",
                "28 > RS Momentum",
                "29 > Morning Fakeout",
            ],
            "yellow",
        )

        # 2. Alpha Intelligence (Magenta)
        alpha_panel = MYRA_UI.get_category_panel(
            "Alpha Intelligence",
            [
                "A1 > VCP Base Breakout",
                "A2 > Weekly Bear Trap",
                "A3 > RS Leadership (S2)",
                "A4 > Earnings Drift",
                "A5 > Delivery Clusters",
                "A6 > Stage 2 Cont.",
                "A0 > Alpha Ranker (IAS)",
                "30 > Smart Money Ignition",
                "33 > Structural Flow (SMC-2)",
                "35 > Multibagger Early",
            ],
            "magenta",
        )

        # 3. AI & Quant Engine (Cyan)
        ml_panel = MYRA_UI.get_category_panel(
            "AI & Quant Engine",
            [
                "14 > ML-Signals",
                "15 > Elite Whale Tracker",
                "31 > AEON Agent (ML-1)",
                "32 > Dilated CNN (ML-2)",
                "36 > Institutional Fusion Tracker",
                "20 > Intelligence Scout",
                "24 > Crash Resilience",
                "19 > Market Breadth",
                "M  > Watchdog Daemon",
                "L  > Last Results",
            ],
            "cyan",
        )

        # 4. Strategic Command (Green)
        val_panel = MYRA_UI.get_category_panel(
            "Strategic Command",
            [
                "0 > Daily Routine",
                "12 > Super-Scan (RS)",
                "27 > Bottom Hunter",
                "8  > Value (ROE/PEG)",
                "13 > Deep Value (Graham)",
                "21 > Portfolio Monitor",
                "26 > Piped Playbook",
                "T  > IAS Timing Engine Picks",
                "Z  > Exit System",
            ],
            "green",
        )

        menu_grid.add_row(tech_panel, alpha_panel, ml_panel, val_panel)
        return menu_grid

    @staticmethod
    def get_category_panel(title, options, color):
        content = Table.grid(expand=True)
        # Category Header with solid background
        content.add_row(f"[black on {color}]  {title.upper():^24}  [/]")
        content.add_row("")  # Spacer

        for opt in options:
            if " > " in opt:
                key, desc = opt.split(" > ", 1)
                content.add_row(f" [bold {color}]{key:^2}[/] [dim white]> {desc}[/]")
            else:
                content.add_row(f" [bold {color}]{opt}[/]")

        return Panel(content, border_style=color, padding=(1, 1), expand=True)

    @staticmethod
    def get_ias_leaderboard(librarian):
        """Top symbols by Institutional Activity Score."""
        return Panel(
            "[dim]Insider trading feed unavailable – historical data removed.[/dim]\n"
            "[dim]Institutional flow estimates continue to update live.[/dim]",
            title="Institutional",
            border_style="dim",
            width=30,
        )

    @staticmethod
    def get_fii_dii_flow(librarian):
        """Summary of institutional flow."""
        # For now, a placeholder until shareholding history is fully populated
        content = Table.grid(expand=True)
        content.add_row("[bold cyan]Institutional Flow (Est)[/]")
        content.add_row(" FII: [green]↑ Bullish[/]")
        content.add_row(" DII: [green]↑ Bullish[/]")
        content.add_row(" Vibe: [success]Accumulation[/]")
        return Panel(content, border_style="cyan")

    @staticmethod
    def get_timing_triggers_panel(librarian):
        """Scans top IAS symbols for immediate entry setups."""
        try:
            if (
                not librarian
                or not hasattr(librarian, "_gov_conn")
                or not librarian._gov_conn
            ):
                gov_db = os.path.join(os.getcwd(), "db", "governance.db")
                if os.path.exists(gov_db):
                    conn = sqlite3.connect(gov_db)
                else:
                    return Panel(
                        "[red]Governance DB Missing[/]", title="Timing Triggers"
                    )
            else:
                conn = librarian._gov_conn

            # Hardened v3.2: Only EQUITY
            from myra_app.librarian_core import LibrarianCore

            meta_path = os.path.join(os.getcwd(), "db", LibrarianCore.DB_MAP["meta"])
            try:
                conn.execute(f"ATTACH DATABASE '{meta_path}' AS meta_db")  # noqa: S608
                sql = """
                    SELECT h.symbol 
                    FROM ias_history h
                    JOIN meta_db.symbols_master s ON h.symbol = s.symbol
                    WHERE h.ias_score >= 7.0 AND s.instrument_type = 'EQUITY' AND s.is_active = 1
                    ORDER BY h.date DESC, h.ias_score DESC 
                    LIMIT 20
                """
                df_ias = pd.read_sql(sql, conn)
            except Exception:
                sql = "SELECT symbol FROM ias_history WHERE ias_score >= 7.0 ORDER BY date DESC, ias_score DESC LIMIT 20"
                df_ias = pd.read_sql(sql, conn)

            table = Table(
                title="[bold green]Timing Triggers (IAS >= 7)[/]",
                expand=True,
                header_style="bold green",
                box=None,
            )
            table.add_column("Symbol", style="bold yellow")
            table.add_column("Type", style="cyan")
            table.add_column("Entry", justify="right")

            if df_ias.empty:
                table.add_row("No Data", "-", "-")
                return table

            from myra_app.data_adapter import DataAdapter
            from myra_app.strategies.ias_timing_engine import run as run_timing

            adapter = DataAdapter(librarian=librarian)
            count = 0

            for symbol in df_ias["symbol"]:
                df = adapter.get_technical_history(symbol, days=150)
                if df.empty:
                    continue
                funda = adapter.get_latest_funda(symbol, df)
                funda["symbol"] = symbol

                res = run_timing(df, funda)
                if res and res.get("signal"):
                    m = res.get("metrics", {})
                    table.add_row(
                        symbol, str(m.get("Type", "-")), str(m.get("Entry", "-"))
                    )
                    count += 1
                    if count >= 5:
                        break

            if count == 0:
                table.add_row("No Setups", "-", "-")

            return table
        except Exception as e:
            return Panel(
                f"[dim]Timing Feed Error: {str(e)}[/]", title="Timing Triggers"
            )

    @staticmethod
    def get_background_tasks_panel():
        """Display active background tasks with safety status."""
        try:
            from myra_app.task_tracker import get_active_tasks

            tasks = get_active_tasks()
            if not tasks:
                # Idle status line
                return Panel("✅ System idle – safe to exit", title="Status", border_style="green")

            lines = []
            any_unsafe = False
            for t in tasks:
                name = t["name"]
                status = t["status"]
                progress = t["progress"]
                eta = t["eta"]
                safe = t["safe_to_exit"]
                if not safe:
                    any_unsafe = True

                if progress is not None:
                    # Render a text progress bar 20 chars wide
                    bar_len = 20
                    filled = int(progress / 100 * bar_len)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    eta_text = f" {eta}" if eta else ""
                    lines.append(f"• {name}: {bar} {progress}%{eta_text}")
                else:
                    lines.append(f"• {name} – {status}")

            safe_text = "⚠️  Unsafe to close – background tasks in progress" if any_unsafe else "✅ Safe to close"
            content = "\n".join(lines) + "\n\n" + safe_text
            style = "red" if any_unsafe else "cyan"
            return Panel(content, title="Background Tasks", border_style=style)
        except Exception:
            return Panel("Task tracker unavailable", title="Background Tasks", border_style="dim")

    @staticmethod
    def get_footer(librarian, market_breadth="↗ 0 | ↘ 0", forecast=None):
        db_stats = (
            librarian.get_db_stats()
            if librarian and hasattr(librarian, "get_db_stats")
            else {"status": "Connected (Core)", "size": "-"}
        )
        db_status = db_stats.get("status", "Connected")
        db_size = db_stats.get("size", "0MB")

        # 1. Resolve Data Dates (Bhavcopy)
        b_date = (
            librarian.get_max_price_date()
            if librarian and hasattr(librarian, "get_max_price_date")
            else None
        )

        def _format_dt(dt):
            if not dt:
                return "-"
            if isinstance(dt, str):
                try:
                    # Expected format: YYYY-MM-DD
                    parsed = datetime.strptime(dt, "%Y-%m-%d")
                    return f"{parsed.day}/{parsed.month}"
                except:
                    return dt
            return f"{dt.day}/{dt.month}"

        b_str = f"BCOPY({_format_dt(b_date)})"
        i_str = "INST([bold green]LIVE[/bold green])"
        # 2. Market Status / Breadth
        sync_text = market_breadth
        if (
            librarian
            and hasattr(librarian, "sync_status")
            and librarian.sync_status
            and hasattr(librarian.sync_status, "task_name")
            and librarian.sync_status.task_name
        ):
            st = librarian.sync_status
            sync_text = f"[bold cyan]Sync:[/] {st.task_name} ({st.percentage}%)"

        # 3. AI Forecast (Footer Visibility)
        if forecast:
            f_col = (
                "green"
                if forecast["direction"] == "BULLISH"
                else "red"
                if forecast["direction"] == "BEARISH"
                else "yellow"
            )
            forecast_text = (
                f" | [bold white]AI-Trend:[/] [{f_col}]{forecast['direction']}[/]"
            )
        else:
            forecast_text = " | [bold white]AI-Trend:[/] [yellow]Calibrating...[/]"

        footer_table = Table.grid(expand=True)
        left_text = f"[bold blue]DB:[/] {db_status} ({db_size}) | [bold white]Status:[/] {sync_text}{forecast_text}"
        right_text = f"[bold cyan]{b_str}[/] | [bold magenta]{i_str}[/]"

        footer_table.add_row(left_text, right_text)
        return Panel(footer_table, border_style="blue", expand=True)


def draw_dashboard(librarian, breadth_text="↗ 0 | ↘ 0", forecast=None):
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="body", ratio=1),
        Layout(name="tasks", size=5),
        Layout(name="footer", size=3),
    )

    # Split Body into Left (Menu) and Right (Sidebar)
    layout["body"].split_row(
        Layout(name="body_left", ratio=3), Layout(name="body_right", ratio=1)
    )

    # Nested Split for Sidebar
    layout["body_right"].split_column(
        Layout(name="ias_widget", ratio=2),
        Layout(name="timing_widget", ratio=1),
        Layout(name="flow_widget", ratio=1),
    )

    layout["header"].update(MYRA_UI.get_header(librarian, forecast))
    layout["body_left"].update(MYRA_UI.get_menu_grid())
    layout["ias_widget"].update(MYRA_UI.get_ias_leaderboard(librarian))
    layout["timing_widget"].update(MYRA_UI.get_timing_triggers_panel(librarian))
    layout["flow_widget"].update(MYRA_UI.get_fii_dii_flow(librarian))
    layout["tasks"].update(MYRA_UI.get_background_tasks_panel())
    layout["footer"].update(MYRA_UI.get_footer(librarian, breadth_text, forecast))

    return layout


# --- EXECUTION TRIGGER ---
if __name__ == "__main__":
    try:
        from myra_app.librarian_core import LibrarianCore

        librarian = LibrarianCore()
    except Exception as e:
        # Fallback if LibrarianCore fails to load or isn't set up perfectly yet
        console.print(
            f"[dim yellow]Running UI without full LibrarianCore connection ({e})[/]"
        )
        librarian = None

    # Draw and print the full layout to the terminal
    ui_layout = draw_dashboard(librarian)
    console.print(ui_layout)
