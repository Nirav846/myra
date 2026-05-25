"""Test dalal shareholding with all fields."""
import dalal, json

sh = dalal.shareholding("ITC")
if sh:
    latest = sh[0]
    print(f"ITC latest quarter: {latest.get('date')}")
    print(f"  promoter% (pr_and_prgrp): {latest.get('pr_and_prgrp')}")
    print(f"  public% (public_val): {latest.get('public_val')}")
    print(f"  All keys:")
    for k, v in latest.items():
        print(f"    {k}: {v}")

# Also test HDFCBANK
sh2 = dalal.shareholding("HDFCBANK")
if sh2:
    latest2 = sh2[0]
    print(f"\nHDFCBANK latest: {latest2.get('date')}")
    print(f"  promoter%: {latest2.get('pr_and_prgrp')}")
    print(f"  public%: {latest2.get('public_val')}")

# Test a small cap
for sym in ["TCS", "INFY", "SBIN", "MARUTI", "HAL"]:
    try:
        sh3 = dalal.shareholding(sym)
        if sh3:
            lat = sh3[0]
            print(f"\n{sym}: promoter={lat.get('pr_and_prgrp')}%, public={lat.get('public_val')}%, date={lat.get('date')}")
    except Exception as e:
        print(f"\n{sym}: ERROR: {e}")
