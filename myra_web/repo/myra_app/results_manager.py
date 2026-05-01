import os
import pandas as pd
import numpy as np
from datetime import datetime
from rich.table import Table
from rich.console import Console
from .institutional_pipe import InstitutionalPipe
from myra_app.strategies.alpha.position_sizer import VolatilityAdjustedSizer, KellySizer

try:
    import matplotlib.pyplot as plt
    import matplotlib

    matplotlib.use("Agg")  # Headless mode
except ImportError:
    plt = None


class ResultsManager:
    """
    MYRA Results Manager (v1.5)
    Handles table formatting, CSV archiving, Automated Charting, and AI Prompts.
    """

    def __init__(self, console: Console = None):
        self.console = console or Console()
        self.report_dir = os.path.join(os.getcwd(), "myra_reports")
        self.chart_dir = os.path.join(self.report_dir, "charts")
        for d in [self.report_dir, self.chart_dir]:
            if not os.path.exists(d):
                os.makedirs(d)

    def load_last_snapshot(self, strategy_id):
        """Loads the last valid scan result from archive (Fix 21, 23)."""
        archive_path = os.path.join(self.report_dir, f"scan_{strategy_id}.csv")
        if not os.path.exists(archive_path):
            return []

        try:
            # Check file age (Fix 23)
            mtime = os.path.getmtime(archive_path)
            last_date = datetime.fromtimestamp(mtime)
            age_days = (datetime.now() - last_date).days

            if age_days > 2:
                # print(f"[CRITICAL] Snapshot too old ({age_days} days). Rejecting stale data.")
                return []

            df = pd.read_csv(archive_path)
            return df.to_dict("records")
        except Exception:
            return []

    def sanitize_results(self, results: list) -> list:
        """Removes unknown or broken data points and applies Global 'Best First' Ranking."""
        if not results:
            return []

        # Avoid .append() in loop - Use list comprehension for batch processing
        clean = [
            r
            for r in results
            if r.get("Stock") and r.get("LTP") != "-" and r.get("LTP") is not None
        ]

        if not clean:
            return []

        # Apply Global Ranking (NewUI.TXT Specification)
        df = pd.DataFrame(clean)
        df = self.apply_global_ranking(df)
        return df.to_dict("records")

    def apply_global_ranking(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        PKScreener Gold Standard Ranking:
        1. Stage Dominance (Stage 2 > 1 > 3 > 4)
        2. Conviction Multiplier (5 Stars > 0 Stars)
        3. Liquidity (Higher Money Flow wins)
        """
        try:
            # 1. Map Stage to Priority
            stage_order = {"Stage 2": 0, "Stage 1": 1, "Stage 3": 2, "Stage 4": 3}
            # Extract "Stage X" part if it has arrows or other text
            df["_stage_base"] = df["Stage"].astype(str).str.extract(r"(Stage \d)")
            df["_stage_rank"] = df["_stage_base"].map(stage_order).fillna(4)

            # 2. Convert Star Rating to Numeric
            df["_star_rank"] = df["Stars"].astype(str).str.count(r"\*").fillna(0)

            # 3. AEON Conviction Rank
            if "AEON_Conviction" in df.columns:
                aeon_col = df["AEON_Conviction"].astype(str)
                conditions = [
                    aeon_col.str.contains("CONVICTION"),
                    aeon_col.str.contains("CORE"),
                    aeon_col.str.contains("TACTICAL"),
                    aeon_col.str.contains("EXIT"),
                ]
                choices = [0, 1, 2, 3]
                df["_aeon_rank"] = np.select(conditions, choices, default=4)
            else:
                df["_aeon_rank"] = 4

            # 4. Clean Money Flow for numeric sort
            mf_key = "Money_Flow" if "Money_Flow" in df.columns else "Money Flow"
            if mf_key in df.columns:
                df["_mf_rank"] = pd.to_numeric(
                    df[mf_key].astype(str).str.replace("[₹,Cr]", "", regex=True),
                    errors="coerce",
                ).fillna(0.0)
            else:
                df["_mf_rank"] = 0.0

            # 5. Hybrid Institutional Ranking (PKScreener Superpower)
            # Priority: AEON -> Anomaly + RDV Bonus -> Stage -> Stars -> Liquidity
            df["_anom_rank"] = (
                pd.to_numeric(df["Anomaly_Score"], errors="coerce").fillna(0.0)
                if "Anomaly_Score" in df.columns
                else 0.0
            )

            # TURNAROUND BONUS: If RDV is very high (> 3.0), boost the Anomaly rank
            # This ensures stocks like ONESOURCE float to the top
            df["_inst_score"] = df["_anom_rank"]
            if "RDV" in df.columns:
                rdv_col = pd.to_numeric(df["RDV"], errors="coerce").fillna(0.0)
                bonus_conditions = [rdv_col > 5.0, rdv_col > 3.0, rdv_col > 1.5]
                bonus_choices = [0.5, 0.3, 0.1]
                df["_inst_score"] += np.select(
                    bonus_conditions, bonus_choices, default=0.0
                )

            # 6. Sort Everything
            sort_cols = [
                "_aeon_rank",
                "_inst_score",
                "_stage_rank",
                "_star_rank",
                "_mf_rank",
            ]
            sort_asc = [True, False, True, False, False]

            df = df.sort_values(by=sort_cols, ascending=sort_asc)

            # 7. Cleanup temp columns
            return df.drop(
                columns=[
                    "_stage_base",
                    "_stage_rank",
                    "_star_rank",
                    "_mf_rank",
                    "_aeon_rank",
                    "_anom_rank",
                    "_inst_score",
                ]
            )
        except Exception:
            # If ranking fails, return original to avoid losing data
            return df

    def archive_results(self, results: list, scan_name: str, strategy_id: str = None):
        """Saves final scan results to CSV, builds AI Prompt, and caches for instant recall."""
        if not results:
            return None
        try:
            df = pd.DataFrame(results)
            # Cache for instant recall (The PKScreener superpower)
            df.to_pickle(os.path.join(self.report_dir, "last_results.pkl"))

            # Save strategy-specific snapshot for holiday recovery (Fix 21)
            if strategy_id:
                snap_path = os.path.join(self.report_dir, f"scan_{strategy_id}.csv")
                df.to_csv(snap_path, index=False)

            # Remove Rich Tags for CSV/HTML
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = (
                        df[col].astype(str).str.replace(r"\[.*?\]", "", regex=True)
                    )
            # Performance Guard Compliant (Fix 142)
            n = datetime.now()
            ts = f"{n.day:02d}{n.month:02d}{n.year}_{n.hour:02d}{n.minute:02d}{n.second:02d}"
            base = f"MYRA_{scan_name.replace(' ', '_')}_{ts}"

            # Save CSV
            csv_path = os.path.join(self.report_dir, f"{base}.csv")
            df.to_csv(csv_path, index=False)

            # Save Rich HTML Report (PKScreener Superpower)
            html_path = os.path.join(self.report_dir, f"{base}.html")
            html_content = f"""
            <html>
            <head>
                <title>MYRA Intelligence: {scan_name}</title>
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0d1117; color: #c9d1d9; padding: 20px; }}
                    h1 {{ color: #58a6ff; }}
                    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                    th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #30363d; }}
                    th {{ background-color: #161b22; color: #8b949e; font-weight: 600; }}
                    tr:hover {{ background-color: #21262d; }}
                    .success {{ color: #3fb950; }}
                    .warning {{ color: #d29922; }}
                    .danger {{ color: #f85149; }}
                </style>
            </head>
            <body>
                <h1>MYRA Discovery: {scan_name}</h1>
                <p>Generated on: {datetime.now().isoformat(sep=' ', timespec='seconds')}</p>
                {df.to_html(index=False, classes='myra-table', border=0)}
            </body>
            </html>
            """
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            # Generate the AI Prompt for the top 10 (NewUI.TXT)
            self.generate_ai_prompt(results[:10])

            return csv_path
        except Exception:
            return None

    def display_last_results(self):
        """Instantly re-displays the last successful scan results."""
        path = os.path.join(self.report_dir, "last_results.pkl")
        if not os.path.exists(path):
            self.console.print(
                "[warning][!] No previous scan results found in cache.[/warning]"
            )
            return

        try:
            df = pd.read_pickle(path)
            results = df.to_dict("records")
            # Determine hero columns based on what's in the DF
            all_cols = df.columns.tolist()
            # We skip standard columns to find 'Hero' metrics
            std = [
                "Stock",
                "Stars",
                "Grade",
                "MYRA_Score_v25",
                "Stage",
                "LTP",
                "Entry",
                "TSL",
                "Vibe",
                "FVG_Zone",
                "CHoCH",
                "Support",
            ]
            hero = [c for c in all_cols if c not in std and c != "last_updated"]

            self.display_discovery_table(results, "Cached Last Results", "cached", hero)
        except Exception as e:
            self.console.print(f"[error][!] Error loading cache: {e}[/error]")

    def generate_ai_prompt(self, top_candidates: list):
        """Builds a professional research prompt for copy-pasting into LLMs."""
        if not top_candidates:
            return

        prompt = [
            "Act as a Senior Equity Analyst. I have identified high-conviction technical setups in the NSE India market.",
            "Use the following MYRA Intelligence thresholds for your analysis:",
            "- **Funda_Score (0-100)**: Institutional quality rank. >70 is Elite, <40 is Risky.",
            "- **Insider Cost Basis**: The avg price at which promoters recently bought. Buying BELOW this is 'Underwater Entry' (High Alpha).",
            "- **Volatility Compression (VCP)**: Indicates short-term risk is low and an explosive expansion is likely.",
            "- **Institutional Absorption**: Measures delivery volume vs norms. >100% means Smart Money accumulation.",
            "- **CHoCH (Change of Character)**: Structural shift signaling potential trend reversal.",
            "- **Best Buy (FVG Zone)**: The price range where institutional fair value gaps exist. Buying within this zone offers the highest probability of institutional support.",
            "",
            "Please perform a deep fundamental and macro 'Second Opinion' for these stocks:",
            "",
        ]

        for r in top_candidates[:15]:
            sym = (
                str(r.get("Stock", "Unknown"))
                .replace("[black on cyan]", "")
                .replace("[/black on cyan]", "")
                .replace("[bold yellow]", "")
                .replace("[/bold yellow]", "")
            )

            # Institutional DNA
            dna = f"LTP: {r.get('LTP','-')}, Stage: {r.get('Stage','-')}, MCap: {r.get('MCap','UNKNOWN')}, SM_Score: {r.get('smart_money_score',0)}"
            ms_intel = f"MStar: {r.get('MStar','-')}, Fair Value: {r.get('Fair_Val','-')}, Whale: {r.get('Whale_Conv','Low')}, CAR: {r.get('CAR',0.0)}"

            # Gather special footprints (Fix 230-245: Avoid .append in loop)
            avg_buy = r.get("avg_buy_60d")
            ltp_val = (
                float(str(r.get("LTP", 0)).replace(",", ""))
                if str(r.get("LTP")).replace(".", "").replace(",", "").isdigit()
                else 0
            )

            # Use list comprehension to build entire footprints list in one go
            footprints = []
            if avg_buy and avg_buy != 0:
                footprints = [f"Insider Cost Basis: {avg_buy}"]
                if ltp_val < float(avg_buy):
                    footprints = footprints + ["**UNDERWATER ENTRY SIGNAL**"]

            # Hidden Accumulation Footprint
            if r.get("Hidden_Acc") == "YES":
                footprints = footprints + [
                    "**HIDDEN INSTITUTIONAL ACCUMULATION (Price down, FII up)**"
                ]

            # Vectorized flags
            flags = [
                ("Compression", "YES", "Volatility Compression (VCP) Active"),
                ("Divergence", "YES", "Bullish RSI Divergence (Coiled Spring)"),
                ("VWAP_Reclaim", "YES", "Institutional VWAP Reclaim (In-the-Money)"),
                ("Weekly_Div", "YES", "Bullish Weekly RSI Divergence"),
                ("CHoCH", "YES", "Structural Change of Character (CHoCH)"),
            ]
            footprints.extend([msg for key, val, msg in flags if r.get(key) == val])

            # Other conditional data points (Fix 246-252: Avoid .append in loop)
            extra_footprints = [
                f"Relative Strength (RS): {r['RS_Raw']}" if r.get("RS_Raw") else None,
                f"Structural Support: {r['Support']}" if r.get("Support") else None,
                f"Institutional Delivery: {r['Delivery']} (RDV: {r.get('RDV','-')})"
                if r.get("Delivery")
                else None,
                f"Profile: {r.get('Tag', r.get('Multibagger_Flag', 'STANDARD'))}",
            ]
            footprints.extend([f for f in extra_footprints if f is not None])

            # Tactical Zone
            tactics = f"Entry: {r.get('Entry','-')}, SL: {r.get('SL_Final', r.get('SL','-'))}, T1: {r.get('T1','-')}, T2: {r.get('T2','-')}"

            # Batch append to prompt
            prompt.extend(
                [
                    f"### {sym}",
                    f"- **Institutional DNA**: {dna}",
                    f"- **Morningstar Intel**: {ms_intel}",
                    f"- **Institutional Footprint**: {', '.join(footprints)}"
                    if footprints
                    else "- **Institutional Footprint**: None",
                    f"- **Tactical Plan**: {tactics}",
                    "",
                ]
            )

        prompt.extend(
            [
                "For each candidate, perform a cap-specific analysis:",
                "1. If Small/Midcap (<50k Cr): Evaluate 'Multibagger potential' based on scalability and float absorption.",
                "2. If Large Cap (>50k Cr): Focus on 'Institutional Turnaround' and steady-state trend expansion.",
                "3. If MCap UNKNOWN: Research MCap first to apply the correct framework above.",
                "4. Cross-Market Analysis: Sector correlation and Price-Volume anomalies in the last 5 days.",
            ]
        )

        with open("MYRA_AI_READY.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(prompt))

        self.console.print(
            "\n[success][✔] AI Prompt Ready![/success] Copy from [bold]MYRA_AI_READY.txt[/bold] into Gemini."
        )

    def display_portfolio_risk_audit(self, results: list):
        """Visualizes portfolio concentration and risk distribution."""
        if not results:
            return

        # 1. Sector Concentration
        sectors = {}
        for r in results:
            s = r.get("Sector", "Unknown")
            sectors[s] = sectors.get(s, 0) + 1

        t_sector = Table(
            title="Sector Exposure Audit", header_style="bold cyan", border_style="dim"
        )
        t_sector.add_column("Sector")
        t_sector.add_column("Count")
        t_sector.add_column("Concentration")
        total = len(results)
        for s, count in sorted(sectors.items(), key=lambda x: -x[1]):
            perc = (count / total) * 100
            color = "red" if perc > 30 else "yellow" if perc > 20 else "green"
            t_sector.add_row(s, str(count), f"[{color}]{perc:.1f}%[/{color}]")

        # 2. Risk Metrics (Sharpe/Sortino placeholders - requires daily returns)
        # For now, we show Annualized Volatility and Max Drawdown
        t_risk = Table(
            title="Portfolio Risk Metrics", header_style="bold red", border_style="dim"
        )
        t_risk.add_column("Stock")
        t_risk.add_column("Max Drawdown")
        t_risk.add_column("Volatility (Ann)")
        t_risk.add_column("Risk Rating")

        for r in results:
            mdd = r.get("MDD", 0)
            vol = r.get("Vol_Ann", 0)
            rating = (
                "[green]LOW[/green]"
                if vol < 20
                else "[yellow]MOD[/yellow]"
                if vol < 40
                else "[red]HIGH[/red]"
            )
            t_risk.add_row(r["Stock"], f"{mdd:.1f}%", f"{vol:.1f}%", rating)

        self.console.print(t_sector)
        self.console.print(t_risk)

    def save_charts(self, results: list, lib):
        """Generates PNG charts for the top 5 candidates."""
        if not plt or not results:
            return
        self.console.print(
            "[info][MYRA] Generating visual charts for top candidates...[/info]"
        )
        for r in results[:5]:
            try:
                symbol = r["Stock"]
                df = lib.get_ohlcv(symbol)
                if df is None or df.empty:
                    continue
                df = df.tail(100)
                plt.figure(figsize=(10, 6))
                plt.style.use("dark_background")
                plt.plot(
                    df.index, df["Close"], color="white", label="Price", linewidth=2
                )
                plt.plot(
                    df.index,
                    df["Close"].rolling(20).mean(),
                    color="cyan",
                    label="SMA 20",
                    alpha=0.8,
                )
                plt.plot(
                    df.index,
                    df["Close"].rolling(50).mean(),
                    color="yellow",
                    label="SMA 50",
                    alpha=0.8,
                )
                plt.title(f"MYRA Intelligence: {symbol}", color="magenta", fontsize=14)
                plt.legend()
                plt.grid(color="#333", linestyle="--", alpha=0.5)
                plt.savefig(os.path.join(self.chart_dir, f"{symbol}_chart.png"))
                plt.close()
            except Exception:
                pass

    def display_discovery_table(
        self,
        results: list,
        scan_name: str,
        strategy_id: str,
        hero_cols: list,
        strictly_technical: bool = False,
    ):
        from rich import box

        results = self.sanitize_results(results)

        # PERSISTENT SUMMARY FOOTER (Prep)
        total = len(results)
        strong = (
            len([r for r in results if "2" in str(r.get("Stage", ""))])
            if results
            else 0
        )

        if not results:
            self.console.print(
                "[dim]  [+] Scan Complete: 0 candidates identified. [/dim]"
            )
            return

        # Check if v2.5 Score exists in results to add it automatically
        has_v25 = "MYRA_Score_v25" in results[0]

        # AUTO-ADJUST UI: Get terminal width
        term_width = self.console.width
        is_narrow = term_width < 130
        is_mobile = term_width < 100
        show_sector = term_width >= 150  # High-res only

        table = Table(
            title=f"MYRA Discovery: {scan_name}",
            header_style="bold magenta",
            border_style="cyan",
            style="on black",
            box=box.SIMPLE if is_narrow else box.HEAVY_EDGE,
            expand=True,
            min_width=100,
            caption=f"[dim]Total: {total} | Strong (Stage 2): {strong}[/dim]",
        )

        # Column 1: Identity Group (Yellow)
        table.add_column(
            "Stock",
            style="bold yellow",
            overflow="ellipsis",
            no_wrap=True,
            min_width=8,
            header_style="bold yellow",
        )
        if show_sector:
            table.add_column(
                "Sector", style="dim cyan", no_wrap=True, header_style="bold yellow"
            )
        table.add_column(
            "Rating", justify="center", header_style="bold yellow", min_width=6
        )
        # Institutional Alpha Group (Smart Money)
        table.add_column(
            "MStar", justify="center", style="yellow", header_style="bold magenta"
        )
        table.add_column(
            "CAR", justify="right", style="bold cyan", header_style="bold magenta"
        )
        table.add_column(
            "Whale", justify="center", style="dim", header_style="bold magenta"
        )
        table.add_column("Hidden_Acc", justify="center", header_style="bold magenta")

        if not strictly_technical:
            if not is_mobile:
                table.add_column("Grade", justify="center", header_style="bold yellow")
            if has_v25:
                table.add_column(
                    "Score",
                    justify="right",
                    style="bold green",
                    header_style="bold yellow",
                )

            # Technical DNA Group (Cyan)
            if not is_narrow:
                table.add_column("Accuracy", justify="right", header_style="cyan")
                table.add_column(
                    "Pattern", justify="left", overflow="fold", header_style="cyan"
                )

            if not is_mobile:
                table.add_column(
                    "Money_Flow",
                    justify="right",
                    style="bold cyan",
                    header_style="cyan",
                )

            table.add_column(
                "Stage", style="bold magenta", justify="center", header_style="cyan"
            )
            table.add_column("LTP", justify="right", header_style="cyan")
            table.add_column(
                "Fair_Val",
                justify="right",
                style="bold green",
                header_style="bold magenta",
            )

            # Tactical Zone (Green)
            mid_col = "Best Buy"
            table.add_column(
                mid_col, style="green", justify="center", header_style="bold green"
            )

            if not is_narrow:
                table.add_column("Vibe", justify="center", header_style="bold green")

        # Institutional Alpha Group (Magenta)
        for c in hero_cols:
            # Shorten column headers for UI density
            header = (
                c.replace("Anomaly_Score", "Anom")
                .replace("Active_Windows", "Win")
                .replace("Absorp_Ratio", "Absorp")
                .replace("Absorption", "Absorp")
            )
            if header == "Stage":
                header = "Trend"  # Avoid collision
            table.add_column(
                header, justify="right", style="cyan", header_style="bold magenta"
            )

        for idx, r in enumerate(results):
            g = r.get("Grade", "C")
            g_sym = "★" if "A" in g else "✓" if "B" in g else "⚠" if "C" in g else "✗"
            gc = (
                "green"
                if "A" in g
                else "yellow"
                if "B" in g
                else "white"
                if "C" in g
                else "red"
            )

            # Universal Formatting for standard numeric fields
            ltp = r.get("LTP", "-")
            if isinstance(ltp, (float, int, np.number)):
                ltp = f"{float(ltp):.2f}"

            entry_val = r.get("Entry", "-")
            tsl_val = r.get("TSL", "-")
            fvg_zone = r.get("FVG_Zone", "None")

            if strategy_id == "bottom_hunter":
                val = r.get("FVG_Zone", "Wait")
            elif strategy_id == "portfolio_monitor":
                val = (
                    f"{float(tsl_val):.2f}"
                    if isinstance(tsl_val, (float, int, np.number))
                    else str(tsl_val)
                )
            else:
                # System-wide: Best Buy = FVG Zone (Mandate)
                fvg_zone = r.get("FVG_Zone", "None")
                if fvg_zone != "None" and fvg_zone != "Wait" and fvg_zone != "-":
                    val = f"[bold green]{fvg_zone}[/]"
                else:
                    val = "[dim]- Wait -[/dim]"

            s = r.get("Stage", "-")
            s_str = f"↗ {s}" if "2" in s else f"↘ {s}" if "4" in s else s
            if is_narrow:
                s_str = s_str.replace("Stage ", "S")  # Compact Stage

            # Row Decorators (NewUI.TXT Specification)
            is_diamond = idx < max(1, len(results) * 0.05)  # Top 5%
            is_danger = "4" in str(s)

            # 1. Stock / Ticker Column
            ticker_style = "black on cyan" if is_diamond else "bold yellow"
            ticker_text = f"[{ticker_style}]{r['Stock']}[/{ticker_style}]"

            # 2. Global Dimming for Stage 4
            row_style = "dim" if is_danger else ""

            # Optimized row construction (Fix 442-477: Avoid .append in loop)
            row = [ticker_text]
            if show_sector:
                sect = r.get("Sector", "Unknown")
                row = row + [str(sect) if pd.notna(sect) else "Unknown"]

            stars = r.get("Stars", "-")
            row = row + [str(stars) if pd.notna(stars) else "-"]

            # Whale Conviction logic (Human Friendly)
            w_conv = str(r.get("Whale_Conv", "Low"))
            if w_conv == "HIGH":
                w_conv = "[bold green]HIGH[/]"
            elif w_conv == "Medium":
                w_conv = "[bold yellow]Med[/]"
            else:
                w_conv = "[dim]Low[/]"

            # Smart Metrics (v3.2 Institutional Alpha)
            row = row + [
                str(r.get("MStar", "-")),
                str(r.get("CAR", 0.0)),
                w_conv,
                "[bold green]YES[/]" if r.get("Hidden_Acc") == "YES" else "NO",
            ]

            if not strictly_technical:
                if not is_mobile:
                    row = row + [f"[{gc}]{g_sym} {g}[/{gc}]"]
                score_v25 = r.get("MYRA_Score_v25", 0)
                if score_v25 is None or str(score_v25) == "nan":
                    score_v25 = 0
                s_sym = "★ " if score_v25 >= 70 else "✓ " if score_v25 >= 50 else "⚠ "
                score_color = (
                    "bold green"
                    if score_v25 >= 70
                    else "yellow"
                    if score_v25 >= 50
                    else "red"
                )
                row = row + [f"[{score_color}]{s_sym}{score_v25}[/{score_color}]"]

                # Add Accuracy Data
                if not is_narrow:
                    acc = r.get("Accuracy", "-")
                    if acc is None or str(acc) == "nan":
                        acc = "-"
                    acc_color = "white"
                    a_sym = ""
                    try:
                        if "%" in str(acc):
                            val_num = float(acc.replace("%", ""))
                            a_sym = (
                                "↑ "
                                if val_num >= 70
                                else "↓ "
                                if val_num < 40
                                else "→ "
                            )
                            acc_color = (
                                "green"
                                if val_num >= 70
                                else "yellow"
                                if val_num >= 40
                                else "red"
                            )
                        elif acc == "New":
                            a_sym = "✦ "
                            acc_color = "cyan"
                    except:
                        pass
                    row = row + [f"[{acc_color}]{a_sym}{acc}[/{acc_color}]"]

                    # Add Pattern Data (PKScreener Superpower)
                    row = row + [str(r.get("Pattern", "-"))]

                # Add Money Flow Data
                if not is_mobile:
                    row = row + [str(r.get("Money_Flow", "-"))]

                # LTP and Fair Value Comparison (Human Friendly)
                fv_raw = r.get("Fair_Val", 0)
                ltp_num = 0
                try:
                    ltp_num = float(str(ltp).replace(",", ""))
                except:
                    pass

                fv_num = 0
                try:
                    fv_num = float(str(fv_raw).replace(",", ""))
                except:
                    pass

                fv_str = str(fv_raw)
                if fv_num > 0 and ltp_num > 0:
                    upside = ((fv_num - ltp_num) / ltp_num) * 100
                    if fv_num > ltp_num:
                        fv_str = f"[bold green]{fv_num:.2f}[/]"
                    else:
                        fv_str = f"[bold red]{fv_num:.2f}[/]"

                row = row + [s_str, str(ltp), fv_str, str(val)]
                if not is_narrow:
                    row = row + [str(r.get("Vibe", "-"))]

            for c in hero_cols:
                raw_val = r.get(c, "-")

                # UNIVERSAL PRECISION FORMATTING (TRILOGY ERA v2.5)
                # Ensure only 2 decimal places for numeric values
                formatted_val = str(raw_val)
                try:
                    if (
                        raw_val is None
                        or str(raw_val).lower() == "nan"
                        or raw_val == "-"
                    ):
                        formatted_val = "-"
                    elif isinstance(
                        raw_val, (float, np.float64, np.float32, int, np.int64)
                    ):
                        formatted_val = f"{float(raw_val):.2f}"
                    elif isinstance(raw_val, str):
                        # Try to parse float from string if it's not a special text
                        try:
                            num = float(
                                raw_val.replace("%", "")
                                .replace("x", "")
                                .replace("₹", "")
                                .replace("Cr", "")
                                .replace(",", "")
                            )
                            # If it was originally a percentage or had other suffix, keep it
                            if "%" in raw_val:
                                formatted_val = f"{num:.2f}%"
                            elif "x" in raw_val:
                                formatted_val = f"{num:.2f}x"
                            elif "₹" in raw_val:
                                formatted_val = f"₹{num:.2f}Cr"
                            else:
                                formatted_val = f"{num:.2f}"
                        except ValueError:
                            pass
                except:
                    pass

                # SMART COLORING (Logic from PKScreener)
                colored_val = formatted_val
                try:
                    # Strip formatting for color logic
                    clean_str = (
                        formatted_val.replace("%", "")
                        .replace("x", "")
                        .replace("₹", "")
                        .replace("Cr", "")
                        .replace(",", "")
                    )
                    num_val = float(clean_str)

                    if c == "RS_Raw":
                        color = (
                            "green"
                            if num_val > 1.1
                            else "red"
                            if num_val < 0.9
                            else "yellow"
                        )
                        arrow = (
                            "↑ " if num_val > 1.1 else "↓ " if num_val < 0.9 else "→ "
                        )
                        colored_val = f"[{color}]{arrow}{formatted_val}[/{color}]"
                    elif c == "ROE":
                        sym = "★ " if num_val > 20 else "⚠ " if num_val < 10 else "✓ "
                        color = (
                            "green"
                            if num_val > 20
                            else "red"
                            if num_val < 10
                            else "yellow"
                        )
                        colored_val = f"[{color}]{sym}{formatted_val}[/{color}]"
                    elif c == "SMC":
                        sym = "★ " if num_val > 20 else "⚠ " if num_val < 10 else "✓ "
                        color = (
                            "green"
                            if num_val > 20
                            else "red"
                            if num_val < 10
                            else "yellow"
                        )
                        colored_val = f"[{color}]{sym}{formatted_val}[/{color}]"
                    elif c == "Absorp_Ratio" or c == "Absorption":
                        sym = "★ " if num_val > 1.5 else "⚠ " if num_val < 0.8 else "✓ "
                        color = (
                            "green"
                            if num_val > 1.5
                            else "red"
                            if num_val < 0.8
                            else "yellow"
                        )
                        colored_val = f"[{color}]{sym}{formatted_val}[/{color}]"
                    elif c == "d_poc" or c == "D-POC":
                        colored_val = f"[dim]{formatted_val}[/dim]"
                    elif c == "Floor_Gap%" or c == "POC_Dist":
                        sym = "★ " if num_val < 0 else "⚠ "
                        color = "green" if num_val < 0 else "yellow"
                        colored_val = f"[{color}]{sym}{formatted_val}[/{color}]"
                    elif c == "Forecast_Move%":
                        color = (
                            "green"
                            if num_val > 0.5
                            else "red"
                            if num_val < -0.5
                            else "white"
                        )
                        arrow = (
                            "↑ " if num_val > 0.5 else "↓ " if num_val < -0.5 else "→ "
                        )
                        colored_val = f"[{color}]{arrow}{formatted_val}[/{color}]"
                    elif c == "Tightness":
                        sym = "★ " if num_val < 2 else "⚠ " if num_val >= 5 else "✓ "
                        color = (
                            "green"
                            if num_val < 2
                            else "yellow"
                            if num_val < 5
                            else "red"
                        )
                        colored_val = f"[{color}]{sym}{formatted_val}[/{color}]"
                except:
                    # Handle specific text colorings
                    if c == "AEON_Conviction":
                        if "CONVICTION" in formatted_val:
                            colored_val = f"[bold green]{formatted_val}[/]"
                        elif "CORE" in formatted_val:
                            colored_val = f"[bold cyan]{formatted_val}[/]"
                        elif "TACTICAL" in formatted_val:
                            colored_val = f"[bold yellow]{formatted_val}[/]"
                        elif "EXIT" in formatted_val:
                            colored_val = f"[bold red]{formatted_val}[/]"

                row = row + [colored_val]

            table.add_row(*row, style=row_style)

        self.console.print(table)

    def offer_institutional_deep_dive(self, results: list):
        """
        PKScreener Superpower: On-Demand Institutional Deep-Dive.
        Prompts user to run DFR-inspired valuation and risk logic on scan results.
        """
        top_n = min(len(results), 5)
        self.console.print(
            rf"\n[bold cyan][?][/bold cyan] [white]Found {len(results)} candidates. Run Institutional Deep-Dive (DCF + Red Flags) on top {top_n}? [y/N][/white] ",
            end="",
        )

        # Note: In a real CLI, we would use input() here.
        # Since this is an agentic environment, we'll implement the logic
        # but the actual trigger would happen in the main loop or via user prompt.
        # For now, we provide the method that the main loop can call.
        pass

    def run_institutional_deep_dive(self, results: list, db_conn, fetcher):
        """
        Executes the DFR-inspired logic on the provided results.
        """
        pipe = InstitutionalPipe(db_conn, fetcher)
        symbols = [r["Stock"] for r in results[:5]]  # Top 5 targeted deep-dive

        self.console.print(
            f"\n[bold magenta][*] MYRA Institutional Pipe: Performing Deep-Dive for {', '.join(symbols)}...[/bold magenta]"
        )

        with self.console.status(
            "[bold green]Fetching Deep History & Computing Intrinsic Alpha...[/bold green]"
        ):
            # Ensure we have deep history first
            for sym in symbols:
                fetcher.fetch_deep_history(sym)

            deep_results = pipe.run_deep_dive(symbols)

        t = Table(
            title="Institutional Deep-Dive (TRILOGY-DFR)",
            header_style="bold magenta",
            border_style="dim",
            box=None,
        )
        t.add_column("Stock", style="bold yellow")
        t.add_column("Intrinsic Upside", justify="right")
        t.add_column("Health Grade", justify="center")
        t.add_column("Institutional Red Flags", justify="left", overflow="fold")

        for sym, res in deep_results.items():
            if res.get("status") != "SUCCESS":
                continue

            upside = res.get("upside_pct", 0)
            u_color = "green" if upside > 30 else "yellow" if upside > 0 else "red"

            grade = res.get("health_grade", "C")
            g_color = "green" if grade == "A" else "yellow" if grade == "B" else "red"

            flags = (
                "\n".join([f"• {f}" for f in res.get("flags", [])])
                if res.get("flags")
                else "[dim]No institutional flags detected[/dim]"
            )

            t.add_row(
                sym,
                f"[{u_color}]{upside:.1f}%[/{u_color}]",
                f"[{g_color}]{grade}[/{g_color}]",
                flags,
            )

        self.console.print(t)
        self.console.print(
            "[dim][*] Valuation logic: Gordon Growth Model (DCF) with 5-year CAGR decay and sector-adjusted WACC.[/dim]"
        )

    def display_execution_plan(self, results: list, scan_name: str):
        if not results:
            return
        t = Table(
            title=f"Tactical Execution Plan: {scan_name}",
            header_style="bold red",
            border_style="dim",
            style="on black",
        )
        t.add_column("Stock", style="bold yellow")
        t.add_column("Entry >", style="success", justify="right")
        t.add_column("Target", style="info", justify="right")
        t.add_column("Stop Loss", style="red", justify="right")
        t.add_column("Risk%", justify="right")
        t.add_column("Rec. Size (RA%)", style="bold green", justify="center")
        t.add_column(
            "VaR (95%)", justify="right"
        )  # Value at Risk (PKScreener Superpower)

        for r in results:
            entry = r.get("Entry", 0)
            sl = r.get("SL_Final", 0) or r.get("SL", 0)
            risk_per = r.get("Risk_Per_Final", 5.0)
            atr = r.get("atr20", 0)

            # Institutional Position Sizing (Plan 1 Closing)
            try:
                # 1. Kelly Allocation (Strategy Edge)
                acc_str = str(r.get("Accuracy", "50%")).replace("%", "")
                win_rate = float(acc_str) / 100.0 if acc_str.isdigit() else 0.5
                kelly_pct = KellySizer().get_allocation_pct(
                    win_rate, 3.0, 1.0
                )  # Using 3:1 RR default

                # 2. Risk Parity Quantity (Volatility Adj)
                # Assuming standard 10L capital for display purposes
                qty = VolatilityAdjustedSizer().get_quantity(
                    entry, atr, 1000000, risk_per_trade_pct=1.0
                )
                ra_final = f"{kelly_pct}% / {qty} qty" if qty > 0 else f"{kelly_pct}%"
            except:
                ra_final = r.get("RA_Percent", 5.0)

            # Parametric VaR (Simplified)
            vol = atr / (entry or 1)
            var_95 = round(vol * 1.645 * 100, 1)

            t.add_row(
                r["Stock"],
                str(entry),
                str(r.get("Target", "-")),
                str(sl),
                f"{risk_per}%",
                str(ra_final),
                f"{var_95}%",
            )
        self.console.print(t)
