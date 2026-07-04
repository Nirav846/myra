import requests, time
r = requests.post('http://localhost:8000/api/liquidity-flip/scan', json={'prior_window': 120, 'recent_window': 21, 'lookback_days': 141})
print('Started:', r.json())
time.sleep(30)
status = requests.get('http://localhost:8000/api/liquidity-flip/status').json()
cands = status.get('candidates', [])
if cands:
    c = cands[0]
    print('Sample keys:', list(c.keys()))
    for key in ['avg_del_value_cr', 'flip_consistency', 'sma_200', 'sma_200_factor']:
        print(f'{key}: {c.get(key)}')
else:
    print('No candidates')
