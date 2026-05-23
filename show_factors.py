import requests, json
r = requests.get('http://localhost:8000/api/ml/factor-importance')
data = r.json()
print('\n=== TOP FEATURES ===')
for item in data['top_features']:
    print(f"{item['feature']:30s} {item['importance']:.4f}")
print('\n=== BY CATEGORY ===')
for cat, items in data.get('by_category', {}).items():
    print(f'\n{cat}:')
    for item in items:
        print(f"  {item['feature']:25s} {item['importance']:.4f}")
