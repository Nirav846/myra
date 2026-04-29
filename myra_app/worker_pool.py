"""Worker pool and task execution for MYRA engine."""
import signal
import warnings
import importlib
import multiprocessing
import pandas as pd
from rich.progress import Progress
from myra_app.adapters.data_adapter import DataAdapter
from myra_app.smc_manager import SMCManager


_worker_strategy = None
_worker_adapter = None


def init_worker(strategy_name, db_path=None):
    global _worker_strategy, _worker_adapter
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    warnings.filterwarnings("ignore", category=UserWarning)

    # 1. INITIALIZE ADAPTER FIRST
    try:
        from myra_app.librarian import Librarian
        lib = Librarian(read_only=True, db_path=db_path)
        _worker_adapter = DataAdapter(librarian=lib)
    except Exception as e:
        print(f"[WORKER INIT ERROR] Adapter failed: {e}")

    # 2. SANITIZE NAME (Robust version: handles "Multibagger Early Detection (Quant)")
    # Splits first, THEN cleans, to avoid trailing underscores from spaces before brackets
    clean_base = str(strategy_name).split("(")[0].strip()
    safe_name = clean_base.lower().replace(" ", "_").replace("-", "_")

    # 3. ROUTE TO CORRECT MODULE (Removed the 101 restriction)
    is_primitive = False
    if str(strategy_name).isdigit() or "|" in str(strategy_name):
        is_primitive = True

    try:
        if is_primitive:
            _worker_strategy = importlib.import_module("myra_app.scanners.primitives")
        else:
            try:
                _worker_strategy = importlib.import_module(f"myra_app.strategies.{safe_name}")
            except ModuleNotFoundError:
                _worker_strategy = importlib.import_module("myra_app.scanners.primitives")
    except Exception:
        pass


