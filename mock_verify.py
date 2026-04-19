import sys
import pandas as pd

def get_strategies():
    import ast
    with open("myra_app/myra.py") as f:
        for node in ast.walk(ast.parse(f.read())):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "strategies":
                return ast.literal_eval(node.value)
    return {}

strategies = get_strategies()

# Verify strategies dict
if "36" not in strategies:
    print("Strategy 36 missing")
    sys.exit(1)

info = strategies["36"]
if info[0] != "fusion_engine":
    print("Wrong strategy id")
    sys.exit(1)

# Verifying Signal_Type inclusion
if set(info[2]) != {"Entry", "SL", "TP", "Score", "Signal_Type"}:
    print(f"Wrong hero cols: {info[2]}")
    sys.exit(1)

print("Mock UI verification complete: integration properties are correct.")
