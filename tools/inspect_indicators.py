import os
import sys

import pandas as pd

targets = sys.argv[1:] or ["DCAL", "WABAG", "PARKHOTELS", "DIVISLAB", "METROPOLIS"]
for s in targets:
    p = os.path.join("data", "indicators", "precomputed", f"{s}.parquet")
    if not os.path.exists(p):
        print(s, "MISSING")
        continue
    df = pd.read_parquet(p)
    dup = df.index.duplicated().any()
    last_ias = (
        df["ias"].dropna().iloc[-1]
        if "ias" in df.columns and df["ias"].dropna().any()
        else "IAS_NOT_FOUND"
    )
    print(f"{s}: rows={len(df)} duplicate_index={dup} last_ias={last_ias}")
