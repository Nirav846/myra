"""Try BSE scrip code search approaches."""
import urllib.request, json

# Try various known BSE API patterns for search/lookup
endpoints = [
    # ScripMaster variants
    "https://api.bseindia.com/BseIndiaAPI/api/ScripMaster/w",
    "https://api.bseindia.com/BseIndiaAPI/api/Corp_EquitySeries/w",
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripMaster/w",
    "https://api.bseindia.com/BseIndiaAPI/api/SecurityMaster/w",
    # Master download
    "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_30052024.ZIP",
    # Alternative search endpoint
    "https://api.bseindia.com/BseIndiaAPI/api/Search/w?text=RELIANCE&type=Stocks",
    # Stock quote (might tell us scrip code)
    "https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w?scripcode=500325",
    # Corporate announcement search
    "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryByDate/w?fromDate=01-Jan-2025&toDate=31-Dec-2025&scripcode=500325",
    # Try with different search parameter
    "https://api.bseindia.com/BseIndiaAPI/api/Search/w?search=RELIANCE",
    # BSE main site corporate info
    "https://api.bseindia.com/BseIndiaAPI/api/CorpInfo/w?scripcode=500325",
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/",
}

for url in endpoints:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as res:
            body = res.read()
            content_type = res.headers.get("Content-Type", "")
            if "json" in content_type:
                data = json.loads(body)
                print(f"[200 JSON] {url}")
                if isinstance(data, list):
                    print(f"  list[{len(data)}] keys[0]: {list(data[0].keys()) if data else 'empty'}")
                elif isinstance(data, dict):
                    print(f"  dict keys: {list(data.keys())[:6]}")
            else:
                print(f"[200 {content_type[:30]}] {url}  ({len(body)} bytes)")
    except urllib.error.HTTPError as e:
        print(f"[{e.code}] {url}")
    except Exception as e:
        print(f"[ERR] {url}: {type(e).__name__}: {str(e)[:80]}")
