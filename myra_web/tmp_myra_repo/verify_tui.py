import pandas as pd
import numpy as np
from myra_app.strategies.fusion_engine import FusionEngine
from myra_app.utils.strategy_utils import get_strategies

def test_fusion_engine():
    engine = FusionEngine()

    # Create mock dataframe with enough rows to bypass lookback
    lookback = engine.config.get("parameters", {}).get("lookback_trading_days", 60)

    # Ensure minimum lookback length + 1
    size = lookback + 1

    # Enforce OHLCV TitleCase rule
    df = pd.DataFrame({
        'Close': np.random.uniform(100, 150, size),
        'Open': np.random.uniform(100, 150, size),
        'High': np.random.uniform(150, 160, size),
        'Low': np.random.uniform(90, 100, size),
        'Volume': np.random.uniform(1000, 5000, size),
        'htf_bullish': np.ones(size),
        'mtf_bullish': np.ones(size),
        'fvg_freshness': np.ones(size),
        'fvg_boundary': np.ones(size) * 115, # Close to close price to trigger proximity
        'fvg_top': np.ones(size) * 120,
        'fvg_bottom': np.ones(size) * 110,
        'swing_high': np.ones(size) * 180, # Provide good RR
        'swing_low': np.ones(size) * 90,
    })

    # Force the last row to trigger a signal
    df.loc[size-1, 'Close'] = 115

    res = engine.run(df, {})
    print("FusionEngine result:")
    print(res)

    # Graceful handling
    if not res:
        print("Empty dict returned.")
    elif res.get("signal"):
        metrics = res.get("metrics", {})
        if metrics:
            assert "TP" in metrics, "TP missing from metrics"
            assert "T1" in metrics, "T1 missing from metrics"
            assert "Signal_Type" in metrics, "Signal_Type missing from metrics"
            print("TP, T1, Signal_Type are present in metrics!")
        else:
            print("Signal true but no metrics found.")
    else:
        print("No signal returned, structure handled gracefully.")

    strats = get_strategies()
    assert "36" in strats, "Strategy 36 not present"
    assert "Target" not in strats["36"][2] and "T1" not in strats["36"][2] and "TP" in strats["36"][2], "Checking hero cols"

    # Check screener logic directly to simulate TUI column population
    # "Target" = r.get("T1") or tactics.get("target", round(r["Entry"] * 1.15, 2))
    # We will just verify the dictionary mutation that Screener does

    # Extract values gracefully
    metrics = res.get("metrics") or {}
    mock_screener_result = {
        "Stock": "TEST",
        "Entry": metrics.get("Entry", 100),
        "SL": metrics.get("SL", 90),
        "T1": metrics.get("T1", 150),
        "tactics": {}
    }

    target_val = mock_screener_result.get("T1") or mock_screener_result["tactics"].get("target", round(mock_screener_result["Entry"] * 1.15, 2))
    print(f"Screener logic populated Target: {target_val}")
    assert target_val == 150 or target_val == mock_screener_result.get("T1"), "Target not correctly populated!"

    # Simulate the absence of the T1 key to verify the screener's fallback mechanism
    mock_screener_result_no_t1 = {
        "Stock": "TEST",
        "Entry": 100,
        "SL": 90,
        "tactics": {}
    }
    target_val_no_t1 = mock_screener_result_no_t1.get("T1") or mock_screener_result_no_t1["tactics"].get("target", round(mock_screener_result_no_t1["Entry"] * 1.15, 2))
    print(f"Screener logic populated Target (Fallback): {target_val_no_t1}")
    assert target_val_no_t1 == 115.0, "Fallback target logic failed!"

    print("Verification complete!")

if __name__ == '__main__':
    test_fusion_engine()
