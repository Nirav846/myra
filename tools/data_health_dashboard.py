import json
import os
import sys


def main():
    manifest_path = "data_sync_manifest.json"
    if not os.path.exists(manifest_path):
        print(f"Manifest file '{manifest_path}' not found.")
        sys.exit(1)

    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading manifest: {e}")
        sys.exit(1)

    total_symbols_processed = data.get("total_symbols_processed", 0)
    missing_delivery_list = data.get("missing_delivery_list", [])

    if total_symbols_processed > 0:
        score = (
            (total_symbols_processed - len(missing_delivery_list))
            / total_symbols_processed
        ) * 100
    else:
        score = 0.0

    print("================================")
    print("      DATA HEALTH DASHBOARD     ")
    print("================================")
    print(f"Data Confidence Score: {score:.2f}%")
    print(f"Total Symbols Processed: {total_symbols_processed}")
    print(f"Symbols Missing Delivery Data: {len(missing_delivery_list)}")
    print("================================")

    if missing_delivery_list:
        print("Missing Delivery Symbols:")
        for symbol in missing_delivery_list:
            print(f"  - {symbol}")


if __name__ == "__main__":
    main()
