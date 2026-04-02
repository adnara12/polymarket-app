import requests
import hashlib
import time
import json
from datetime import datetime, timezone
from database import upsert_trades, update_market_outcomes, init_db

GAMMA_URL  = "https://gamma-api.polymarket.com"
TRADES_URL = "https://data-api.polymarket.com/trades"

def fetch_closed_markets(limit=50, offset=0):
    try:
        r = requests.get(f"{GAMMA_URL}/markets", params={
            "closed": "true", "limit": limit, "offset": offset,
            "order": "volume", "ascending": "false"
        }, timeout=15)
        if r.status_code != 200:
            return []
        return r.json()
    except Exception as e:
        print(f"[scraper] Error mercados: {e}")
        return []

def fetch_trades_for_market(condition_id, limit=500, offset=0):
    try:
        r = requests.get(TRADES_URL, params={
            "market": condition_id, "limit": limit, "offset": offset
        }, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[scraper] Error trades {condition_id[:12]}: {e}")
        return []

def get_winner(market):
    """Devuelve el outcome ganador del mercado o None si no está resuelto."""
    try:
        prices_raw = market.get("outcomePrices", "[]")
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        prices = [float(p) for p in prices]
        outcomes = market.get("outcomes", '["Yes","No"]')
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        for i, p in enumerate(prices):
            if p >= 0.99:
                return outcomes[i] if i < len(outcomes) else None
        return None
    except Exception:
        return None

def parse_trade(t, market, winner):
    wallet    = t.get("proxyWallet") or ""
    market_id = t.get("conditionId") or t.get("asset") or ""
    question  = t.get("title") or market.get("question", market_id[:24])
    side      = t.get("side", "").upper()
    size      = float(t.get("size") or 0)
    price     = float(t.get("price") or 0)
    outcome   = t.get("outcome") or ""
    alias     = t.get("name") or t.get("pseudonym") or ""

    ts_raw = t.get("timestamp")
    if ts_raw:
        try:
            ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat()
        except Exception:
            ts = str(ts_raw)
    else:
        ts = ""

    # Determinar si ganó: comparamos el outcome del trade con el ganador del mercado
    if winner and outcome and side == "BUY":
        outcome_won = 1 if outcome.strip().lower() == winner.strip().lower() else 0
    else:
        outcome_won = -1  # mercado aún abierto o no determinable

    raw_id   = f"{wallet}{market_id}{side}{size}{price}{ts_raw}"
    trade_id = hashlib.md5(raw_id.encode()).hexdigest()

    return {
        "id":          trade_id,
        "wallet":      wallet,
        "market_id":   market_id,
        "question":    question,
        "side":        side,
        "size":        size,
        "price":       price,
        "timestamp":   ts,
        "outcome":     outcome,
        "alias":       alias,
        "outcome_won": outcome_won,
    }

def scrape_closed_markets(n_markets=100):
    """Descarga trades de mercados cerrados y sabe directamente si ganaron."""
    print(f"[scraper] Descargando trades de mercados cerrados (objetivo: {n_markets})...")
    total_trades = 0
    markets_done = 0
    offset = 0
    batch  = 50

    while markets_done < n_markets:
        markets = fetch_closed_markets(limit=batch, offset=offset)
        if not markets:
            break

        for market in markets:
            condition_id = market.get("conditionId") or market.get("id")
            if not condition_id:
                continue

            winner = get_winner(market)
            question = market.get("question", "")[:60]
            print(f"  [{markets_done+1}] {question[:50]} | ganador: {winner or '?'}")

            # Descarga todos los trades de este mercado (paginado)
            market_trades = []
            trade_offset  = 0
            while True:
                batch_trades = fetch_trades_for_market(condition_id, limit=500, offset=trade_offset)
                if not batch_trades:
                    break
                market_trades.extend(batch_trades)
                if len(batch_trades) < 500:
                    break
                trade_offset += 500
                time.sleep(0.2)

            parsed = []
            for t in market_trades:
                if not t.get("proxyWallet"):
                    continue
                p = parse_trade(t, market, winner)
                if p["wallet"] and p["size"] > 0 and p["price"] > 0:
                    parsed.append(p)

            if parsed:
                upsert_trades(parsed)
                total_trades += len(parsed)

            markets_done += 1
            time.sleep(0.3)

            if markets_done >= n_markets:
                break

        offset += batch
        if len(markets) < batch:
            break

    print(f"[scraper] Completado. {total_trades} trades de {markets_done} mercados cerrados.")
    return total_trades

def run_scraper(pages=10):
    """Llamado por el scheduler cada 5 min — descarga mercados cerrados recientes."""
    return scrape_closed_markets(n_markets=pages * 5)

if __name__ == "__main__":
    init_db()
    scrape_closed_markets(n_markets=200)