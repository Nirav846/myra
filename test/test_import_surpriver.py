
import sys
try:
    import myra_app.strategies.surpriver_v2 as s
    print("Import successful")
    strat = s.Strategy()
    print("Strategy initialized")
except Exception as e:
    print(f"Import failed: {e}")
    import traceback
    traceback.print_exc()
