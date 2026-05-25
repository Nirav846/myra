"""Test BSE APIs for RELIANCE."""
import urllib.request, json

# 1. Search API
sym = "RELIANCE"
req = urllib.request.Request(
    f"https://api.bseindia.com/BseIndiaAPI/api/Search/w?text={sym}",
    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=15) as res:
        data = json.loads(res.read().decode())
        print(f"BSE Search for '{sym}':")
        if isinstance(data, list):
            print(f"  Results: {len(data)}")
            for item in data[:5]:
                print(f"    scripCode: {item.get('scripCode')}, scripName: {item.get('scripName')}, bseticker: {item.get('bseticker')}")
                # Find RELIANCE match
                if "RELIANCE" in str(item.get("scripName", "")).upper():
                    scripcode = item.get("scripCode")
                    print(f"\n  Found RELIANCE: scripCode={scripcode}")

                    # 2. Shareholding API
                    req2 = urllib.request.Request(
                        f"https://api.bseindia.com/BseIndiaAPI/api/ShareHolding/w?scripcode={scripcode}",
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                    )
                    with urllib.request.urlopen(req2, timeout=15) as res2:
                        sh_data = json.loads(res2.read().decode())
                        print(f"\n  Shareholding response type: {type(sh_data).__name__}")
                        if isinstance(sh_data, list):
                            print(f"  Categories: {len(sh_data)}")
                            for cat in sh_data:
                                print(f"    cat: {cat.get('cat')}, per: {cat.get('per')}, no_of_share: {cat.get('no_of_share')}")
                        elif isinstance(sh_data, dict):
                            for k, v in sh_data.items():
                                print(f"    {k}: {str(v)[:200]}")
        else:
            print(f"  Response: {str(data)[:500]}")
except Exception as e:
    print(f"ERROR: {e}")
