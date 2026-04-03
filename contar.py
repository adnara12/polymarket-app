import requests

for offset in [0, 5000, 10000, 20000, 50000, 100000]:
    r = requests.get(
        'https://gamma-api.polymarket.com/markets',
        params={'closed': 'true', 'limit': 1, 'offset': offset},
        timeout=15
    )
    data = r.json()
    print(f'offset {offset}: {len(data)} resultados')