"""Check what the 1814-byte HTML error is."""
import urllib.request

url = "https://api.bseindia.com/BseIndiaAPI/api/Search/w?text=RELIANCE"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/",
}

# First warm up with the main page to get cookies
import http.cookiejar
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# First request to main site
req_main = urllib.request.Request(
    "https://www.bseindia.com/",
    headers={"User-Agent": "Mozilla/5.0"},
)
opener.open(req_main, timeout=10)
print(f"Cookies after main page: {len(cj)}")

# Now try search
req2 = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/",
})
with opener.open(req2, timeout=10) as res:
    body = res.read()
    print(f"Status: {res.status}, Content-Type: {res.headers.get('Content-Type')}")
    print(f"Body ({len(body)} bytes):")
    print(body[:500].decode("utf-8", errors="replace"))
