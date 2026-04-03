# myra_app/institutional_pipe.py
import json
import os
import pandas as pd
import numpy as np
from datetime import date, datetime
from .fundamental_manager import FundamentalManager

class InstitutionalPipe:
    """
    MYRA Institutional Deep-Dive Pipe (TRILOGY-DFR)
    A secondary, on-demand analysis engine for high-conviction technical setups.
    Updated for Modular Architecture v3.0.
    """
    def __init__(self, librarian=None, fetcher=None):
        self.lib = librarian
        self.fetcher = fetcher
        self.config = self._load_config()
        self.funda_manager = FundamentalManager(fetcher=fetcher)
        if librarian and librarian._val_conn:
            self.funda_manager.set_connection(librarian._val_conn)

    def _load_config(self):
        config_path = os.path.join(os.getcwd(), "config", "valuation_rules.json")
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception:
            return {
                "valuation_settings": {"risk_free_rate": 0.07, "terminal_growth_rate": 0.04, "growth_decay_factor": 0.95, "cagr_cap": 0.15},
                "sector_wacc_adjustments": {"default": 0.12},
                "risk_thresholds": {"max_pledge_pct": 0.25, "min_interest_coverage": 3.0, "max_profit_cash_divergence": 2.0, "max_debtor_days_deviation": 1.5}
            }

    def run_deep_dive(self, symbols):
        """Orchestrates the deep-dive for a list of candidate symbols."""
        results = {}
        for symbol in symbols:
            results[symbol] = self.analyze_symbol(symbol)
        return results

    def analyze_symbol(self, symbol):
        """Performs DCF, Red Flag, and Insider Conviction analysis."""
        symbol_clean = symbol.split('.')[0].upper()
        
        # A. Fundamental Context (from valuation.db)
        funda_data = self._get_funda_history(symbol_clean)
        
        # B. Intrinsic Valuation (DFR Logic)
        valuation = self._calculate_dcf(symbol_clean, funda_data)
        
        # C. Health Audit (Red Flags + Insider Conviction)
        health = self._audit_health(symbol_clean, funda_data)
        
        return {
            "symbol": symbol_clean,
            "intrinsic_value": valuation.get("fair_value"),
            "upside_pct": valuation.get("upside_pct"),
            "health_grade": health.get("grade"),
            "flags": health.get("flags"),
            "status": "SUCCESS" if not funda_data.empty else "INSUFFICIENT_DATA"
        }

    def _get_funda_history(self, symbol):
        """Retrieves quarterly history from modular valuation.db."""
        if not self.lib or not self.lib._val_conn: return pd.DataFrame()
        query = "SELECT * FROM quarterly_results WHERE symbol = ? ORDER BY report_date DESC"
        return pd.read_sql(query, self.lib._val_conn, params=(symbol,))

    def _calculate_dcf(self, symbol, df):
        """ Gordon Growth Model with Forward Projection. """
        if df.empty or not self.lib: return {"fair_value": 0, "upside_pct": 0}
        
        settings = self.config["valuation_settings"]
        growth_rate = self._estimate_growth(df)
        growth_rate = min(growth_rate, settings["cagr_cap"])
        
        sector = self._get_sector(symbol)
        wacc = self.config["sector_wacc_adjustments"].get(sector.lower(), self.config["sector_wacc_adjustments"]["default"])
        
        current_fcf = df.iloc[0].get('net_profit', 0) # Proxy
        if current_fcf <= 0: return {"fair_value": 0, "upside_pct": 0}

        projections = []
        temp_fcf = current_fcf
        temp_growth = growth_rate
        for _ in range(5):
            temp_fcf = temp_fcf * (1 + temp_growth)
            projections.append(temp_fcf / ((1 + wacc) ** (_ + 1)))
            temp_growth *= settings["growth_decay_factor"]

        g = settings["terminal_growth_rate"]
        terminal_value = (temp_fcf * (1 + g)) / (wacc - g)
        terminal_value_pv = terminal_value / ((1 + wacc) ** 5)
        intrinsic_ev = sum(projections) + terminal_value_pv
        
        res = self.lib._val_conn.execute("SELECT market_cap FROM fundamentals WHERE symbol = ?", (symbol,)).fetchone()
        mcap = res[0] if res else 0
        upside = ((intrinsic_ev / mcap) - 1) * 100 if mcap > 0 else 0
        
        return {"fair_value": intrinsic_ev, "upside_pct": round(upside, 2)}

    def _audit_health(self, symbol, df):
        """ Red Flag Monitoring + Insider Conviction. """
        thresholds = self.config["risk_thresholds"]
        flags = []
        
        if not df.empty:
            latest = df.iloc[0]
            # 1. Cash Flow Divergence
            if latest.get('net_profit', 0) > 0 and latest.get('cash_from_ops'):
                ratio = latest.get('net_profit', 0) / latest.get('cash_from_ops', 1)
                if ratio > thresholds["max_profit_cash_divergence"]:
                    flags.append("ACCRUAL_HEAVY: Net Profit > Cash Flow")

            # 2. Pledging
            if latest.get('pledged_pct', 0) > thresholds["max_pledge_pct"]:
                flags.append(f"HIGH_PLEDGE: {latest.get('pledged_pct')}% shares pledged")

        # 2.1 Structural Governance Audit (New)
        try:
            from myra_app.ias_manager import IASManager
            ias_mgr = IASManager()
            gov_flags = ias_mgr.run_governance_audit(symbol)
            for gf in gov_flags:
                # Strip rich tags for internal list, will be re-added by RM if needed
                clean_f = gf.replace("[red]", "").replace("[yellow]", "").replace("[/]", "")
                flags.append(clean_f)
        except: pass

        # 3. Insider Conviction (Modular Integration)
        if self.lib and self.lib._inst_conn:
            # Check for Underwater Promoters (Alpha Signal)
            # Find latest material buy > 0.1Cr (10L)
            res = self.lib._inst_conn.execute("""
                SELECT avg_price, value_cr, date FROM insider_trades 
                WHERE symbol = ? AND type = 'Buy' AND value_cr > 0.1 
                ORDER BY date DESC LIMIT 1
            """, (symbol,)).fetchone()
            
            if res:
                avg_buy, val, dt = res
                # Get current price from technical.db
                p_res = self.lib._tech_conn.execute("SELECT close FROM technical_data WHERE symbol = ? ORDER BY date DESC LIMIT 1", (symbol,)).fetchone()
                if p_res:
                    ltp = p_res[0]
                    if ltp < avg_buy:
                        discount = round((1 - ltp/avg_buy)*100, 1)
                        flags.append(f"UNDERWATER_PROMOTER: LTP is {discount}% BELOW insider buy (Price: {avg_buy})")
                    else:
                        flags.append(f"PROMOTER_BACKED: Recent material buy of ₹{round(val,2)}Cr at {avg_buy}")

        grade = "A" if not flags else "B" if len(flags) < 2 else "C" if len(flags) < 4 else "F"
        return {"grade": grade, "flags": flags}

    def _estimate_growth(self, df):
        if len(df) < 4: return 0.10
        try:
            latest, old = df.iloc[0].get('net_profit', 1), df.iloc[-1].get('net_profit', 1)
            if latest > 0 and old > 0:
                return (latest / old) ** (1/min(len(df)/4, 3)) - 1
        except: pass
        return 0.10

    def _get_sector(self, symbol):
        if not self.lib or not self.lib._val_conn: return "default"
        res = self.lib._val_conn.execute("SELECT sector FROM fundamentals WHERE symbol = ?", (symbol,)).fetchone()
        return res[0] if res and res[0] else "default"
