"""Explore shpSecSummery_New response for RELIANCE."""
import urllib.request, json, html
from bs4 import BeautifulSoup

# First find RELIANCE's scrip code
# BSE scrip code for RELIANCE is 500325
scripcode = "500325"

req = urllib.request.Request(
    f"https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode={scripcode}",
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.bseindia.com/",
    },
)
with urllib.request.urlopen(req, timeout=15) as res:
    raw = json.loads(res.read().decode())
    data_html = raw.get("Data", "")
    print(f"HTML length: {len(data_html)}")
    print(f"First 500 chars:\n{data_html[:500]}")
    print(f"\n---\nLast 500 chars:\n{data_html[-500:]}")

    # Parse with BeautifulSoup
    soup = BeautifulSoup(data_html, "html.parser")
    tables = soup.find_all("table")
    print(f"\nTables found: {len(tables)}")
    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        print(f"\nTable {i}: {len(rows)} rows")
        for row in rows[:10]:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            print(f"  {cells}")
