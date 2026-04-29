"""Universe-wide precomputation for MYRA engine."""
import pandas as pd
import numpy as np
from datetime import date


def load_universe(lib, symbols, as_of_date, silent=False):
    """
    Builds funda_map, insider_map, deal_map for the full universe.
    Returns (cache_df, regime, mood, vix_stable, funda_map, insider_map, deal_map).
    If len(symbols) < 10, returns empty dicts immediately (fast path).
    """
    if len(symbols) < 10:
        cache_df = pd.DataFrame()
        regime = "Unknown"
        mood = "Neutral"
        vix_stable = True
        funda_lookup = {}
        insider_map = {}
        deal_map = {}
        funda_map = {}
        return cache_df, regime, mood, vix_stable, funda_map, insider_map, deal_map

    if not silent:
        mode_text = f" as of {as_of_date}" if as_of_date else ""
        print(f"[MYRA] Turbo-SQL Mode (10x Opt) Initialized{mode_text}...")

    cache_df = lib.precompute_indicators(as_of_date=as_of_date)
    if cache_df.empty:
        if not silent:
            print("[!] No precomputed indicators found. Database might be stale.")
        return [], {}, {}, {}, {}, {}, {}

    regime = lib.get_market_regime()
    from myra_app.strategies.base_strategy import MarketMoodHelper
    mood = MarketMoodHelper().get_market_mood(lib)
    vix_stable = lib._is_vix_stable(lib)

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

        buy_val = deal_map.get(s, 0) or 0
        mcap = f.get("market_cap", 0) if f.get("market_cap") is not None else 0
        intensity = round((buy_val / mcap * 100), 2) if mcap > 0 else 0

        rel_spread = c.get("rel_spread", 1.0) if c.get("rel_spread") is not None else 1.0
        rel_vol = c.get("rel_vol", 1.0) if c.get("rel_vol") is not None else 1.0
        del_pct = c.get("delivery_percent", 0) if c.get("delivery_percent") is not None else 0
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
                if ((2 * close_price - high_price - low_price) / (high_price - low_price)) > 0
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
            "vol_sma50": c.get("vol_sma50", 1) if c.get("vol_sma50") is not None else 1,
            "deliv_sma50": c.get("deliv_sma50", 1) if c.get("deliv_sma50") is not None else 1,
            "AD_Flow": c.get("ad_flow", 0) if c.get("ad_flow") is not None else 0,
            "Absorp_Ratio": c.get("absorp_ratio", 0) if c.get("absorp_ratio") is not None else 0,
            "sma200": c.get("sma200", 0) if c.get("sma200") is not None else 0,
            "sma150": sma150,
            "sma50": sma50,
            "high_2y": c.get("high_2y", 0) if c.get("high_2y") is not None else 0,
            "cpr_bc": c.get("cpr_bc", 0) if c.get("cpr_bc") is not None else 0,
            "cpr_tc": c.get("cpr_tc", 0) if c.get("cpr_tc") is not None else 0,
            "keltner_upper": c.get("keltner_upper", 0) if c.get("keltner_upper") is not None else 0,
            "keltner_lower": c.get("keltner_lower", 0) if c.get("keltner_lower") is not None else 0,
            "rel_spread": rel_spread,
            "rel_vol": rel_vol,
            "closing_pos": c.get("closing_pos", 0.5) if c.get("closing_pos") is not None else 0.5,
            "VSA_Intensity": vsa_intensity,
            "pct_above_ma50_60d": c.get("pct_above_ma50_60d", 0) if c.get("pct_above_ma50_60d") is not None else 0,
            "avg_volume_20d": c.get("avg_volume_20d", 0) if c.get("avg_volume_20d") is not None else 0,
            "avg_delivery_20d": c.get("avg_delivery_20d", 0) if c.get("avg_delivery_20d") is not None else 0,
            "delivery_qty": c.get("delivery_qty", 0) if c.get("delivery_qty") is not None else 0,
            "delivery_percent": c.get("delivery_percent", 0) if c.get("delivery_percent") is not None else 0,
            "RDV": c.get("rdv", 0) if c.get("rdv") is not None else 0,
            "ATR14": c.get("atr14", 0) if c.get("atr14") is not None else 0,
            "Squeeze": c.get("squeeze_flag", False),
            "smart_money_score": c.get("smart_money_score", 0) if c.get("smart_money_score") is not None else 0,
            "smc_phase": c.get("smc_phase", 0) if c.get("smc_phase") is not None else 0,
            "d_poc": c.get("d_poc", 0) if c.get("d_poc") is not None else 0,
            "choch": c.get("choch", 0) if c.get("choch") is not None else 0,
            "bos": c.get("bos", 0) if c.get("bos") is not None else 0,
            "fvg": c.get("fvg", 0) if c.get("fvg") is not None else 0,
            "std20": c.get("std20", 0) if c.get("std20") is not None else 0,
            "atr_pct": c.get("atr_pct", 0) if c.get("atr_pct") is not None else 0,
            "atr5": atr5,
            "drawdown": c.get("drawdown", 0) if c.get("drawdown") is not None else 0,
            "money_flow_cr": c.get("money_flow_cr", 0) if c.get("money_flow_cr") is not None else 0,
            "EPS_Latest": c.get("eps_latest", f.get("eps", 0)) if c.get("eps_latest", f.get("eps", 0)) is not None else 0,
            "BVPS_Latest": c.get("bvps_latest", f.get("book_value", 0)) if c.get("bvps_latest", f.get("book_value", 0)) is not None else 0,
            "atr20": atr20,
            "avg_buy_60d": i.get("avg_buy_60d", 0) or 0,
        }

    return cache_df, regime, mood, vix_stable, funda_map, insider_map, deal_map
