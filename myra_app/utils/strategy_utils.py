def get_strategies():
    """
    Returns the centralized strategies dictionary to eliminate duplication across mock_verify.py and myra.py.
    """
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
        "25": ("institutional_alpha", "Institutional Alpha Radar", "CAR"),
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
        "36": (
            "fusion_engine",
            "Institutional Fusion Tracker",
            ["Entry", "SL", "TP", "Score", "Signal_Type"],
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
    return strategies
