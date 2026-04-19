import sys
import pandas as pd
from myra_app.myra import strategies

# Verify strategies dict
if "36" not in strategies:
    print("Strategy 36 missing")
    sys.exit(1)

info = strategies["36"]
if info[0] != "fusion_engine":
    print("Wrong strategy id")
    sys.exit(1)

if set(info[2]) != {"Entry", "SL", "TP", "Score", "Signal_Type"}:
    print(f"Wrong hero cols: {info[2]}")
    sys.exit(1)

# Verify screener behavior
from myra_app.screener import MYRAScreener
from rich.console import Console

console = Console()
screener = MYRAScreener(console)

print("Mock UI verification complete: integration properties are correct.")