def _worker_task(payload):
    global _worker_strategy, _worker_adapter
    symbol, strategy_name, as_of_date, funda = payload

    # Ensure worker is initialized
    if _worker_adapter is None:
        init_worker(strategy_name)
    if _worker_adapter is None:
        return None

    noise_keywords = [
        "BEES", "GOLD", "SILVER", "LIQUID", "ETF",
        "IETF", "SDL", "GSEC", "CASH"
    ]
    if any(k in symbol.upper() for k in noise_keywords):
        return None

    try:
        lookback = _worker_adapter.get_lookback_for_scanner(strategy_name)
        df = _worker_adapter.get_price_df(
            symbol, lookback_days=lookback
        )

        if df is None or df.empty:
            return None

        # Contract enforcement after load
        from myra_core.data_contracts import enforce_ohlcv_contract
        df = enforce_ohlcv_contract(df, symbol)

        # Ensure DataFrame Integrity
        if "date" not in df.columns:
            df = df.reset_index()
            if "index" in df.columns and "date" not in df.columns:
                df = df.rename(columns={"index": "date"})

        if "date" in df.columns:
            df = df.sort_values("date")
            df = df.drop_duplicates(subset=["date"], keep="last")
            df = df.set_index("date")
        else:
            if not df.index.is_unique:
                df = df[~df.index.duplicated(keep="last")]
            df = df.sort_index()

        # --- UNIVERSAL CASE-INSENSITIVE COMPATIBILITY LAYER ---
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col.lower()] = df[col]

        delivery_variants = ["DeliveryPct", "delivery_pct", "delivery_percent"]
        actual_col = next((c for c in delivery_variants if c in df.columns), None)
        if actual_col:
            for variant in delivery_variants:
                df[variant] = df[actual_col]

        # Calculate Stage (Case-Safe & Float-Hardened)
        sma150_col = next((c for c in ["sma150", "SMA150"] if c in df.columns), None)
        sma50_col = next((c for c in ["sma50", "SMA50"] if c in df.columns), None)

        sma150 = float(df[sma150_col].iloc[-1]) if sma150_col else 0
        sma50 = float(df[sma50_col].iloc[-1]) if sma50_col else 0
        close_price = float(df["Close"].iloc[-1])

        stage = "Stage 4"
        if sma150 > 0:
            if close_price > sma150:
                stage = "Stage 2" if sma50 > sma150 else "Stage 1"
            else:
                stage = "Stage 4" if sma50 < sma150 else "Stage 3"

        funda["Stage"] = stage
        funda["symbol"] = symbol

        # Pattern detection
        from myra_app.scanners.patterns import PatternScout
        pattern = PatternScout().get_latest_pattern(df)

        # Factor Scoring
        try:
            from myra_app.factor_engine import FactorEngine
            fe = FactorEngine()
            factor_scores = fe.score_symbol(df, funda)
            funda.update(factor_scores)
        except Exception:
            pass

        # --- SMC FIX: ISOLATED COLUMNS (Stops Truth Value Ambiguity) ---
        base_cols = ["Open", "High", "Low", "Close", "Volume"]
        df_smc = df[base_cols].copy()
        df_smc.columns = [c.lower() for c in df_smc.columns]

        fvg_signals = SMCManager.calculate_fvg(df_smc)
        bos_signals, choch_signals = SMCManager.calculate_market_structure(df_smc)

        funda["fvg"] = fvg_signals.iloc[-1] if not fvg_signals.empty else 0
        funda["bos"] = bos_signals.iloc[-1] if not bos_signals.empty else 0
        funda["choch"] = choch_signals.iloc[-1] if not choch_signals.empty else 0

        fvg_zone = SMCManager.get_fvg_buy_zone(df)
        fvg_mid = fvg_zone["mid"] if fvg_zone else None

        funda["fvg_zone"] = fvg_zone
        funda["fvg_active"] = 1 if fvg_zone else 0

        # Accept string strategy names properly
        funda["active_sid"] = strategy_name

        # Contract enforcement before strategy
        df = enforce_ohlcv_contract(df, symbol)

        # 1. CLASS-BASED STRATEGY SUPPORT
        if hasattr(_worker_strategy, "Strategy"):
            strat_instance = _worker_strategy.Strategy()
            res = strat_instance.run(df, funda)

        # 2. PRIMITIVE SCANNER SUPPORT
        elif hasattr(_worker_strategy, "run_scanner"):
            sid = funda.get("active_sid")
            if not sid:
                return None

            passed = False
            if "|" in str(sid):
                sids = str(sid).split("|")
                if any(_worker_strategy.run_scanner(df, s.strip(), funda=funda) for s in sids):
                    passed = True
            else:
                if _worker_strategy.run_scanner(df, str(sid), funda=funda):
                    passed = True

            if passed:
                _deliv_pct = funda.get('delivery_percent', 0)
                if pd.isna(_deliv_pct):
                    _deliv_pct = 0
                funda['delivery_percent'] = _deliv_pct
                res_payload = {
                    "Stock": symbol,
                    "Stage": stage,
                    "LTP": round(df["Close"].iloc[-1], 2),
                    "Pattern": pattern,
                    "SL": funda.get("SL", 0),
                    "Entry": fvg_mid if fvg_mid else funda.get("LTP", 0),
                    "FVG_Zone": f"{round(fvg_zone['bottom'], 2)} - {round(fvg_zone['top'], 2)}" if fvg_zone else "None",
                    "Risk_Per": funda.get("Risk_Per", 0),
                    "Closing_Vibe": funda.get("Closing_Vibe", "-"),
                    "Consensus": funda.get("Consensus", 0),
                    "Support": funda.get("Support", "-"),
                    "Weekly_Div": funda.get("Weekly_Div", "NO"),
                    "CHoCH": funda.get("CHoCH", "NO"),
                    "D-POC": round(funda.get("d_poc", 0), 2),
                    "POC_Dist": f"{round(((df['Close'].iloc[-1] - funda.get('d_poc', 0)) / (funda.get('d_poc', 1) if funda.get('d_poc', 0) > 0 else 1) * 100), 2)}%",
                    "Absorption": f"{round(funda.get('Absorp_Ratio', 0) * 100)}%",
                    "Tightness": f"{round(funda.get('std20', 0) / df['Close'].iloc[-1] * 100, 2)}%" if df["Close"].iloc[-1] > 0 else "0%",
                    "Deliv_Pct": f"{round(_deliv_pct)}%",
                    "Confluence": "High" if funda.get("Consensus", 0) >= 4 else "Moderate" if funda.get("Consensus", 0) >= 2 else "Low",
                    **funda,
                }

                res_payload["Money_Flow"] = f"₹{round(funda.get('money_flow_cr', 0))}Cr"

                stars = 1
                if res_payload.get("Weekly_Div", "NO") == "YES": stars += 1
                if "3Y" in res_payload.get("Support", ""): stars += 1
                if funda.get("RDV", 0) > 1.5 and funda.get("VIX_Stable") == True: stars += 1
                if funda.get("smart_money_score", 0) > 0.7: stars += 1
                res_payload["Stars"] = "*" * int(min(5, stars))

                return res_payload
            return None
        else:
            res = _worker_strategy.run(df, funda)

        # FINAL RETURN BLOCK
        if res and res.get("signal"):
            fallback_entry = round(df["Close"].iloc[-1] * 1.005, 2)
            entry_price = fvg_mid if fvg_mid else fallback_entry

            res_dict = {
                "Stock": symbol,
                "Stage": stage,
                "LTP": round(df["Close"].iloc[-1], 2),
                "Pattern": pattern,
                "SL": funda.get("SL", 0),
                "Entry": entry_price,
                "FVG_Zone": f"{round(fvg_zone['bottom'], 2)} - {round(fvg_zone['top'], 2)}" if fvg_zone else "None",
                "Risk_Per": funda.get("Risk_Per", 0),
                "Closing_Vibe": funda.get("Closing_Vibe", "-"),
                "Consensus": funda.get("Consensus", 0),
                **funda,
                **res.get("metrics", {}),
            }

            res_dict["Money_Flow"] = f"₹{round(funda.get('money_flow_cr', 0))}Cr"

            return res_dict

        return None
    except Exception as e:
        import logging
        logging.error(f"[WORKER FAILED] {symbol}: {type(e).__name__} - {e}")
        return None


def run_workers(payloads, strategy_name, db_path, silent=False, watchdog=None) -> list[dict]:
    """
    Runs _worker_task across all payloads, using multiprocessing for large batches
    and a simple loop for small batches (<10 symbols).
    Returns list of result dicts.
    """
    max_workers = multiprocessing.cpu_count()
    total_symbols = len(payloads)
    results = []

    with Progress() as progress:
        task = progress.add_task("[cyan]Scanning...", total=total_symbols)

        if total_symbols < 10:
            # Fast path: no multiprocessing overhead for small scans
            init_worker(strategy_name, db_path)
            for i, payload in enumerate(payloads, 1):
                if watchdog:
                    watchdog.poke()
                res = _worker_task(payload)
                progress.update(task, advance=1)
                if res:
                    results.append(res)
        else:
            # Full path: use multiprocessing for bulk scans
            with multiprocessing.Pool(
                processes=max_workers,
                initializer=init_worker,
                initargs=(strategy_name, db_path),
            ) as pool:
                raw_it = pool.imap(_worker_task, payloads)
                for i, res in enumerate(raw_it, 1):
                    if watchdog:
                        watchdog.poke()
                    progress.update(task, advance=1)
                    if res:
                        results.append(res)

    return results
