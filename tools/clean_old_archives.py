import pandas as pd
import os

ARCHIVE_DIR = os.path.join("data", "Market_Archives")
KEEP_SERIES = {"eq", "be", "sm"}

for filename in os.listdir(ARCHIVE_DIR):
    if not filename.endswith(".csv"):
        continue
    filepath = os.path.join(ARCHIVE_DIR, filename)
    try:
        df = pd.read_csv(filepath)
        # Normalize column names: strip spaces, lowercase
        df.columns = [c.strip().lower() for c in df.columns]

        # Find the series column (may be named 'series', ' series', 'series ', etc.)
        series_col = None
        for col in df.columns:
            if col.strip() == "series":
                series_col = col
                break

        if series_col is None:
            print(f"{filename}: no 'series' column found (columns: {df.columns.tolist()[:5]}) – skipping")
            continue

        # Normalize series values: strip spaces, uppercase
        before = len(df)
        df[series_col] = df[series_col].astype(str).str.strip().str.lower()
        df = df[df[series_col].isin(KEEP_SERIES)]
        after = len(df)

        # Only overwrite if we kept something (safety check)
        if after == 0 and before > 0:
            print(f"{filename}: WARNING – kept 0 rows after filtering, NOT overwriting file. Check series values.")
            continue

        if before != after:
            df.to_csv(filepath, index=False)
            print(f"{filename}: removed {before - after} rows, kept {after}")
        else:
            print(f"{filename}: already clean")
    except Exception as e:
        print(f"{filename}: error – {e}")
