"""Test various BSE API endpoints."""
import urllib.request, json

def test(url, desc=""):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://www.bseindia.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode())
            print(f"[{res.status}] {desc or url}")
            if isinstance(data, list):
                print(f"  -> list[{len(data)}]")
                if data:
                    sample = data[0]
                    print(f"     keys: {list(sample.keys()) if isinstance(sample, dict) else sample}")
            elif isinstance(data, dict):
                print(f"  -> dict[{len(data)} keys]: {list(data.keys())[:6]}")
            else:
                print(f"  -> {str(data)[:200]}")
    except urllib.error.HTTPError as e:
        print(f"[{e.code}] {desc or url}")
    except Exception as e:
        print(f"[ERR] {desc or url}: {e}")

test("https://api.bseindia.com/BseIndiaAPI/api/Search/w?text=RELIANCE", "Search w/ text")
test("https://api.bseindia.com/BseIndiaAPI/api/Search/w?text=RELIANCE&type=Stocks", "Search w/ text+type")
test("https://api.bseindia.com/BseIndiaAPI/api/ShareHolding/w?scripcode=500325", "ShareHolding 500325")
test("https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode=500325", "shpSecSummery_New 500325")
test("https://api.bseindia.com/BseIndiaAPI/api/Corp_EquitySeries/w?scripcode=500325", "Corp_EquitySeries 500325")
test("https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w?scripcode=500325", "StockReachGraph 500325")
