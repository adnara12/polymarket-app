import requests, json

resp = requests.get("https://data-api.polymarket.com/trades", 
                    params={"limit": 3}, timeout=15)
trades = resp.json()
print(json.dumps(trades[0], indent=2))
