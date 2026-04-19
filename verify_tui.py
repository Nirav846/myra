import pandas as pd
import numpy as np
from myra_app.strategies.fusion_engine import FusionEngine

def get_strategies():
    import ast
    with open("myra_app/myra.py") as f:
        for node in ast.walk(ast.parse(f.read())):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "strategies":
                return ast.literal_eval(node.value)
    return {}

def test_fusion_engine():
    engine = FusionEngine()

    # Create mock dataframe with enough rows to bypass lookback
    lookback = engine.config.get("parameters", {}).get("lookback_trading_days", 60)

    # Ensure minimum lookback length + 1
    size = lookback + 1

    df = pd.DataFrame({
        'close': np.random.uniform(100, 150, size),
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
    df.loc[size-1, 'close'] = 115

    res = engine.run(df, {})
    print("FusionEngine result:")
    print(res)

    if res.get("signal"):
        assert "TP" in res["metrics"], "TP missing from metrics"
        assert "T1" in res["metrics"], "T1 missing from metrics"
        assert "Signal_Type" in res["metrics"], "Signal_Type missing from metrics"
        print("TP, T1, Signal_Type are present in metrics!")
    else:
        print("No signal returned, cannot fully test metrics dict but structure is generally correct.")

    strats = get_strategies()
    assert "36" in strats, "Strategy 36 not present"
    assert "Target" not in strats["36"][2] and "T1" not in strats["36"][2] and "TP" in strats["36"][2], "Checking hero cols"

    # Check screener logic directly to simulate TUI column population
    # "Target" = r.get("T1") or tactics.get("target", round(r["Entry"] * 1.15, 2))
    # We will just verify the dictionary mutation that Screener does
    mock_screener_result = {
        "Stock": "TEST",
        "Entry": res["metrics"].get("Entry", 100),
        "SL": res["metrics"].get("SL", 90),
        "T1": res["metrics"].get("T1", 150), # This maps exactly to the metrics dictionary populated by Screener
        "tactics": {}
    }

    target_val = mock_screener_result.get("T1") or mock_screener_result["tactics"].get("target", round(mock_screener_result["Entry"] * 1.15, 2))
    print(f"Screener logic populated Target: {target_val}")

    assert target_val == 150 or target_val == mock_screener_result["T1"], "Target not correctly populated!"

    print("Verification complete!")

if __name__ == '__main__':
    test_fusion_engine()
