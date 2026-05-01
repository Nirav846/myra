import functools
import os
import threading
import warnings
from datetime import datetime

import pandas as pd

warnings.filterwarnings("ignore", message=".*deprecated now, and have no effect.*")
from rich.console import Console
from rich.panel import Panel

from myra_app.broker_parser import BrokerParser
from myra_app.engine import Engine
from myra_app.librarian import Librarian
from myra_app.positional_engine import PositionalScorer
from myra_app.results_manager import ResultsManager
from myra_core.utils.myra_log import myra_log


class MYRAScreener:
    """
    MYRA Stock Screener Orchestrator (v2.5)
    Coordinates Data, Analysis, and Reporting layers.
    Enhanced with v2.5 Positional Intelligence Engine.
    """

    def __init__(self, console: Console):
        self.console = console
        self.lib = Librarian(console=self.console)
        self.engine = Engine(self.lib)
        self.rm = ResultsManager(console)
        self.broker = BrokerParser(os.getcwd())
        # NO FUNDAMENTALS: Removed fundamental_ranker
        self.positional_scorer = PositionalScorer()
        # PERFORMANCE IMPROVEMENT: Cache for precompute_indicators
        self._indicator_cache = None
        self._indicator_cache_date = None
        self._cache_lock = threading.Lock()

    def _get_cached_indicators(self):
        """PERFORMANCE IMPROVEMENT: Cached wrapper for precompute_indicators."""
        # NO FUNDAMENTALS: Only technical indicators from Parquet lake
        latest_date = self.lib.get_max_price_date()
        
        with self._cache_lock:
            if self._indicator_cache_date == latest_date and self._indicator_cache is not None:
                return self._indicator_cache
        
        # Cache miss or first run
        try:
            df = self.lib.precompute_indicators()
            if df.empty:
                myra_log(0, 1, desc="Parquet lake empty")
                self.console.print(
                    "[warning][!] Parquet Indicator Lake is empty. Starting background refresh...[/warning]"
                )
                # PERFORMANCE IMPROVEMENT: Non-blocking background refresh
                def _background_refresh():
                    try:
                        self.lib.sync_market_data(history_years=0.02, skip_maintenance=True)
                        self.lib.start_background_sync(history_years=3)
                    except Exception as e:
                        self.console.print(f"[error]Background refresh failed: {e}[/error]")
                threading.Thread(target=_background_refresh, daemon=True).start()
                return df
            
            # Update cache
            with self._cache_lock:
                self._indicator_cache = df
                self._indicator_cache_date = latest_date
            return df
        except Exception as e:
            self.console.print(f"[error]Failed to build X-Ray: {e}[/error]")
            return pd.DataFrame()

    def run_market_xray(self):
        """Bird's Eye View of Market Health (PKScreener Superpower)"""
        self.console.print(
            "[info][MYRA] Generating Institutional Market X-Ray...[/info]"
        )

        symbols = self.lib.get_active_universe()
        if not symbols:
            return

        # 1. Trend Distribution & Volume (Direct SQL for speed and safety)
        # PERFORMANCE IMPROVEMENT: Use cached indicators
        df = self._get_cached_indicators()

        if df.empty:
            return

        # NO FUNDAMENTALS: Removed sector fetch and merge - pure technical analysis

        # 1. Trend Distribution
        total = len(df)
        above_200 = len(df[df["close"] > df["sma200"]]) if "sma200" in df.columns else 0
        above_50 = len(df[df["close"] > df["sma50"]]) if "sma50" in df.columns else 0

        # 2. Volume Sentiment
        high_vol = (
            len(df[df["volume"] > df["vol_sma50"] * 1.5])
            if "vol_sma50" in df.columns
            else 0
        )

        # 3. Breadth
        bullish_rsi = len(
            df[df["close"] > df["sma20"]]
        )  # Placeholder for momentum breadth

        # UI Display
        from rich.columns import Columns
        from rich.table import Table

        panels = [
            Panel(
                f"[bold green]{(above_200/total)*100:.1f}%[/bold green]\nStocks > SMA200",
                title="Long-Term Trend",
            ),
            Panel(
                f"[bold cyan]{(above_50/total)*100:.1f}%[/bold cyan]\nStocks > SMA50",
                title="Medium-Term Trend",
            ),
            Panel(
                f"[bold yellow]{high_vol}[/bold yellow]\nVolume Surges",
                title="Institutional Activity",
            ),
        ]

        self.console.print(
            Panel(
                Columns(panels),
                title="[bold white]MYRA Market X-Ray[/bold white]",
                border_style="magenta",
            )
        )

        # NO FUNDAMENTALS: Removed Sector Heatmap - pure technical analysis

    def display_data_status(self):
        """Displays a summary panel of data availability (Fix: PROMPT.txt UX)."""
        import datetime as _dt
        from datetime import datetime

        from rich.table import Table

        now = _dt.datetime.now()
        last_bhav = self.lib.get_max_price_date()

        expected_raw = self.lib.get_expected_trading_day(now)
        from myra_core.utils.date_utils import to_date

        expected = to_date(expected_raw)

        status_table = Table.grid(padding=(0, 1))
        status_table.add_column(style="dim")
        status_table.add_column()

        status_table.add_row("Expected (Trading Day):", str(expected))

        bhav_str = f"{last_bhav}"
        if last_bhav:
            try:
                lb_dt = to_date(last_bhav)
                if lb_dt < expected:
                    bhav_str += " [bold red]❌ (Delayed)[/]"
                else:
                    bhav_str += " [bold green]✅[/]"
            except:
                pass
        status_table.add_row("Bhavcopy:", bhav_str)

        # Institutional Intelligence replaces legacy Insider status
        status_table.add_row("Institutional:", "[bold green]LIVE (Morningstar)[/]")

        mode = "NORMAL"
        if last_bhav:
            try:
                lb_dt = to_date(last_bhav)
                if lb_dt < expected:
                    mode = "[bold yellow]STALE DATA MODE[/]"
            except:
                pass

        status_table.add_row("Mode:", mode)

        self.console.print(
            Panel(
                status_table,
                title="[bold cyan]MYRA DATA STATUS[/bold cyan]",
                border_style="cyan",
                expand=False,
            )
        )

    def execute_scan(
        self,
        strategy_id: str,
        display_name: str,
        sort_by: str = None,
        as_of_date: str = None,
        portfolio_symbols: list = None,
        is_piped: bool = False,
        scan_all: bool = False,
    ):
        # 0. Data Status Panel
        self.display_data_status()

        # 1. SMART SYNC: Only sync if missing relevant data
        last_import_raw = self.lib.get_max_price_date()
        from myra_core.utils.date_utils import to_date

        last_import = to_date(last_import_raw) if last_import_raw else None

        import datetime as _dt

        now = _dt.datetime.now()
        expected_date_raw = self.lib.get_expected_trading_day(now)
        expected_date = to_date(expected_date_raw)

        needs_sync = False
        if not last_import:
            needs_sync = True
        else:
            if last_import < expected_date:
                last_check_str = (
                    self.lib.get_metadata("last_sync_check") or "1900-01-01 00:00:00"
                )
                last_check = datetime.strptime(last_check_str, "%Y-%m-%d %H:%M:%S")
                if (now - last_check).total_seconds() > 7200:
                    needs_sync = True

        if needs_sync:
            # self.console.print("[dim][*] Market data stale. Synchronizing archives...[/dim]")
            # Performance Guard Compliant (Fix 186)
            self.lib.set_metadata(
                "last_sync_check", now.isoformat(sep=" ", timespec="seconds")
            )
            self.lib.sync_market_data(history_years=0.02, skip_maintenance=True)
            # self.console.print("[dim][*] History backfill started in background.[/dim]")
            self.lib.start_background_sync(history_years=3)

        # Resolve Universe
        symbols = []
        if portfolio_symbols:
            symbols = portfolio_symbols
            print(f"Scanning {len(symbols)} stocks from portfolio selection")
        elif strategy_id == "whale_tracker":
            symbols = self.lib.get_index_symbols("NIFTY 50")
            if not symbols:
                self.console.print(
                    "[warning][!] No NIFTY 50 data available. Please run index sync first.[/warning]"
                )
                return []
            print(f"Scanning {len(symbols)} stocks in NIFTY 50")
        elif strategy_id in [
            "super_setup",
            "insider_signals",
            "smart_money",
            "vsa_momentum",
            "large_deal_momentum",
        ]:
            symbols = self.lib.get_index_symbols("NIFTY 500")
            if not symbols:
                self.console.print(
                    "[warning][!] No NIFTY 500 data available. Please run index sync first.[/warning]"
                )
                return []
            print(f"Scanning {len(symbols)} stocks in NIFTY 500")
        elif scan_all:
            symbols = self.lib.get_all_symbols()
            print(f"Scanning {len(symbols)} stocks in Full Market")
        else:
            # Default: Institutional Core = NIFTY 500
            symbols = self.lib.get_index_symbols("NIFTY 500")
            if not symbols:
                self.console.print(
                    "[warning][!] No NIFTY 500 data available. Please run index sync first.[/warning]"
                )
                return []
            print(f"Scanning {len(symbols)} stocks in NIFTY 500")

        # DEBUG: Check symbols
        # self.console.print(f"[dim]DEBUG: Resolved {len(symbols)} symbols for scan.[/dim]")

        # 2. TECHNICAL SCAN
        from myra_app.feature_enrichment import pause_enrichment, resume_enrichment

        pause_enrichment()
        try:
            results, payload_map = self.engine.run_scan(
                symbols, strategy_id, as_of_date=as_of_date
            )
        finally:
            resume_enrichment()

        if not results:
            # ... (Stale data warning logic)
            return []

        # 3. Intelligence Buffer & Feature Enrichment (Tier 2 & 3)
        # We only deep-dive the TOP 20 results to stay fast/light on RAM
        from myra_app.utils.feature_enricher import FeatureEnricher

        # Get top 20 by Score (already sorted by engine usually, but we ensure it)
        top_20 = sorted(results, key=lambda x: x.get("Score", 0), reverse=True)[:20]

        enricher = FeatureEnricher(self.lib)
        enriched_top = enricher.enrich(top_20)

        # Merge enriched back into results
        enriched_map = {r["Stock"]: r for r in enriched_top}
        results = [enriched_map.get(r["Stock"], r) for r in results]

        # 4. INITIAL ENRICHMENT & RANKING
        # NO FUNDAMENTALS: Pure technical analysis only
        n100 = set(
            self.lib.get_index_symbols("NIFTY 50")
            + self.lib.get_index_symbols("NIFTY NEXT 50")
        )
        n500 = set(self.lib.get_index_symbols("NIFTY 500"))

        funda_map = {}  # Empty map - no fundamentals used
        is_smc_scan = strategy_id in [
            "126",
            "smart_money_ignition",
            "institutional_structural_flow",
            "multibagger_early",
            "fusion_engine",
        ]
        is_aeon_scan = strategy_id == "31"

        for r in results:
            sym = r["Stock"]

            # NO FUNDAMENTALS: Skip all fundamental data fetching
            # Set default values for display compatibility
            r["PE"] = "-"
            r["ROE"] = "-"
            r["Sector"] = "Unknown"
            r["MCap"] = 0
            r["ROCE"] = 0
            r["ProfitGrowth"] = 0

            if not (is_smc_scan or is_aeon_scan):
                # Use strategy-provided Grade if present, otherwise default
                if "Grade" not in r:
                    r["Grade"] = "C"
                r["MYRA_Score"] = r.get("Score", 50)
            else:
                # Use strategy-provided Grade if present
                if "Grade" not in r:
                    r["Grade"] = "SMC" if is_smc_scan else "AEON"
                r["MYRA_Score"] = 90

            con = r.get("Consensus", 0)
            r["Vibe"] = "High" if con >= 4 else "Moderate" if con >= 2 else "Low"

            # Star Logic (Respect strategy-provided stars if present)
            if "Stars" not in r:
                bonus = 0
                if sym in n100:
                    bonus = 2
                elif sym in n500:
                    bonus = 1

                if is_smc_scan:
                    # Strategy 35 (Multibagger) uses Score (0-100)
                    s_score = r.get("Score", 0)
                    if strategy_id == "multibagger_early" and s_score > 0:
                        stars_count = (
                            5
                            if s_score >= 80
                            else 4
                            if s_score >= 65
                            else 3
                            if s_score >= 50
                            else 2
                        )
                    else:
                        stars_count = 4 if r.get("smc_phase") == 2 else 3
                else:
                    stars_count = min(
                        5,
                        (3 if "A" in r.get("Grade", "C") else 1)
                        + (2 if r.get("Stage") == "Stage 2" else 0)
                        + bonus,
                    )

                r["Stars"] = "*" * stars_count

            if r.get("block_stars"):
                r["Stars"] = ""

            # Format Inst_Intensity for UI
            intens = r.get("Inst_Intensity", 0)
            # Safely convert the snapshot string to a number for comparison
            try:
                intens_val = float(intens)
            except (ValueError, TypeError):
                intens_val = 0

            r["Inst_Intensity"] = f"{int(intens_val)}%" if intens_val > 0 else "-"

            # Tactics
            tactics = r.get("tactics", {})
            # Use strategy-provided Entry/SL if present
            r["Entry"] = r.get("Entry") or tactics.get(
                "entry", round(r.get("LTP", 0) * 1.005, 2)
            )
            r["Target"] = r.get("T1") or tactics.get(
                "target", round(r["Entry"] * 1.15, 2)
            )
            r["SL_Final"] = r.get("SL") or tactics.get(
                "sl", round(r["Entry"] * 0.95, 2)
            )
            risk_per = (
                round(((r["Entry"] - r["SL_Final"]) / r["Entry"]) * 100, 1)
                if r["Entry"] > 0
                else 5.0
            )
            r["Risk_Per_Final"] = risk_per
            base_size = 5.0 / (risk_per / 2.0) if risk_per > 0 else 2.0
            r["RA_Percent"] = round(
                min(25, base_size * (1.0 + (0.2 * (len(r["Stars"]) - 1)))), 1
            )

        # 4. SELECT TOP CANDIDATES FOR DISPLAY
        results.sort(key=lambda x: x.get("MYRA_Score", 0), reverse=True)
        # NO FUNDAMENTALS: Removed deep fundamental audit

        # 5. FINAL POSITIONAL SCORING
        res_df = pd.DataFrame(results)
        regime = results[0].get("Market_Mood", "NEUTRAL") if results else "NEUTRAL"
        scored_df = self.positional_scorer.rank(res_df, regime)
        final_results = scored_df.to_dict("records")

        if sort_by and sort_by in final_results[0]:
            final_results.sort(
                key=lambda x: (x[sort_by] if x[sort_by] is not None else -999),
                reverse=True,
            )
        else:
            final_results.sort(
                key=lambda x: (x.get("MYRA_Score_v25", 0), len(x["Stars"])),
                reverse=True,
            )

        # 6. ENRICH FINAL TOP CANDIDATES WITH ACCURACY
        top_cand = final_results[:20]
        total_cand = len(top_cand)
        for i, r in enumerate(top_cand):
            s = r["Stock"]
            if s in payload_map:
                r["Accuracy"] = self.engine.calculate_accuracy(
                    s, strategy_id, payload_map[s], funda_map.get(s, {})
                )
            myra_log(i + 1, total_cand, desc="Success Probability")

        # 7. REGISTER SIGNALS FOR AUDIT (Trust Loop)
        self._register_signals(final_results, strategy_id, as_of_date)

        # Add Dist% column to all results
        for r in final_results:
            if "Dist%" not in r:
                entry = r.get("Entry")
                ltp = r.get("Ltp") or r.get("LTP") or r.get("Close")
                if entry and ltp and entry != 0:
                    try:
                        entry_val = float(entry)
                        ltp_val = float(ltp)
                        distance_pct = abs(ltp_val - entry_val) / entry_val * 100
                        r["Dist%"] = round(distance_pct, 2)
                    except (ValueError, TypeError, ZeroDivisionError):
                        r["Dist%"] = "-"

        # --- Still-in-Range Filter (Terminal Prompt) ---
        if final_results and not is_piped:
            apply_filter = (
                input("Filter results by Still-in-Range? (y/N): ").strip().lower()
            )
            if apply_filter == "y":
                try:
                    pct = float(
                        input("   Range percentage (default 3%): ").strip() or "3.0"
                    )
                except ValueError:
                    pct = 3.0
                filtered = self._filter_still_in_range(final_results, pct)
                if filtered:
                    self.console.print(
                        f"[bold green]→ Still-in-Range (≤{pct}%): {len(filtered)} stocks[/bold green]"
                    )
                    # Reprint the Rich table with filtered results
                    hero_cols = self._resolve_hero_columns(filtered)
                    self.rm.display_discovery_table(
                        filtered, display_name, strategy_id, hero_cols
                    )
                else:
                    self.console.print(
                        f"[warning][!] No stocks within {pct}% of entry.[/warning]"
                    )
                final_results = filtered

        return final_results

    def run_custom_scout(self):
        symbols = self._load_file(
            os.path.join(os.getcwd(), "data", "custom_watchlist.txt")
        )
        if not symbols:
            return
        self.console.print(
            f"[info][MYRA] Scouting {len(symbols)} targets with Technical Intelligence...[/info]"
        )
        # NO FUNDAMENTALS: Removed fundamental stale check and update
        results = self.execute_scan(
            "technicals", "Custom Scout", portfolio_symbols=symbols
        )
        # NO FUNDAMENTALS: Removed fundamental ranking
        if results:
            self.rm.display_discovery_table(
                results, "Custom Scout", "technicals", []
            )
            self.rm.archive_results(results, "Custom_Scout", strategy_id="technicals")

    def run_full_market_scout(self):
        self.console.print(
            "[info][MYRA] Scouting FULL MARKET (3000+ stocks) with Technical Intelligence...[/info]"
        )
        results = self.execute_scan("technicals", "Full Market Scout", scan_all=True)
        if results:
            self.rm.display_discovery_table(
                results, "Full Market Scout", "technicals", []
            )
            self.rm.archive_results(
                results, "Full_Market_Scout", strategy_id="technicals"
            )

    def run_portfolio_monitor(self):
        holdings = self.broker.parse_holdings()
        if not holdings:
            self.console.print("[warning][!] No Holdings file found.[/warning]")
            return
        symbols = [h["Stock"] for h in holdings]
        self.console.print(
            f"[info][MYRA] Monitoring {len(symbols)} active broker holdings...[/info]"
        )
        # NO FUNDAMENTALS: Removed fundamental stale check and update
        results = self.execute_scan(
            "all_pass", "Portfolio Monitor", portfolio_symbols=symbols
        )
        # NO FUNDAMENTALS: Removed fundamental ranking
        if results:
            broker_map = {h["Stock"]: h for h in holdings}
            for r in results:
                b = broker_map.get(r["Stock"])
                ltp = r.get("LTP", 0)
                ab = b.get("Avg_Price", 0)
                pnl = round(((ltp - ab) / ab) * 100, 1) if ab > 0 else 0
                pnl_color = "green" if pnl > 0 else "red" if pnl < 0 else "white"
                pnl_arrow = "↑ " if pnl > 0 else "↓ " if pnl < 0 else "→ "
                r["PnL%"] = f"[{pnl_color}]{pnl_arrow}{pnl}%[/{pnl_color}]"
                r["Funda_Score"] = 0  # NO FUNDAMENTALS
                tsl = r.get("SL", 0)
                stage = r.get("Stage", "-")
                ad_vibe = r.get("Closing_Vibe", "-")
                accel = r.get("AV_Accel", 0)
                status = "[bold green]HOLD[/bold green]"
                f_score = 0  # NO FUNDAMENTALS
                if ltp < tsl:
                    status = "[bold red]EXIT NOW[/bold red]"
                elif "Stage 4" in stage and accel == 0:
                    # NO FUNDAMENTALS: Simplified logic without f_score
                    status = "[bold red]SELL (Downtrend)[/bold red]"
                elif "Stage 3" in stage:
                    status = "[yellow]PROTECT (Topping)[/yellow]"
                elif pnl > 20 and ad_vibe == "Distribution":
                    status = "[bold yellow]BOOK PARTIAL[/bold yellow]"
                elif pnl < 0 and "Stage 2" in stage and accel > 0:
                    status = "[bold green]STRONG HOLD / ADD[/bold green]"
                elif pnl < -10:
                    status = "[bold blue]ACCUMULATE (Dip)[/bold blue]"
                r["Status"] = status
                r["TSL"] = tsl
            self.rm.display_discovery_table(
                results,
                "Active Broker Monitor",
                "portfolio_monitor",
                ["PnL%", "Status"],  # NO FUNDAMENTALS: Removed Funda_Score
            )
            self.rm.archive_results(results, "Broker_Monitor", strategy_id="all_pass")
            self.rm.display_portfolio_risk_audit(results)

    def _load_file(self, filename: str):
        if not os.path.exists(filename):
            return []
        with open(filename, "r") as f:
            content = f.read()
        return [
            s.strip().upper()
            for line in content.splitlines()
            if not line.startswith("#") and line.strip()
            for s in line.replace(",", " ").split()
        ]


    def _register_signals(
        self, results: list, strategy_id: str, as_of_date: str = None
    ):
        """Registers signals into the performance_audit table for the 'Trust Loop'."""
        if not results:
            return

        # We only audit institutional strategies (34=Surpriver, 126=SMC-1, etc.)
        audit_strategies = [
            "34",
            "126",
            "smart_money",
            "vsa_momentum",
            "large_deal_momentum",
            "31",
        ]
        is_inst = strategy_id in audit_strategies or strategy_id.startswith("3")
        # Exclude fusion engine (Option 36) from database writes to prevent bloat
        if strategy_id == "36":
            return
        if not is_inst:
            return

        # Performance Guard Compliant (Fix 451)
        signal_date = as_of_date if as_of_date else datetime.now().date().isoformat()

        registered_count = 0
        for r in results:
            symbol = r.get("Stock")
            ltp = r.get("LTP")
            sl = r.get("SL_Final") or r.get("SL")
            tp = r.get("Target")

            if not ltp:
                continue

            # If SL is missing, fallback to 5% below LTP
            if not sl:
                sl = round(ltp * 0.95, 2)
            # If TP is missing, fallback to 3:1 Reward/Risk
            if not tp:
                risk = ltp - sl
                tp = round(ltp + (3.0 * risk), 2)

            sql = """
                INSERT OR IGNORE INTO performance_audit (
                    symbol, strategy_id, signal_date, signal_price, sl_price, tp_price
                ) VALUES (?, ?, ?, ?, ?, ?)
            """
            try:
                self.lib.safe_execute(
                    sql, (symbol, strategy_id, signal_date, ltp, sl, tp)
                )
                registered_count += 1
            except Exception:
                pass

        if registered_count > 0:
            self.console.print(
                f"[dim][*] Trust Loop: Registered {registered_count} signals for performance audit.[/dim]"
            )

    def _filter_still_in_range(self, results, range_pct=3.0):
        """
        Filter results to keep only stocks within range_pct of their entry price.
        Returns filtered list, or original if Entry/LTP keys are missing.
        """
        filtered = []
        for r in results:
            entry = r.get("Entry")
            ltp = r.get("Ltp") or r.get("LTP") or r.get("Close")

            if entry is None or entry == 0:
                filtered.append(r)  # noqa: PG-APPEND
                continue

            if ltp is None:
                filtered.append(r)  # noqa: PG-APPEND
                continue

            try:
                entry_val = float(entry)
                ltp_val = float(ltp)
                distance_pct = abs(ltp_val - entry_val) / entry_val * 100

                if distance_pct <= range_pct:
                    r["Dist%"] = round(distance_pct, 2)
                    filtered.append(r)  # noqa: PG-APPEND
            except (ValueError, TypeError, ZeroDivisionError):
                filtered.append(r)  # noqa: PG-APPEND
        return filtered

    def _resolve_hero_columns(self, results):
        """
        Extract hero columns from results for display_discovery_table.
        Returns empty list by default (uses standard columns).
        """
        return ["Dist%"]  # Add "Dist%" as a hero column
        return []

    def close(self):
        self.lib.close()
