#!/usr/bin/env python
"""
MYRA Engine - The Processing Coordinator (UNIVERSAL SQL v12)
Unified SQL precompute for both standard and piped (silent) scans.
"""
import multiprocessing
import importlib
import warnings
import signal
import sys
import os
import threading
import time
import pandas as pd
import numpy as np
from datetime import date, datetime
from typing import List, Dict, Any
from myra_app.data_adapter import DataAdapter
from myra_core.utils.myra_log import myra_log

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings(
    "ignore",
    message=".*deprecated now, and have no effect.*",
    category=DeprecationWarning,
)


def run_with_hard_timeout(func, args, timeout=30):
    """
    Isolated Process Wrapper (Fix 4).
    Forcibly kills workers that hang at the C-extension level (scrapling/curl_cffi).
    DEPRECATED: Use multiprocessing.Pool for high-throughput scans.
    """
    import multiprocessing as mp

    q = mp.Queue()

    def wrapper(q, *args):
        try:
            res = func(*args)
            q.put(res)
        except Exception:
            q.put(None)

    p = mp.Process(target=wrapper, args=(q, *args))
    p.start()
    p.join(timeout)

    if p.is_alive():
        p.terminate()
        p.join()
        return None

    return q.get() if not q.empty() else None


class ScanWatchdog(threading.Thread):
    """
    Progress Watchdog (Fix 7).
    Detects if the scanner has stopped making progress for too long.
    """

    def __init__(self, timeout=60):
        super().__init__(daemon=True)
        self.last_hit = time.time()
        self.timeout = timeout
        self._running = True

    def poke(self):
        self.last_hit = time.time()

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            if time.time() - self.last_hit > self.timeout:
                print(
                    f"\n[CRITICAL] SCANNER STUCK FOR {self.timeout}s! Emergency abort."
                )
                os._exit(1)
            time.sleep(10)


_worker_strategy = None
_worker_adapter = None


def init_worker(strategy_name, db_path=None):
    global _worker_strategy, _worker_adapter
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    warnings.filterwarnings("ignore", category=UserWarning)

    # Separation: 101+ or piped primitives are Scanners, others are Strategies
    is_primitive = False
    if strategy_name.isdigit() and int(strategy_name) >= 101:
        is_primitive = True
    elif "|" in strategy_name:
        is_primitive = True  # e.g. 109|110|111

    try:
        if is_primitive:
            _worker_strategy = importlib.import_module("myra_app.scanners.primitives")
        else:
            _worker_strategy = importlib.import_module(
                f"myra_app.strategies.{strategy_name}"
            )

        # Initialize Adapter in Worker (New 10x Optimization)
        from myra_app.librarian import Librarian

        lib = Librarian(read_only=True, db_path=db_path)
        _worker_adapter = DataAdapter(librarian=lib)
    except Exception:
        pass


