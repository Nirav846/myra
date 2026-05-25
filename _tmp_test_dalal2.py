"""Test dalal shareholding properly."""
import dalal
import json, sys

try:
    sh = dalal.shareholding("RELIANCE")
    print(f"Type: {type(sh).__name__}, Length: {len(sh)}")
    if sh:
        latest = sh[0]
        print(f"\nLatest quarter: {latest}")
        # Print all keys
        if isinstance(latest, dict):
            for k, v in latest.items():
                print(f"  {k}: {v}")
    # Print first 3 entries summary
    for entry in sh[:3]:
        if isinstance(entry, dict):
            quarter = entry.get("quarter", entry.get("period", "?"))
            promoter = entry.get("promoter", entry.get("promoterPct", entry.get("promoter_pct", "?")))
            public = entry.get("public", entry.get("publicPct", entry.get("public_pct", "?")))
            print(f"  Period: {quarter}, Promoter: {promoter}, Public: {public}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {str(e)[:200]}")
    sys.stdout.flush()
