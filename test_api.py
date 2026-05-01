import urllib.request
import json

url = 'http://localhost:8000/api/query'
data = {
    'database': '_tech_conn',
    'query': "SELECT date, vwap, delivery_ma_60, bullish_fvg FROM technical_data WHERE symbol='RELIANCE' ORDER BY date DESC LIMIT 3"
}
req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error: {e}")