def _worker_task(payload):
    global _worker_strategy, _worker_adapter
    symbol, strategy_name, as_of_date, funda = payload

    # Ensure worker is initialized (Fallback for non-Pool environments)
    if _worker_adapter is None:
        init_worker(strategy_name)
    if _worker_adapter is None:
        return None

    # --- TRILOGY ERA v3.2: Metadata-Driven Filtering ---
    # This check has been moved outside the worker loop to prevent N+1 DB queries.
    noise_keywords = [
        "BEES",
        "GOLD",
        "SILVER",
        "LIQUID",
        "ETF",
        "IETF",
        "SDL",
        "GSEC",
        "CASH",
    ]
    if any(k in symbol.upper() for k in noise_keywords):
        return None

    try:
        # 10x Optimization: Dynamic Loading from Worker (On-Demand)
        lookback = _worker_adapter.get_lookback_for_scanner(strategy_name)
        df = _worker_adapter.get_price_df(
            symbol, lookback_days=lookback, as_of_date=as_of_date
        )

        if df is None or df.empty:
            return None

        # Calculate Stage
        sma150 = df["sma150"].iloc[-1] if "sma150" in df.columns else 0
        sma50 = df["sma50"].iloc[-1] if "sma50" in df.columns else 0
        close_price = df["Close"].iloc[-1]
        stage = "Stage 4"
        if sma150 and sma150 > 0:
            if close_price > sma150:
                stage = "Stage 2" if sma50 > sma150 else "Stage 1"
            else:
                stage = "Stage 4" if sma50 < sma150 else "Stage 3"
        funda["Stage"] = stage
        funda["symbol"] = symbol

        # Pattern detection (PKScreener Superpower)
        from myra_app.scanners.patterns import PatternScout

        pattern = PatternScout().get_latest_pattern(df)

        # 10x Factor Scoring (New v4.0 Alpha)
        try:
            from myra_app.factor_engine import FactorEngine

            fe = FactorEngine()
            factor_scores = fe.score_symbol(df, funda)
            funda.update(factor_scores)
        except Exception:
            pass

        # SMC-2: Real-Time Structural Flow (SMC v2.0)
        df_lower = df.rename(columns=lambda x: x.lower())
        fvg_signals = SMCManager.calculate_fvg(df_lower)
        bos_signals, choch_signals = SMCManager.calculate_market_structure(df_lower)

        # Update funda with the latest structural footprints
        funda["fvg"] = fvg_signals.iloc[-1] if not fvg_signals.empty else 0
        funda["bos"] = bos_signals.iloc[-1] if not bos_signals.empty else 0
        funda["choch"] = choch_signals.iloc[-1] if not choch_signals.empty else 0

        # SMC-2: Fair Value Gap (FVG) Buy Zone
        fvg_zone = SMCManager.get_fvg_buy_zone(df)
        fvg_mid = fvg_zone["mid"] if fvg_zone else None

        funda["fvg_zone"] = fvg_zone
        funda["fvg_active"] = 1 if fvg_zone else 0
        funda["active_sid"] = (
            strategy_name
            if (strategy_name.isdigit() and int(strategy_name) >= 101)
            or ("|" in strategy_name)
            else None
        )

        # 1. CLASS-BASED STRATEGY SUPPORT
        if hasattr(_worker_strategy, "Strategy"):
            strat_instance = _worker_strategy.Strategy()
            res = strat_instance.run(df, funda)
        # 2. PRIMITIVE SCANNER SUPPORT (Now with OR logic)
        elif hasattr(_worker_strategy, "run_scanner"):
            sid = funda.get("active_sid")
            if not sid:
                return None

            passed = False
            if "|" in sid:
                sids = sid.split("|")
                if any(
                    _worker_strategy.run_scanner(df, s.strip(), funda=funda)
                    for s in sids
                ):
                    passed = True
            else:
                if _worker_strategy.run_scanner(df, sid, funda=funda):
                    passed = True

            if passed:
                res_payload = {
                    "Stock": symbol,
                    "Stage": stage,
                    "LTP": round(df["Close"].iloc[-1], 2),
                    "Pattern": pattern,
                    "SL": funda.get("SL", 0),
                    "Entry": fvg_mid if fvg_mid else funda.get("LTP", 0),
                    "FVG_Zone": f"{round(fvg_zone['bottom'], 2)} - {round(fvg_zone['top'], 2)}"
                    if fvg_zone
                    else "None",
                    "Risk_Per": funda.get("Risk_Per", 0),
                    "Closing_Vibe": funda.get("Closing_Vibe", "-"),
                    "Consensus": funda.get("Consensus", 0),
                    "Support": funda.get("Support", "-"),
                    "Weekly_Div": funda.get("Weekly_Div", "NO"),
                    "CHoCH": funda.get("CHoCH", "NO"),
                    "D-POC": round(funda.get("d_poc", 0), 2),
                    "POC_Dist": f"{round(((df['Close'].iloc[-1] - funda.get('d_poc', 0)) / (funda.get('d_poc', 1) if funda.get('d_poc', 0) > 0 else 1) * 100), 2)}%",
                    "Absorption": f"{round(funda.get('Absorp_Ratio', 0) * 100)}%",
                    "Tightness": f"{round(funda.get('std20', 0) / df['Close'].iloc[-1] * 100, 2)}%"
                    if df["Close"].iloc[-1] > 0
                    else "0%",
                    "Deliv_Pct": f"{round(funda.get('delivery_percent', 0))}%",
                    "Confluence": "High"
                    if funda.get("Consensus", 0) >= 4
                    else "Moderate"
                    if funda.get("Consensus", 0) >= 2
                    else "Low",
                    **funda,
                }

                res_payload["Money_Flow"] = f"₹{round(funda.get('money_flow_cr', 0))}Cr"

                # Dynamic Star Logic
                stars = 1
                if res_payload["Weekly_Div"] == "YES":
                    stars += 1
                if "3Y" in res_payload["Support"]:
                    stars += 1
                if funda.get("RDV", 0) > 1.5 and funda.get("VIX_Stable") == True:
                    stars += 1
                if funda.get("smart_money_score", 0) > 0.7:
                    stars += 1
                res_payload["Stars"] = "*" * int(min(5, stars))

                return res_payload
            return None
        else:
            res = _worker_strategy.run(df, funda)

        if res and res.get("signal"):
            fallback_entry = round(df["Close"].iloc[-1] * 1.005, 2)
            entry_price = fvg_mid if fvg_mid else fallback_entry

            res_dict = {
                "Stock": symbol,
                "Stage": funda.get("Stage", "-"),
                "LTP": round(df["Close"].iloc[-1], 2),
                "Pattern": pattern,
                "SL": funda.get("SL", 0),
                "Entry": entry_price,
                "FVG_Zone": f"{round(fvg_zone['bottom'], 2)} - {round(fvg_zone['top'], 2)}"
                if fvg_zone
                else "None",
                "Risk_Per": funda.get("Risk_Per", 0),
                "Closing_Vibe": funda.get("Closing_Vibe", "-"),
                "Consensus": funda.get("Consensus", 0),
                **funda,
                **res.get("metrics", {}),
            }

            res_dict["Money_Flow"] = f"₹{round(funda.get('money_flow_cr', 0))}Cr"
            return res_dict
    except Exception:
        return None
    return None


