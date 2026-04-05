import os
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings("ignore", message=".*deprecated now, and have no effect.*")
from rich.console import Console
from rich.panel import Panel
from tqdm import tqdm
from myra_app.librarian import Librarian
from myra_app.engine import Engine
from myra_app.results_manager import ResultsManager
from myra_app.broker_parser import BrokerParser
from myra_app.fundamental_ranker import FundamentalRanker
from myra_app.positional_engine import PositionalScorer

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
        self.ranker = self.lib.fundamental_ranker
        self.positional_scorer = PositionalScorer()

    def run_market_xray(self):
        """Bird's Eye View of Market Health (PKScreener Superpower)"""
        self.console.print("[info][MYRA] Generating Institutional Market X-Ray...[/info]")
        
        symbols = self.lib.get_active_universe()
        if not symbols: return
        
        # 1. Trend Distribution & Volume (Direct SQL for speed and safety)
        sql = """
            WITH latest_indicators AS (
                SELECT c.*, p.close, p.volume
                FROM calculated_indicators c
                JOIN prices p ON c.symbol = p.symbol AND c.date = p.date
                WHERE c.date = (SELECT MAX(date) FROM calculated_indicators)
                AND c.symbol IN (SELECT symbol FROM symbols_master WHERE in_active_universe = TRUE)
            )
            SELECT * FROM latest_indicators
        """
        try:
            df = self.lib.conn.execute(sql).df()
        except Exception as e:
            self.console.print(f"[error]Failed to build X-Ray: {e}[/error]")
            return
            
        if df.empty: return
        
        # Merge with fundamentals to get Sector
        funda = self.lib.conn.execute("SELECT symbol, sector FROM fundamentals").df()
        if not funda.empty:
            df = df.merge(funda, on='symbol', how='left')
        
        # 1. Trend Distribution
        total = len(df)
        above_200 = len(df[df['close'] > df['sma200']]) if 'sma200' in df.columns else 0
        above_50 = len(df[df['close'] > df['sma50']]) if 'sma50' in df.columns else 0
        
        # 2. Volume Sentiment
        high_vol = len(df[df['volume'] > df['vol_sma50'] * 1.5]) if 'vol_sma50' in df.columns else 0
        
        # 3. Breadth
        bullish_rsi = len(df[df['close'] > df['sma20']]) # Placeholder for momentum breadth
        
        # UI Display
        from rich.columns import Columns
        from rich.table import Table
        
        panels = [
            Panel(f"[bold green]{(above_200/total)*100:.1f}%[/bold green]\nStocks > SMA200", title="Long-Term Trend"),
            Panel(f"[bold cyan]{(above_50/total)*100:.1f}%[/bold cyan]\nStocks > SMA50", title="Medium-Term Trend"),
            Panel(f"[bold yellow]{high_vol}[/bold yellow]\nVolume Surges", title="Institutional Activity")
        ]
        
        self.console.print(Panel(Columns(panels), title="[bold white]MYRA Market X-Ray[/bold white]", border_style="magenta"))
        
        # 4. Sector Heatmap (New PKScreener Idea)
        if 'Sector' in df.columns:
            sector_stats = []
            for sector, group in df.groupby('Sector'):
                if not sector or sector == 'Unknown' or pd.isna(sector): continue
                s_total = len(group)
                if s_total < 5: continue # Skip tiny sectors
                s_bullish = len(group[group['close'] > group['sma50']])
                s_perc = (s_bullish / s_total) * 100
                sector_stats.append((sector, s_total, s_perc))
            
            if sector_stats:
                st = Table(title="Sector Leadership Heatmap", header_style="bold cyan", border_style="dim")
                st.add_column("Sector"); st.add_column("Count", justify="right"); st.add_column("Trend Strength", justify="center")
                
                for s, count, perc in sorted(sector_stats, key=lambda x: -x[2]):
                    color = "green" if perc > 70 else "yellow" if perc > 45 else "red"
                    bar = "█" * int(perc / 5)
                    st.add_row(str(s), str(count), f"[{color}]{bar} {perc:.1f}%[/{color}]")
                
                self.console.print(st)

    def display_data_status(self):
        """Displays a summary panel of data availability (Fix: PROMPT.txt UX)."""
        from rich.table import Table
        from datetime import datetime
        
        import datetime as _dt
        now = _dt.datetime.now()
        last_bhav = self.lib.get_max_price_date()
        last_insider = self.lib.get_max_insider_date()
        
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
                if lb_dt < expected: bhav_str += " [bold red]❌ (Delayed)[/]"
                else: bhav_str += " [bold green]✅[/]"
            except: pass
        status_table.add_row("Bhavcopy:", bhav_str)
        
        insider_str = f"{last_insider}"
        if last_insider:
            try:
                li_dt = to_date(last_insider)
                if li_dt < expected: insider_str += " [bold yellow]🟡[/]"
                else: insider_str += " [bold green]✅[/]"
            except: pass
        status_table.add_row("Insider:", insider_str)
        
        mode = "NORMAL"
        if last_bhav:
            try:
                lb_dt = to_date(last_bhav)
                if lb_dt < expected: mode = "[bold yellow]STALE DATA MODE[/]"
            except: pass
            
        status_table.add_row("Mode:", mode)
        
        self.console.print(Panel(status_table, title="[bold cyan]MYRA DATA STATUS[/bold cyan]", border_style="cyan", expand=False))

    def execute_scan(self, strategy_id: str, display_name: str, sort_by: str = None, 
                     as_of_date: str = None, portfolio_symbols: list = None, 
                     is_piped: bool = False, scan_all: bool = False):
        
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
                last_check_str = self.lib.get_metadata("last_sync_check") or "1900-01-01 00:00:00"
                last_check = datetime.strptime(last_check_str, "%Y-%m-%d %H:%M:%S")
                if (now - last_check).total_seconds() > 7200:
                    needs_sync = True
            
        if needs_sync:
            # self.console.print("[dim][*] Market data stale. Synchronizing archives...[/dim]")
            self.lib.set_metadata("last_sync_check", now.strftime('%Y-%m-%d %H:%M:%S'))
            self.lib.sync_market_data(history_years=0.02, skip_maintenance=True)
            # self.console.print("[dim][*] History backfill started in background.[/dim]")
            self.lib.start_background_sync(history_years=3)
        
        # Resolve Universe
        symbols = []
        if portfolio_symbols:
            symbols = portfolio_symbols
        elif strategy_id == "whale_tracker":
            symbols = self.lib.get_index_symbols("NIFTY 50")
        elif strategy_id in ["super_setup", "insider_signals", "smart_money", "vsa_momentum", "large_deal_momentum"]:
            symbols = self.lib.get_index_symbols("NIFTY 500")
        
        if not symbols:
            if scan_all:
                symbols = self.lib.get_all_symbols()
            else:
                symbols = self.lib.get_active_universe()
                if not symbols: symbols = self.lib.get_all_symbols()
        
        # DEBUG: Check symbols
        # self.console.print(f"[dim]DEBUG: Resolved {len(symbols)} symbols for scan.[/dim]")
            
        # 2. TECHNICAL SCAN
        with self.console.status(f"[bold magenta][MYRA] Computing Technical Intelligence for {len(symbols)} stocks...[/bold magenta]", spinner="dots"):
            results, payload_map = self.engine.run_scan(symbols, strategy_id, as_of_date=as_of_date)
            
        if not results:
            # Check data age (Fix: UX improvement for stale data)
            from datetime import datetime
            max_date = self.lib.get_max_price_date()
            if max_date:
                try:
                    md = datetime.strptime(max_date, "%Y-%m-%d").date()
                    age = (datetime.now().date() - md).days
                    if age > 1:
                        self.console.print(f"[bold yellow][!] WARNING: Latest Bhavcopy is from {max_date} ({age} days old).[/bold yellow]")
                        self.console.print(f"[dim]Note: Strategy '{display_name}' depends on fresh volume/delivery data. Zero candidates is expected on stale data.[/dim]")
                except: pass

            self.console.print(f"[warning][!] Scan Complete: 0 candidates identified for '{display_name}'.[/warning]")
            return []

        # 3. INITIAL ENRICHMENT & RANKING
        sector_stats = self.lib.get_sector_stats()
        n100 = set(self.lib.get_index_symbols("NIFTY 50") + self.lib.get_index_symbols("NIFTY NEXT 50"))
        n500 = set(self.lib.get_index_symbols("NIFTY 500"))

        funda_map = {} # Store for accuracy enrichment
        is_smc_scan = strategy_id in ["126", "smart_money_ignition", "institutional_structural_flow", "multibagger_early"]
        is_aeon_scan = strategy_id == "31"
        
        for r in results:
            sym = r["Stock"]
            
            # Hybrid Load: Always get Core Identity (MCap/Sector), skip Heavy Technico-Fundamentals if SMC/AEON
            f = self.lib.get_fundamentals(sym)
            
            if is_smc_scan or is_aeon_scan:
                # Keep core identity but mask heavy fundamentals to avoid NULL noise in specialized scans
                f_mask = {
                    "PE": f.get("PE", "-"), 
                    "ROE": f.get("ROE", "-"), 
                    "Sector": f.get("Sector", "ML-Specialist"), 
                    "MCap": f.get("MCap", 0)
                }
                r.update(f_mask)
            else:
                r.update(f)
            
            funda_map[sym] = f
            r["ROCE"] = f.get("ROE", 0) 
            r["ProfitGrowth"] = f.get("ProfitGrowth", 0)
            
            if not (is_smc_scan or is_aeon_scan):
                score, grade = self._calculate_grade(f, sector_stats)
                r["Grade"], r["MYRA_Score"] = grade, score
            else:
                # Use strategy-provided Grade if present
                if "Grade" not in r:
                    r["Grade"] = "SMC" if is_smc_scan else "AEON"
                r["MYRA_Score"] = 90
            
            con = r.get("Consensus", 0); r["Vibe"] = "High" if con >= 4 else "Moderate" if con >= 2 else "Low"
            
            # Star Logic (Respect strategy-provided stars if present)
            if "Stars" not in r:
                bonus = 0
                if sym in n100: bonus = 2
                elif sym in n500: bonus = 1
                
                if is_smc_scan:
                    # Strategy 35 (Multibagger) uses Score (0-100)
                    s_score = r.get("Score", 0)
                    if strategy_id == "multibagger_early" and s_score > 0:
                        stars_count = 5 if s_score >= 80 else 4 if s_score >= 65 else 3 if s_score >= 50 else 2
                    else:
                        stars_count = 4 if r.get("smc_phase") == 2 else 3
                else:
                    stars_count = min(5, (3 if "A" in r.get("Grade","C") else 1) + (2 if r.get("Stage") == "Stage 2" else 0) + bonus)
                
                r["Stars"] = "*" * stars_count
            
            if r.get("block_stars"): r["Stars"] = ""
            
            # Format Inst_Intensity for UI
            intens = r.get("Inst_Intensity", 0)
            r["Inst_Intensity"] = f"{intens}%" if intens > 0 else "-"
            
            # Tactics
            tactics = r.get("tactics", {})
            # Use strategy-provided Entry/SL if present
            r["Entry"] = r.get("Entry") or tactics.get('entry', round(r.get("LTP", 0) * 1.005, 2))
            r["Target"] = r.get("T1") or tactics.get('target', round(r["Entry"] * 1.15, 2))
            r["SL_Final"] = r.get("SL") or tactics.get('sl', round(r["Entry"] * 0.95, 2))
            risk_per = round(((r["Entry"] - r["SL_Final"]) / r["Entry"]) * 100, 1) if r["Entry"] > 0 else 5.0
            r["Risk_Per_Final"] = risk_per
            base_size = 5.0 / (risk_per / 2.0) if risk_per > 0 else 2.0
            r["RA_Percent"] = round(min(25, base_size * (1.0 + (0.2 * (len(r["Stars"]) - 1)))), 1)

        # 4. SELECT TOP CANDIDATES FOR DEEP FUNDAMENTAL AUDIT (PKScreener Superpower)
        results.sort(key=lambda x: x.get("MYRA_Score", 0), reverse=True)
        top_20 = results[:20]
        
        # Optimization: Fetch F-Score and Graham Numbers in bulk to prevent N+1 Queries
        top_20_symbols = [r["Stock"] for r in top_20]
        bulk_f_scores = self.lib.fundamental_manager.get_bulk_f_scores(top_20_symbols)
        bulk_val_metrics = self.lib.fundamental_manager.get_bulk_valuation_metrics(top_20_symbols)

        for r in tqdm(top_20, desc="Deep Fundamental Audit", leave=False):
            sym = r["Stock"]
            sym_clean = sym.split('.')[0].upper()
            r["F_Score"] = bulk_f_scores.get(sym_clean, 0)
            r["graham_number"] = bulk_val_metrics.get(sym_clean, {}).get("graham_number", 0)

        # 5. FINAL POSITIONAL SCORING
        res_df = pd.DataFrame(results)
        regime = results[0].get("Market_Mood", "NEUTRAL") if results else "NEUTRAL"
        scored_df = self.positional_scorer.rank(res_df, regime)
        final_results = scored_df.to_dict('records')

        if sort_by and sort_by in final_results[0]: 
            final_results.sort(key=lambda x: (x[sort_by] if x[sort_by] is not None else -999), reverse=True)
        else: 
            final_results.sort(key=lambda x: (x.get("MYRA_Score_v25", 0), len(x["Stars"])), reverse=True)
            
        # 6. ENRICH FINAL TOP CANDIDATES WITH ACCURACY
        top_cand = final_results[:20]
        for r in tqdm(top_cand, desc="Calculating Success Probability", leave=False):
            s = r["Stock"]
            if s in payload_map:
                r["Accuracy"] = self.engine.calculate_accuracy(s, strategy_id, payload_map[s], funda_map.get(s, {}))

        # 7. REGISTER SIGNALS FOR AUDIT (Trust Loop)
        self._register_signals(final_results, strategy_id, as_of_date)

        return final_results

    def run_custom_scout(self):
        symbols = self._load_file(os.path.join(os.getcwd(), "data", "custom_watchlist.txt"))
        if not symbols: return
        self.console.print(f"[info][MYRA] Scouting {len(symbols)} targets with Fundamental Intelligence...[/info]")
        stale = [s for s in symbols if self.lib.fundamental_manager.is_stale(s, days=90)]
        if stale:
            self.console.print(f"[dim][*] Updating {len(stale)} stale fundamental profiles...[/dim]")
            self.lib.update_quarterly_fundamentals(stale)
        results = self.execute_scan("technicals", "Custom Scout", portfolio_symbols=symbols)
        funda_df = self.ranker.rank(symbols)
        if not funda_df.empty:
            funda_map = funda_df.set_index('Stock')['Funda_Score'].to_dict()
            for r in results:
                s = r["Stock"]; clean_s = s.split('.')[0].upper()
                r["Funda_Score"] = funda_map.get(s, funda_map.get(clean_s, 0))
        if results:
            self.rm.display_discovery_table(results, "Custom Scout", "technicals", ["Funda_Score"])
            self.rm.archive_results(results, "Custom_Scout")

    def run_full_market_scout(self):
        self.console.print("[info][MYRA] Scouting FULL MARKET (3000+ stocks) with Technical Intelligence...[/info]")
        results = self.execute_scan("technicals", "Full Market Scout", scan_all=True)
        if results:
            self.rm.display_discovery_table(results, "Full Market Scout", "technicals", [])
            self.rm.archive_results(results, "Full_Market_Scout")

    def run_portfolio_monitor(self):
        holdings = self.broker.parse_holdings()
        if not holdings:
            self.console.print("[warning][!] No Holdings file found.[/warning]"); return
        symbols = [h['Stock'] for h in holdings]
        self.console.print(f"[info][MYRA] Monitoring {len(symbols)} active broker holdings...[/info]")
        stale = [s for s in symbols if self.lib.fundamental_manager.is_stale(s, days=90)]
        if stale:
            self.console.print(f"[dim][*] Updating {len(stale)} stale fundamental profiles...[/dim]")
            self.lib.update_quarterly_fundamentals(stale)
        results = self.execute_scan("all_pass", "Portfolio Monitor", portfolio_symbols=symbols)
        funda_df = self.ranker.rank(symbols)
        funda_map = funda_df.set_index('Stock')['Funda_Score'].to_dict() if not funda_df.empty else {}
        if results:
            broker_map = {h['Stock']: h for h in holdings}
            for r in results:
                b = broker_map.get(r["Stock"]); ltp = r.get("LTP", 0); ab = b.get("Avg_Price", 0)
                pnl = round(((ltp - ab) / ab) * 100, 1) if ab > 0 else 0
                pnl_color = 'green' if pnl > 0 else 'red' if pnl < 0 else 'white'
                pnl_arrow = '↑ ' if pnl > 0 else '↓ ' if pnl < 0 else '→ '
                r["PnL%"] = f"[{pnl_color}]{pnl_arrow}{pnl}%[/{pnl_color}]"
                r["Funda_Score"] = funda_map.get(r["Stock"], 0)
                tsl = r.get("SL", 0); stage = r.get("Stage", "-")
                ad_vibe = r.get("Closing_Vibe", "-"); accel = r.get("AV_Accel", 0)
                status = "[bold green]HOLD[/bold green]"
                f_score = r["Funda_Score"]
                if ltp < tsl: status = "[bold red]EXIT NOW[/bold red]"
                elif "Stage 4" in stage and accel == 0:
                    if f_score > 0 and f_score < 40: status = "[bold red]SELL (Weak Fundamentals)[/bold red]"
                    elif f_score == 0: status = "[bold red]SELL (Downtrend)[/bold red]"
                    else: status = "[bold yellow]HOLD (Quality Lagging)[/bold yellow]"
                elif "Stage 3" in stage: status = "[yellow]PROTECT (Topping)[/yellow]"
                elif pnl > 20 and ad_vibe == "Distribution": status = "[bold yellow]BOOK PARTIAL[/bold yellow]"
                elif pnl < 0 and "Stage 2" in stage and accel > 0: status = "[bold green]STRONG HOLD / ADD[/bold green]"
                elif f_score > 70 and pnl < -10: status = "[bold blue]ACCUMULATE (Quality Dip)[/bold blue]"
                r["Status"] = status; r["TSL"] = tsl
            self.rm.display_discovery_table(results, "Active Broker Monitor", "portfolio_monitor", ["PnL%", "Funda_Score", "Status"])
            self.rm.archive_results(results, "Broker_Monitor")
            self.rm.display_portfolio_risk_audit(results)

    def _load_file(self, filename: str):
        if not os.path.exists(filename): return []
        with open(filename, "r") as f: content = f.read()
        return [s.strip().upper() for line in content.splitlines() if not line.startswith("#") and line.strip() for s in line.replace(",", " ").split()]

    def _calculate_grade(self, stock_funda, sector_stats):
        if not sector_stats: return 50, "C"
        sect = stock_funda.get("Sector")
        if sect not in sector_stats: return 50, "C"
        stats = sector_stats[sect]; ts, f = 0, 0
        roe = stock_funda.get("ROE")
        if roe and roe != "NULL" and "roe" in stats:
            z = (roe - stats["roe"]["mean"]) / stats["roe"]["std"]; ts += max(0, min(100, 50 + (z * 20))); f += 1
        pe = stock_funda.get("PE")
        if pe and pe != "NULL" and "pe" in stats:
            z = (pe - stats["pe"]["mean"]) / stats["pe"]["std"]; ts += max(0, min(100, 50 - (z * 20))); f += 1
        if f == 0: return 50, "C"
        fs = round(ts / f, 1)
        if fs >= 80: return fs, "A"
        elif fs >= 60: return fs, "B"
        elif fs >= 40: return fs, "C"
        return fs, "F"

    def _register_signals(self, results: list, strategy_id: str, as_of_date: str = None):
        """Registers signals into the performance_audit table for the 'Trust Loop'."""
        if not results: return
        
        # We only audit institutional strategies (34=Surpriver, 126=SMC-1, etc.)
        audit_strategies = ["34", "126", "smart_money", "vsa_momentum", "large_deal_momentum", "31"]
        is_inst = strategy_id in audit_strategies or strategy_id.startswith("3")
        if not is_inst: return

        signal_date = as_of_date if as_of_date else datetime.now().strftime('%Y-%m-%d')
        
        registered_count = 0
        for r in results:
            symbol = r.get("Stock")
            ltp = r.get("LTP")
            sl = r.get("SL_Final") or r.get("SL")
            tp = r.get("Target")
            
            if not ltp: continue
            
            # If SL is missing, fallback to 5% below LTP
            if not sl: sl = round(ltp * 0.95, 2)
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
                self.lib.safe_execute(sql, (symbol, strategy_id, signal_date, ltp, sl, tp))
                registered_count += 1
            except Exception: pass
            
        if registered_count > 0:
            self.console.print(f"[dim][*] Trust Loop: Registered {registered_count} signals for performance audit.[/dim]")

    def close(self): self.lib.close()
