import requests
import json

# Cogemos una wallet de las que ya tenemos en nuestra BD
from database import get_conn
conn = get_conn()
wallet = conn.execute("SELECT wallet FROM trades ORDER BY size DESC LIMIT 1").fetchone()[0]
conn.close()

print(f"Probando wallet: {wallet}")

# Endpoint de actividad por usuario
urls = [
    f"https://data-api.polymarket.com/activity?user={wallet}&limit=5",
    f"https://data-api.polymarket.com/positions?user={wallet}&limit=5",
    f"https://gamma-api.polymarket.com/positions?user={wallet}&limit=5",
]

for url in urls:
    r = requests.get(url, timeout=10)
    print(f"\n[{r.status_code}] {url[:70]}")
    try:
        print(json.dumps(r.json(), indent=2)[:400])
    except:
        print(r.text[:200])