import requests
import hashlib
import time
from database import upsert_trades

TRADES_URL = "https://data-api.polymarket.com/trades"

def fetch_trades(limit=500, offset=0):
    try:
        resp = requests.get(
            TRADES_URL,
            params={"limit": limit, "offset": offset},
            timeout=15
        )
        if resp.status_code != 200:
            return []
        return resp.json()
    except Exception as e:
        print(f"[scraper] Error: {e}")
        return []

def parse_trade(t):
    wallet    = t.get("proxyWallet") or ""
    market_id = t.get("conditionId") or t.get("asset") or ""
    question  = t.get("title") or market_id[:24]
    side      = t.get("side", "").upper()
    size      = float(t.get("size") or 0)
    price     = float(t.get("price") or 0)
    outcome   = t.get("outcome") or ""
    alias     = t.get("name") or t.get("pseudonym") or ""

    # timestamp Unix → string ISO
    ts_raw = t.get("timestamp")
    if ts_raw:
        try:
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat()
        except Exception:
            ts = str(ts_raw)
    else:
        ts = ""

    raw_id   = f"{wallet}{market_id}{side}{size}{price}{ts_raw}"
    trade_id = hashlib.md5(raw_id.encode()).hexdigest()

    return {
        "id":        trade_id,
        "wallet":    wallet,
        "market_id": market_id,
        "question":  question,
        "side":      side,
        "size":      size,
        "price":     price,
        "timestamp": ts,
        "outcome":   outcome,
        "alias":     alias,
    }

def run_scraper(pages=10):
    print("[scraper] Iniciando descarga de trades...")
    total = 0
    for page in range(pages):
        offset = page * 500
        raw = fetch_trades(limit=500, offset=offset)
        if not raw:
            break
        trades = [parse_trade(t) for t in raw if t.get("proxyWallet")]
        trades = [t for t in trades if t["wallet"] and t["size"] > 0 and t["price"] > 0]
        upsert_trades(trades)
        total += len(trades)
        print(f"[scraper] Página {page+1}: {len(trades)} trades (total: {total})")
        time.sleep(0.5)
    print(f"[scraper] Completado. {total} trades en total.")
    return total

if __name__ == "__main__":
    from database import init_db
    init_db()
    run_scraper(pages=20)