class Engine:
    def __init__(self, librarian):
        self.librarian = librarian

    def calculate_accuracy(
        self, symbol: str, strategy_name: str, df: pd.DataFrame = None, funda: dict = {}
    ) -> str:
        """
        PKScreener Superpower: Historical Success Rate.
        Calculates how many times this strategy yielded >3% in the last 10 occurrences.
        """
        if df is None or df.empty:
            try:
                adapter = DataAdapter(librarian=self.librarian)
                df = adapter.get_price_df(symbol, lookback_days=756)
            except Exception:
                pass

        if df is None or df.empty or len(df) < 50:
            return "N/A"

        try:
            success = 0
            count = 0
            is_primitive = (strategy_name.isdigit() and int(strategy_name) >= 101) or (
                "|" in strategy_name
            )
            if is_primitive:
                strat_mod = importlib.import_module("myra_app.scanners.primitives")
            else:
                strat_mod = importlib.import_module(
                    f"myra_app.strategies.{strategy_name}"
                )

            max_idx = len(df) - 10
            for i in range(max_idx, max(20, max_idx - 60), -1):
                if count >= 10:
                    break

                hist_df = df.iloc[:i]
                trigger = False
                if is_primitive:
                    if "|" in strategy_name:
                        sids = strategy_name.split("|")
                        trigger = any(
                            strat_mod.run_scanner(hist_df, s.strip(), funda=funda)
                            for s in sids
                        )
                    else:
                        trigger = strat_mod.run_scanner(
                            hist_df, strategy_name, funda=funda
                        )
                elif hasattr(strat_mod, "run"):
                    res = strat_mod.run(hist_df, funda)
                    trigger = res.get("signal", False)

                if trigger:
                    count += 1
                    entry = hist_df["Close"].iloc[-1]
                    future = df["Close"].iloc[i : i + 10]
                    if not future.empty:
                        cost_factor = 0.003
                        exit_price = future.max()
                        net_return = (exit_price / entry) - 1 - cost_factor
                        if net_return >= 0.03:
                            success += 1

            if count == 0:
                return "New"
            return f"{round((success/count)*100)}%"
        except Exception:
            return "N/A"

    def _is_vix_stable(self, lib) -> bool:
        """Dynamic VIX Sentiment Check"""
        try:
            if not lib._meta_conn:
                return True
            sql = """
                WITH vix_data AS (
                    SELECT date, close,
                           AVG(close) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as sma20
                    FROM benchmarks
                    WHERE symbol = ?
                )
                SELECT (close < sma20) as is_stable
                FROM vix_data
                ORDER BY date DESC
                LIMIT 1
            """
            res = lib._meta_conn.execute(sql, ("^INDIAVIX",)).fetchone()
            return bool(res[0]) if res else True
        except Exception:
            return True

    def run_scan(
        self,
        symbols: List[str],
        strategy_name: str,
        as_of_date: str = None,
        silent: bool = False,
    ):
        import time

        start_time = time.time()
        lib = self.librarian
        if not lib.conn:
            lib.connect()

        # 0. HOLIDAY SHORT-CIRCUIT (Fix 21)
        from myra_core.utils.date_utils import to_date

        target_date = to_date(as_of_date) if as_of_date else date.today()
        from myra_app.fetcher import DataFetcher

        fetcher = DataFetcher()

        if fetcher._is_holiday(target_date):
            if not silent:
                print(
                    f"[MYRA] {target_date} is a Market Holiday. Attempting Snapshot..."
                )
            try:
                from myra_app.results_manager import ResultsManager

                rm = ResultsManager()
                snapshot = rm.load_last_snapshot(strategy_name)
                if snapshot:
                    if not silent:
                        print(f"[MYRA] Success: Loaded Last Known Good Snapshot.")
                    return snapshot, {"status": "HOLIDAY_SNAPSHOT"}
            except Exception:
                pass

            if not as_of_date:
                last_trading_day = lib.get_expected_trading_day(datetime.now())
                if not silent:
                    print(
                        f"[MYRA] No Snapshot found. Targeting last trading day: {last_trading_day}"
                    )
                as_of_date = last_trading_day.date().isoformat() if hasattr(last_trading_day, 'date') else last_trading_day.isoformat()
            else:
                return [], {"status": "HOLIDAY_NO_DATA"}

        if not silent:
            mode_text = f" as of {as_of_date}" if as_of_date else ""
            print(f"[MYRA] Turbo-SQL Mode (10x Opt) Initialized{mode_text}...")

        try:
            cache_df = lib.precompute_indicators(as_of_date=as_of_date)
            if cache_df.empty:
                if not silent:
                    print(
                        "[!] No precomputed indicators found. Database might be stale."
                    )
                return [], {}

            regime = lib.get_market_regime()
            from myra_app.strategies.base_strategy import MarketMoodHelper

            mood = MarketMoodHelper().get_market_mood(lib)
            vix_stable = self._is_vix_stable(lib)

            funda_df = (
                pd.read_sql("SELECT * FROM fundamentals", lib._val_conn)
                if lib._val_conn
                else pd.DataFrame()
            )
            funda_lookup = (
                funda_df.set_index("symbol").to_dict("index")
                if not funda_df.empty
                else {}
            )

            insider_map = {}
            if lib._inst_conn:
                try:
                    m_data = pd.read_sql(
                        """
                        SELECT symbol, 
                               SUM(CASE WHEN type='Buy' THEN value_cr ELSE -value_cr END) as net_60d,
                               AVG(CASE WHEN type='Buy' AND value_cr > 0.1 THEN avg_price ELSE NULL END) as avg_buy_60d,
                               SUM(CASE WHEN type='Buy' AND date >= date('now', '-5 days') THEN value_cr ELSE 0 END) as net_5d,
                               COUNT(DISTINCT CASE WHEN type='Buy' AND value_cr > 0.1 THEN date ELSE NULL END) as active_days
                        FROM insider_trades 
                        WHERE date >= date('now', '-60 days') 
                        AND (mode LIKE '%Market%' OR mode = '-')
                        GROUP BY symbol
                    """,
                        lib._inst_conn,
                    )

                    if not m_data.empty:
                        conditions = [
                            m_data["active_days"] > 5,
                            m_data["active_days"] >= 3,
                            m_data["active_days"] >= 1,
                        ]
                        m_data["accel"] = np.select(
                            conditions, [3, 2, 1], default=0
                        ).astype(int)
                        m_data.fillna(
                            {"avg_buy_60d": 0.0, "net_5d": 0.0, "net_60d": 0.0},
                            inplace=True,
                        )
                        m_data.rename(
                            columns={"net_5d": "buy_latest", "net_60d": "total_60d"},
                            inplace=True,
                        )
                        insider_map.update(
                            m_data.set_index("symbol")[
                                ["buy_latest", "total_60d", "avg_buy_60d", "accel"]
                            ].to_dict("index")
                        )
                except Exception:
                    pass

            deal_map = {}
            if lib._inst_conn:
                try:
                    d_target = as_of_date if as_of_date else date.today().isoformat()
                    deals_df = pd.read_sql(
                        """
                        SELECT symbol, SUM(qty * price) / 10000000.0 as total_buy_cr
                        FROM large_deals 
                        WHERE buy_sell = 'BUY' AND date = ?
                        GROUP BY symbol
                    """,
                        lib._inst_conn,
                        params=(d_target,),
                    )
                    deal_map = dict(zip(deals_df["symbol"], deals_df["total_buy_cr"]))
                except Exception:
                    pass

            funda_map = {}
            cache_records = cache_df.to_dict("records")
            for c in cache_records:
                s = c["symbol"]
                f = funda_lookup.get(s, {})
                i = insider_map.get(
                    s, {"buy_latest": 0, "total_60d": 0, "avg_buy_60d": 0, "accel": 0}
                )

                # Sanitize all cache retrievals to handle None/NaN values (Fix: NoneType comparison error)
                buy_val = deal_map.get(s, 0) or 0
                mcap = f.get("market_cap", 0) if f.get("market_cap") is not None else 0
                intensity = round((buy_val / mcap * 100), 2) if mcap > 0 else 0

                rel_spread = (
                    c.get("rel_spread", 1.0) if c.get("rel_spread") is not None else 1.0
                )
                rel_vol = c.get("rel_vol", 1.0) if c.get("rel_vol") is not None else 1.0
                del_pct = (
                    c.get("delivery_percent", 0)
                    if c.get("delivery_percent") is not None
                    else 0
                )
                vsa_intensity = round((rel_spread / max(0.1, rel_vol)) * del_pct, 2)

                sma150 = c.get("sma150", 0) if c.get("sma150") is not None else 0
                sma50 = c.get("sma50", 0) if c.get("sma50") is not None else 0
                close_price = c.get("close", 0) if c.get("close") is not None else 0

                stage = "Stage 4"
                if sma150 > 0:
                    if close_price > sma150:
                        stage = "Stage 2" if sma50 > sma150 else "Stage 1"
                    else:
                        stage = "Stage 4" if sma50 < sma150 else "Stage 3"

                atr20 = c.get("atr20", 0) if c.get("atr20") is not None else 0
                sl = round(close_price - (2.0 * atr20), 2)
                risk_per = (
                    round(((close_price - sl) / close_price) * 100, 1)
                    if close_price > 0
                    else 0
                )

                cl_vibe = "-"
                high_price = c.get("high", 0) if c.get("high") is not None else 0
                low_price = c.get("low", 0) if c.get("low") is not None else 0
                if (high_price - low_price) != 0:
                    cl_vibe = (
                        "Accumulation"
                        if (
                            (2 * close_price - high_price - low_price)
                            / (high_price - low_price)
                        )
                        > 0
                        else "Distribution"
                    )

                con_score = 0
                sma20 = c.get("sma20", 0) if c.get("sma20") is not None else 0
                if close_price > 0 and sma20 > 0 and sma50 > 0:
                    if close_price > sma20 > sma50:
                        con_score += 1

                atr5 = c.get("atr5", 0) if c.get("atr5") is not None else 0
                if atr5 > 0 and atr20 > 0:
                    if atr5 < atr20:
                        con_score += 1

                funda_map[s] = {
                    "symbol": s,
                    "PE": f.get("pe"),
                    "ROE": f.get("roe"),
                    "ProfitGrowth": f.get("profit_growth", 0) or 0,
                    "SalesGrowth": f.get("sales_growth", 0) or 0,
                    "Debt_Equity": f.get("debt_to_equity", 0) or 0,
                    "MCap": f.get("market_cap"),
                    "Sector": f.get("sector"),
                    "Inst_Hold": f.get("inst_holding"),
                    "Market_Regime": regime,
                    "Market_Mood": mood,
                    "VIX_Stable": vix_stable,
                    "close": close_price,
                    "high": high_price,
                    "low": low_price,
                    "Stage": stage,
                    "Stage_Text": stage,
                    "SL": sl,
                    "Risk_Per": risk_per,
                    "Closing_Vibe": cl_vibe,
                    "Consensus": con_score,
                    "AV_Latest": i["buy_latest"],
                    "AV_Total": i["total_60d"],
                    "AV_Accel": i.get("accel", 0),
                    "Inst_Intensity": intensity,
                    "low_1y": c.get("low_1y", 0) if c.get("low_1y") is not None else 0,
                    "low_2y": c.get("low_2y", 0) if c.get("low_2y") is not None else 0,
                    "low_3y": c.get("low_3y", 0) if c.get("low_3y") is not None else 0,
                    "vol_sma50": c.get("vol_sma50", 1)
                    if c.get("vol_sma50") is not None
                    else 1,
                    "deliv_sma50": c.get("deliv_sma50", 1)
                    if c.get("deliv_sma50") is not None
                    else 1,
                    "AD_Flow": c.get("ad_flow", 0)
                    if c.get("ad_flow") is not None
                    else 0,
                    "Absorp_Ratio": c.get("absorp_ratio", 0)
                    if c.get("absorp_ratio") is not None
                    else 0,
                    "sma200": c.get("sma200", 0) if c.get("sma200") is not None else 0,
                    "sma150": sma150,
                    "sma50": sma50,
                    "high_2y": c.get("high_2y", 0)
                    if c.get("high_2y") is not None
                    else 0,
                    "cpr_bc": c.get("cpr_bc", 0) if c.get("cpr_bc") is not None else 0,
                    "cpr_tc": c.get("cpr_tc", 0) if c.get("cpr_tc") is not None else 0,
                    "keltner_upper": c.get("keltner_upper", 0)
                    if c.get("keltner_upper") is not None
                    else 0,
                    "keltner_lower": c.get("keltner_lower", 0)
                    if c.get("keltner_lower") is not None
                    else 0,
                    "rel_spread": rel_spread,
                    "rel_vol": rel_vol,
                    "closing_pos": c.get("closing_pos", 0.5)
                    if c.get("closing_pos") is not None
                    else 0.5,
                    "VSA_Intensity": vsa_intensity,
                    "pct_above_ma50_60d": c.get("pct_above_ma50_60d", 0)
                    if c.get("pct_above_ma50_60d") is not None
                    else 0,
                    "avg_volume_20d": c.get("avg_volume_20d", 0)
                    if c.get("avg_volume_20d") is not None
                    else 0,
                    "avg_delivery_20d": c.get("avg_delivery_20d", 0)
                    if c.get("avg_delivery_20d") is not None
                    else 0,
                    "delivery_qty": c.get("delivery_qty", 0)
                    if c.get("delivery_qty") is not None
                    else 0,
                    "delivery_percent": c.get("delivery_percent", 0)
                    if c.get("delivery_percent") is not None
                    else 0,
                    "RDV": c.get("rdv", 0) if c.get("rdv") is not None else 0,
                    "ATR14": c.get("atr14", 0) if c.get("atr14") is not None else 0,
                    "Squeeze": c.get("squeeze_flag", False),
                    "smart_money_score": c.get("smart_money_score", 0)
                    if c.get("smart_money_score") is not None
                    else 0,
                    "smc_phase": c.get("smc_phase", 0)
                    if c.get("smc_phase") is not None
                    else 0,
                    "d_poc": c.get("d_poc", 0) if c.get("d_poc") is not None else 0,
                    "choch": c.get("choch", 0) if c.get("choch") is not None else 0,
                    "bos": c.get("bos", 0) if c.get("bos") is not None else 0,
                    "fvg": c.get("fvg", 0) if c.get("fvg") is not None else 0,
                    "std20": c.get("std20", 0) if c.get("std20") is not None else 0,
                    "atr_pct": c.get("atr_pct", 0)
                    if c.get("atr_pct") is not None
                    else 0,
                    "atr5": atr5,
                    "drawdown": c.get("drawdown", 0)
                    if c.get("drawdown") is not None
                    else 0,
                    "money_flow_cr": c.get("money_flow_cr", 0)
                    if c.get("money_flow_cr") is not None
                    else 0,
                    "EPS_Latest": c.get("eps_latest", f.get("eps", 0))
                    if c.get("eps_latest", f.get("eps", 0)) is not None
                    else 0,
                    "BVPS_Latest": c.get("bvps_latest", f.get("book_value", 0))
                    if c.get("bvps_latest", f.get("book_value", 0)) is not None
                    else 0,
                    "atr20": atr20,
                    "avg_buy_60d": i.get("avg_buy_60d", 0) or 0,
                }

            target_symbols = (
                [s.split(".")[0].upper() for s in symbols]
                if symbols
                else [s.split(".")[0].upper() for s in lib.get_active_universe()]
            )

            # Bulk fetch metadata instrument types to avoid N+1 queries in _worker_task
            try:
                from myra_app.librarian_core import LibrarianCore
                import sqlite3
                import os
                import pandas as pd

                meta_conn = sqlite3.connect(
                    os.path.join(os.getcwd(), "db", LibrarianCore.DB_MAP["meta"]),
                    timeout=10,
                    check_same_thread=False,
                )
                meta_df = pd.read_sql("SELECT symbol, instrument_type FROM symbols_master", meta_conn)
                meta_conn.close()
                equity_symbols = set(meta_df.loc[meta_df['instrument_type'] == 'EQUITY', 'symbol'].str.upper())
                target_symbols = [s for s in target_symbols if s in equity_symbols]
            except Exception:
                pass

            # Vectorized payload generation (Fix 515: Avoid .append in loop)
            payloads = [
                (s, strategy_name, as_of_date, funda_map.get(s, {"symbol": s}))
                for s in target_symbols
            ]

        except Exception as e:
            if not silent:
                print(f"[!] Turbo Load failed: {e}")
            return [], {}

        # 5. ANALYZE WITH FAULT TOLERANCE (Fix 4, 7, 10)
        num_stocks = len(payloads)
        if not silent:
            print(
                f"[MYRA] Analyzing {num_stocks} stocks with Institutional Resilience..."
            )

        watchdog = ScanWatchdog(timeout=120)
        watchdog.start()

        max_workers = multiprocessing.cpu_count()

        def poked_results(it, wd):
            for item in it:
                wd.poke()
                yield item

        total_symbols = len(payloads)
        current_symbol = 0
        try:
            # Proper Multiprocessing Pool with initializer fixes the 0/680 failure
            with multiprocessing.Pool(
                processes=max_workers,
                initializer=init_worker,
                initargs=(strategy_name, lib.db_path),
            ) as pool:
                raw_it = pool.imap(_worker_task, payloads)
                results = [
                    res
                    for res, _ in zip(
                        poked_results(raw_it, watchdog), range(total_symbols)
                    )
                    if (
                        res
                        and not (current_symbol := current_symbol + 1)
                        and (not silent and myra_log(current_symbol, total_symbols))
                        is None
                    )
                    or res
                ]

        except KeyboardInterrupt:
            if not silent:
                print("\n[!] Scan interrupted by user.")
            watchdog.stop()
            raise KeyboardInterrupt
        except Exception as e:
            if not silent:
                print(f"[!] Engine Error: {e}")
        finally:
            watchdog.stop()

        valid_results = len(results)
        # Safety fuse adjusted for holiday scans (where volume might be zero for many)
        if num_stocks > 20 and valid_results == 0:
            if not silent:
                print(
                    f"\n[CRITICAL] Data Integrity Failure: 0/{num_stocks} results produced. Aborting."
                )
            return [], {"error": "CATASTROPHIC_PIPELINE_FAILURE"}

        if not silent:
            elapsed = time.time() - start_time
            print(
                f"[MYRA] Scan completed in {elapsed:.2f}s ({num_stocks} stocks, {elapsed/max(1,num_stocks):.3f}s/stock)"
            )

        try:
            lineage_path = self.fetcher.lineage.save()
            if not silent:
                print(f"[MYRA] Data Lineage saved to {lineage_path}")
        except Exception:
            pass

        return results, {}


