import requests
import hashlib
import time
import json
from datetime import datetime, timezone
from database import init_db, upsert_trader, upsert_trades, get_conn

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Palabras que indican mercado de RUIDO (excluir)
NOISE_KEYWORDS = [
    "up or down", "tweets", "tweet", "temperature", "°f", "°c",
    "price above", "price below", "above $", "below $",
    "5-minute", "5 minute", "1-minute", "hourly", "daily candle",
    "total rounds", "total kills", "penta kill", "odd/even",
    "map ", "game ", "set ", "over/under"
]

# Palabras que indican mercado RELEVANTE para insiders
RELEVANT_KEYWORDS = [
    "election", "elect", "president", "prime minister", "chancellor",
    "appointed", "appoint", "resign", "fired", "nomination", "nominee",
    "will ", "sanction", "tariff", "trade deal", "merger", "acquisition",
    "ipo", "bankrupt", "lawsuit", "indicted", "arrested", "convicted",
    "ceasefire", "invasion", "treaty", "agreement", "deal",
    "fed ", "federal reserve", "interest rate", "gdp", "inflation",
    "win the", "won the", "championship", "transfer", "signed",
    "bitcoin", "crypto", "sec ", "regulation", "ban ",
    "poll", "approval rating", "impeach", "bill passed"
]

def is_relevant_market(question):
    """Determina si un mercado es relevante para detectar insiders."""
    q = question.lower()
    for noise in NOISE_KEYWORDS:
        if noise in q:
            return False
    for rel in RELEVANT_KEYWORDS:
        if rel in q:
            return True
    return False

