import pandas as pd
import numpy as np
from myra_app.strategies.fusion_engine import FusionEngine

def test_fusion_logic():
    engine = FusionEngine()

    # We want to test LONG, PENDING_LONG, SHORT, PENDING_SHORT
    # padding with 60 rows

    df1 = pd.DataFrame({
        "close": [102.5]*60,
        "htf_bullish": [1]*60,
        "mtf_bullish": [1]*60,
        "fvg_top": [102]*60,
        "fvg_bottom": [100]*60,
        "fvg_boundary": [100]*60,
        "swing_high": [110]*60,
        "swing_low": [90]*60,
        "fvg_freshness": [0.5]*60,
        "liquidity_distance": [0.5]*60,
        "trend_alignment": [0.5]*60,
        "delivery_qty": [1000]*60,
        "delivery_ma_60": [500]*60,
    })

    # PENDING_LONG
    res1 = engine.run(df1, {})
    print("PENDING_LONG test:", res1)

    df2 = df1.copy()
    df2["close"] = 101.0
    # LONG
    res2 = engine.run(df2, {})
    print("LONG test:", res2)

    df3 = df1.copy()
    df3["htf_bullish"] = 0
    df3["mtf_bullish"] = 0
    df3["htf_bearish"] = 1
    df3["mtf_bearish"] = 1
    df3["close"] = 97.5 # 2.5 distance from 100
    df3["fvg_boundary"] = 100
    res3 = engine.run(df3, {})
    print("PENDING_SHORT test:", res3)

    df4 = df3.copy()
    df4["close"] = 99.0 # 1.0 distance from 100
    res4 = engine.run(df4, {})
    print("SHORT test:", res4)

test_fusion_logic()