class SMCManager:
    """
    MYRA SMC Manager - Institutional Accumulation Engine (SMC-1)
    Focuses on Delivery Point of Control (D-POC) and Multi-Scale Trend Confluence.
    """

    @staticmethod
    def calculate_fvg(df):
        """
        Detects Fair Value Gaps (FVG) with tolerance (0.2%).
        Returns a Series where 1=Bullish FVG, -1=Bearish FVG.
        """
        if len(df) < 3:
            return pd.Series(0, index=df.index)

        fvg = np.zeros(len(df))
        highs = df["high"].values
        lows = df["low"].values

        gap_threshold = 0.002  # 0.2% tolerance for 'institutional footprint'

        for i in range(2, len(df)):
            if lows[i] > highs[i - 2] * (1 - gap_threshold):  # Bullish
                fvg[i] = 1
            elif highs[i] < lows[i - 2] * (1 + gap_threshold):  # Bearish
                fvg[i] = -1
        return pd.Series(fvg, index=df.index)

    @staticmethod
    def get_fvg_buy_zone(df):
        """
        Returns the nearest UNMITIGATED Bullish FVG that is BELOW current price.
        Institutional Magnet logic: Zone is valid until price closes 1.5% below the bottom (SMC v2.0).
        """
        if len(df) < 3:
            return None

        try:
            # Case-insensitive column resolution
            cols = {c.lower(): c for c in df.columns}
            h_col = cols.get("high")
            l_col = cols.get("low")
            c_col = cols.get("close")
            if not h_col or not l_col or not c_col:
                return None

            highs = df[h_col].values
            lows = df[l_col].values
            closes = df[c_col].values
            ltp = closes[-1]

            # 3-year lookback (756 trading days)
            for i in range(len(df) - 1, max(2, len(df) - 756), -1):
                if lows[i] > highs[i - 2]:  # Bullish FVG
                    bottom = highs[i - 2]
                    top = lows[i]
                    mid = (top + bottom) / 2

                    # 1. Must be BELOW current price to be a 'Support' Floor
                    if ltp < bottom:
                        continue

                    # 2. Mitigation Check: Has price CLOSED > 1.5% below bottom since formation?
                    is_dead = False
                    for j in range(i + 1, len(df)):
                        if closes[j] < (
                            bottom * 0.985
                        ):  # 1.5% buffer for 'structural sweeps'
                            is_dead = True
                            break

                    if not is_dead:
                        return {"top": top, "bottom": bottom, "mid": mid}
            return None
        except Exception:
            return None

    @staticmethod
    def calculate_market_structure(df, window=3):
        """
        Detects Market Structure shifts (BOS and CHoCH) using Lagging Pivot Confirmation.
        SMC v2.0: Prevents future-peeking by confirming pivots with a 'window' lag.
        """
        if len(df) < window * 2 + 2:
            return pd.Series(0, index=df.index), pd.Series(0, index=df.index)

        bos = np.zeros(len(df))
        choch = np.zeros(len(df))

        last_high = np.nan
        last_low = np.nan
        last_swing_index = 0
        trend = 0  # 1 for up, -1 for down

        c_prices = df["close"].values
        h_prices = df["high"].values
        l_prices = df["low"].values

        for i in range(len(df)):
            # Lagging Pivot Confirmation: At index i, we can confirm a pivot at index (i - window)
            conf_idx = i - window
            if conf_idx >= window:
                # Was conf_idx a Swing High?
                window_slice_h = h_prices[conf_idx - window : conf_idx + window + 1]
                if h_prices[conf_idx] == np.max(window_slice_h):
                    last_high = h_prices[conf_idx]
                    last_swing_index = i  # Refresh memory timer

                # Was conf_idx a Swing Low?
                window_slice_l = l_prices[conf_idx - window : conf_idx + window + 1]
                if l_prices[conf_idx] == np.min(window_slice_l):
                    last_low = l_prices[conf_idx]
                    last_swing_index = i

            # Stale Structure Reset: Increased to 60 days (SMC v2.0)
            if i - last_swing_index > 60:
                last_high = np.nan
                last_low = np.nan

            if trend == 1:  # Uptrend
                if not np.isnan(last_high) and c_prices[i] > last_high:
                    bos[i] = 1
                    # Note: We don't update last_high here; it only updates on new confirmed pivots
                elif not np.isnan(last_low) and c_prices[i] < last_low:
                    choch[i] = -1  # Trend flipped to Down
                    trend = -1
            elif trend == -1:  # Downtrend
                if not np.isnan(last_low) and c_prices[i] < last_low:
                    bos[i] = -1
                elif not np.isnan(last_high) and c_prices[i] > last_high:
                    choch[i] = 1  # Trend flipped to Up
                    trend = 1
            else:  # Trend Detection (Initialization)
                if not np.isnan(last_high) and c_prices[i] > last_high:
                    trend = 1
                if not np.isnan(last_low) and c_prices[i] < last_low:
                    trend = -1

        return pd.Series(bos, index=df.index), pd.Series(choch, index=df.index)

    @staticmethod
    def calculate_d_poc(df, buckets=50):
        """Finds the price level with the highest cumulative delivery over the window."""
        if df.empty or "close" not in df.columns or "delivery_qty" not in df.columns:
            return 0.0

        try:
            # Drop NaNs to prevent histogram failure
            valid_df = df.dropna(subset=["close", "delivery_qty"])
            if valid_df.empty:
                return 0.0

            prices = valid_df["close"].values
            delivery = valid_df["delivery_qty"].values

            # Ensure we have a price range
            delivery = delivery.astype(float)
            p_min, p_max = prices.min(), prices.max()
            if p_max == p_min:
                return float(p_min)

            hist, bin_edges = np.histogram(
                prices, bins=buckets, range=(p_min, p_max), weights=delivery
            )

            # Find the bin with maximum delivery
            max_idx = np.argmax(hist)
            d_poc = (bin_edges[max_idx] + bin_edges[max_idx + 1]) / 2

            # Final sanity check - if d_poc is 0 but price isn't, something is wrong
            if d_poc == 0 and p_min > 0:
                return float(prices[-1])  # Fallback to LTP

            return float(d_poc)

        except Exception:
            # print(f"DEBUG: calculate_d_poc Error: {e}")
            return 0.0

    @staticmethod
    def get_confluence_score(df):
        """Calculates multi-dilation trend confluence (scales 2, 4, 8)."""
        if len(df) < 40:
            return 0.0

        try:
            # Simple returns to stay fast
            returns = np.log(df["close"] / df["close"].shift(1)).dropna()

            def dilated_mean(series, dilation, window=5):
                # Vectorized slice and mean
                subset = series.iloc[-window * dilation :: dilation]
                return subset.mean() if not subset.empty else 0.0

            c2 = dilated_mean(returns, 2)
            c4 = dilated_mean(returns, 4)
            c8 = dilated_mean(returns, 8)

            return float(c2 + c4 + c8)
        except Exception:
            return 0.0

    @staticmethod
    def get_smc_phase(df, d_poc, confluence, funda={}):
        """
        Determines the SMC Phase:
        Phase 1: Accumulation Base (Near D-POC, tight volatility, Volume Dry-up)
        Phase 2: Ignition Trigger (Breakout from D-POC + Volume)
        """
        if df.empty or d_poc == 0:
            return 0

        try:
            ltp = df["close"].iloc[-1]
            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            vol_last = df["volume"].iloc[-1]
            high_60 = df["close"].rolling(60).max().iloc[-1]

            # Phase 2: Ignition Trigger
            # Price > D-POC * 1.03 AND Price > 60D High AND Vol > 1.5x Avg AND Confluence > 0
            if (
                ltp > (d_poc * 1.03)
                and ltp >= high_60
                and vol_last > (avg_vol_20 * 1.5)
                and confluence > 0
            ):
                return 2

            # Phase 1: Accumulation Base (Basing)
            # 1. Price near D-POC (within 2%)
            price_near_poc = abs(ltp - d_poc) / d_poc <= 0.02

            # 2. Tight Volatility (Stricter: < 1.5% of price)
            std20 = funda.get("std20", 0)
            tightness = (std20 / ltp * 100) if ltp > 0 else 100
            is_tight = tightness < 1.5

            # 3. Volume Dry-up (Volume < 60% of 20d Avg)
            volume_dryup = vol_last < (avg_vol_20 * 0.6)

            if price_near_poc and is_tight and volume_dryup:
                return 1

            return 0
        except Exception:
            return 0