def fetch_user_positions(wallet):
    """Obtiene PnL real de todas las posiciones de un trader."""
    try:
        r = requests.get(f"{DATA_API}/positions",
            params={"user": wallet, "limit": 500}, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [!] positions error {wallet[:10]}: {e}")
        return []

def fetch_user_activity(wallet, limit=500, offset=0):
    """Obtiene historial de trades de un trader."""
    try:
        r = requests.get(f"{DATA_API}/activity",
            params={"user": wallet, "limit": limit, "offset": offset},
            timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [!] activity error {wallet[:10]}: {e}")
        return []

def fetch_market_result(condition_id):
    """Obtiene el resultado de un mercado cerrado."""
    try:
        r = requests.get(f"{GAMMA_API}/markets/{condition_id}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        prices_raw = data.get("outcomePrices", "[]")
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        prices = [float(p) for p in prices]
        outcomes_raw = data.get("outcomes", '["Yes","No"]')
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        for i, p in enumerate(prices):
            if p >= 0.99:
                return outcomes[i] if i < len(outcomes) else None
        return None
    except Exception:
        return None

def parse_activity(act, wallet, market_cache):
    """Convierte un registro de actividad en un trade para la BD."""
    market_id = act.get("conditionId") or ""
    question  = act.get("title") or act.get("question") or market_id[:24]
    side      = act.get("type", "").upper()
    if side not in ("BUY", "SELL"):
        return None
    size      = float(act.get("usdcSize") or act.get("size") or 0)
    price     = float(act.get("price") or 0)
    outcome   = act.get("outcome") or ""

    ts_raw = act.get("timestamp")
    try:
        ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat() if ts_raw else ""
    except Exception:
        ts = str(ts_raw) if ts_raw else ""

    relevant = 1 if is_relevant_market(question) else 0

    # Resultado del mercado (con caché)
    outcome_won = -1
    if market_id and side == "BUY":
        if market_id not in market_cache:
            market_cache[market_id] = fetch_market_result(market_id)
            time.sleep(0.15)
        winner = market_cache[market_id]
        if winner and outcome:
            outcome_won = 1 if outcome.strip().lower() == winner.strip().lower() else 0

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
        "outcome_won": outcome_won,
        "pnl":         0.0,
        "is_relevant": relevant,
    }

def scrape_top_traders(n_traders=200, min_pnl=0):
    """
    Estrategia principal:
    1. Coge wallets conocidas de la BD
    2. Consulta su PnL real via /positions
    3. Descarga historial completo de los top traders
    4. Filtra por mercados relevantes
    """
    print("[scraper] Obteniendo wallets conocidas...")
    conn = get_conn()
    wallets = [r[0] for r in conn.execute(
        "SELECT DISTINCT wallet FROM trades ORDER BY size DESC LIMIT 2000"
    ).fetchall()]
    conn.close()

    if not wallets:
        print("[scraper] No hay wallets. Ejecuta primero scrape_seed().")
        return 0

    print(f"[scraper] Calculando PnL real de {len(wallets)} wallets...")
    trader_pnl = []

    for i, wallet in enumerate(wallets):
        positions = fetch_user_positions(wallet)
        if not positions:
            continue

        total_pnl      = sum(float(p.get("cashPnl") or 0) for p in positions)
        total_invested = sum(float(p.get("initialValue") or 0) for p in positions)
        alias          = positions[0].get("name") or positions[0].get("pseudonym") or ""

        trader_pnl.append((wallet, alias, total_pnl, total_invested))

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(wallets)} wallets procesadas...")
        time.sleep(0.1)

    # Ordenar por PnL y quedarse con los mejores
    trader_pnl.sort(key=lambda x: x[2], reverse=True)
    top_traders = [t for t in trader_pnl if t[2] >= min_pnl][:n_traders]

    print(f"[scraper] Top {len(top_traders)} traders por PnL (mín: {min_pnl} USDC)")

    total_trades = 0
    market_cache = {}

    for rank, (wallet, alias, pnl, invested) in enumerate(top_traders):
        print(f"  [{rank+1}/{len(top_traders)}] {alias or wallet[:12]} | PnL: {pnl:+.0f} USDC")

        upsert_trader(wallet, alias, pnl, invested)

        # Descargar historial completo del trader
        all_activity = []
        offset = 0
        while True:
            batch = fetch_user_activity(wallet, limit=500, offset=offset)
            if not batch:
                break
            all_activity.extend(batch)
            if len(batch) < 500:
                break
            offset += 500
            time.sleep(0.2)

        trades = []
        for act in all_activity:
            t = parse_activity(act, wallet, market_cache)
            if t and t["size"] > 0:
                trades.append(t)

        if trades:
            upsert_trades(trades)
            total_trades += len(trades)
            relevant = sum(1 for t in trades if t["is_relevant"])
            print(f"     {len(trades)} trades ({relevant} relevantes)")

        time.sleep(0.3)

    print(f"[scraper] Completado. {total_trades} trades de {len(top_traders)} traders.")
    return total_trades

def scrape_seed(n_markets=20):
    """
    Descarga inicial: obtiene wallets de mercados cerrados de alto volumen
    para tener una base de datos inicial de traders.
    """
    print("[scraper] Descarga inicial de wallets (seed)...")
    r = requests.get(f"{GAMMA_API}/markets", params={
        "closed": "true", "limit": n_markets,
        "order": "volume", "ascending": "false"
    }, timeout=15)
    markets = r.json()

    wallets_encontradas = set()
    for m in markets:
        cid = m.get("conditionId")
        if not cid:
            continue
        trades_r = requests.get(f"{DATA_API}/trades",
            params={"market": cid, "limit": 500}, timeout=15)
        if trades_r.status_code != 200:
            continue
        for t in trades_r.json():
            w = t.get("proxyWallet")
            if w:
                wallets_encontradas.add(w)
        time.sleep(0.3)

    print(f"[scraper] {len(wallets_encontradas)} wallets encontradas en seed.")

    # Guardar trades básicos para tener las wallets en la BD
    from database import upsert_trades as _ut
    seed_trades = []
    for m in markets:
        cid = m.get("conditionId") or ""
        question = m.get("question", "")
        trades_r = requests.get(f"{DATA_API}/trades",
            params={"market": cid, "limit": 500}, timeout=15)
        if trades_r.status_code != 200:
            continue
        for t in trades_r.json():
            w = t.get("proxyWallet") or ""
            if not w:
                continue
            ts_raw = t.get("timestamp")
            try:
                ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat() if ts_raw else ""
            except Exception:
                ts = ""
            raw_id = f"{w}{cid}{t.get('side','')}{t.get('size',0)}{t.get('price',0)}{ts_raw}"
            seed_trades.append({
                "id":          hashlib.md5(raw_id.encode()).hexdigest(),
                "wallet":      w,
                "market_id":   cid,
                "question":    question,
                "side":        t.get("side", "").upper(),
                "size":        float(t.get("size") or 0),
                "price":       float(t.get("price") or 0),
                "timestamp":   ts,
                "outcome":     t.get("outcome") or "",
                "outcome_won": -1,
                "pnl":         0.0,
                "is_relevant": 1 if is_relevant_market(question) else 0,
            })
        time.sleep(0.3)

    if seed_trades:
        _ut(seed_trades)
    print(f"[scraper] Seed completado. {len(seed_trades)} trades base.")
    return len(wallets_encontradas)

def run_scraper(pages=10):
    """Llamado por el scheduler cada 5 min."""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    if count < 100:
        scrape_seed(n_markets=20)
    return scrape_top_traders(n_traders=100, min_pnl=0)

if __name__ == "__main__":
    init_db()
    scrape_seed(n_markets=30)
    scrape_top_traders(n_traders=200, min_pnl=0)
