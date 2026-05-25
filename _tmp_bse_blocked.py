"""Check what the 12565-byte HTML blocking page is."""
import urllib.request

# The shpSecSummery_New worked earlier, try again with fresh session
url = "https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode=500325"

# Use requests-style browser-like headers
req = urllib.request.Request(
    url,
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.bseindia.com/stock-share-price/reliance-industries-ltd/reliance/500325/",
        "Origin": "https://www.bseindia.com",
        "Connection": "keep-alive",
        "DNT": "1",
    },
)

try:
    with urllib.request.urlopen(req, timeout=15) as res:
        body = res.read()
        html = body.decode("utf-8", errors="replace")
        print(f"Status: {res.status}")
        print(f"Content-Type: {res.headers.get('Content-Type')}")
        print(f"HTML ({len(body)} bytes):")
        print(html[:1000])
        print("...")
        # Search for error keywords
        for kw in ["error", "blocked", "session", "forbidden", "denied", "timeout", "expired"]:
            if kw in html.lower():
                print(f"  Found keyword: '{kw}'")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    print(e.read()[:500].decode("utf-8", errors="replace"))
