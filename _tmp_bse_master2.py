"""Try direct NSE-to-BSE mapping and BSE master download."""
import urllib.request, json, csv, io, zipfile

# Approach 1: Try BSE corporate info search (which returned JSON) for a lookup
# CorpInfo works with scripcode. We need to find the scripcode for a given symbol.
# BSE stock page URL pattern: https://www.bseindia.com/stock-share-price/{name}/{symbol}/{scripcode}/
# We can try finding the stock page first

# Approach 2: Try BSE stock price API with symbol directly  
# Some BSE quote APIs accept the symbol
test_urls = [
    # Try corp info search by name
    "https://api.bseindia.com/BseIndiaAPI/api/CorpInfo/w?scripcode=500325",
    # Try the shpSecSummery_New (this works)
    "https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode=500325",
    # Try master data download (bhavcopy)
    "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_30052026.ZIP",  # today
    "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_22052026.ZIP",  # recent
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

for url in test_urls:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as res:
            body = res.read()
            ct = res.headers.get("Content-Type", "")
            print(f"[{res.status}] {url}")
            if "json" in ct:
                data = json.loads(body)
                print(f"  JSON keys: {list(data.keys())[:6]}")
            elif "zip" in ct or "application/octet-stream" in ct:
                print(f"  ZIP file: {len(body)} bytes, attempting to extract...")
                try:
                    with zipfile.ZipFile(io.BytesIO(body)) as zf:
                        names = zf.namelist()
                        print(f"  Files: {names}")
                        with zf.open(names[0]) as f:
                            content = f.read().decode("ascii", errors="replace")
                            lines = content.split("\n")
                            print(f"  Lines: {len(lines)}")
                            for line in lines[:5]:
                                print(f"    {line[:150]}")
                            # Count how many have RELIANCE
                            for line in lines:
                                if "RELIANCE" in line.upper() and "500325" in line:
                                    print(f"  FOUND RELIANCE: {line[:200]}")
                                    break
                except:
                    print(f"  Not a valid ZIP")
            else:
                print(f"  {ct[:30]}: {len(body)} bytes")
    except urllib.error.HTTPError as e:
        print(f"[{e.code}] {url}")
    except Exception as e:
        print(f"[ERR] {url}: {e}")
