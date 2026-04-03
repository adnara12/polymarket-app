import requests
from collections import Counter

categorias = Counter()
slugs_excluir = ["up-or-down", "tweet", "temperature", "weather", "price-above", "price-below"]

offset = 0
total = 0
while offset < 2000:
    r = requests.get("https://gamma-api.polymarket.com/markets", params={
        "closed": "true", "limit": 500, "offset": offset,
        "order": "volume", "ascending": "false"
    }, timeout=15)
    data = r.json()
    if not data:
        break
    for m in data:
        cat = m.get("category") or m.get("tags") or "sin_categoria"
        if isinstance(cat, list):
            cat = cat[0] if cat else "sin_categoria"
        categorias[str(cat)] += 1
        total += 1
    offset += 500
    print(f"Procesados {total} mercados...")

print("\nCategorías encontradas:")
for cat, count in categorias.most_common(30):
    print(f"  {cat}: {count}")