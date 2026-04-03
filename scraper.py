import requests
import hashlib
import time
import json
from datetime import datetime, timezone
from database import init_db, upsert_trader, upsert_trades, get_conn

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

NOISE_KEYWORDS = [
    "up or down", "above $", "below $", "price above", "price below",
    "bitcoin", "ethereum", "solana", "xrp", "dogecoin", "crypto",
    "btc", "eth", "sol", "bnb", "avax", "doge", "pepe", "shib",
    "token", "coin", "defi", "nft", "blockchain",
    "total kills", "total rounds", "penta kill", "odd/even",
    "over/under", "o/u", "spread:", "handicap",
    "map ", "set 1", "set 2", "game 1", "game 2",
    "most kills", "first blood",
    "temperature", "rainfall", "weather", "hurricane",
    "tweets", "tweet", "followers", "views", "likes",
    "box office", "oscars", "grammy", "emmy", "album",
    "season ", "episode ", "streaming",
    "5-minute", "5 minute", "1-minute", "15-minute",
    "hourly", "daily candle", "weekly candle",
    "trail blazers", "lakers", "celtics", "knicks", "warriors",
    "nba:", "nfl:", "mlb:", "nhl:", "epl:",
"manchester", "fc ", " fc", "juventus", "barcelona", "real madrid",
"chelsea", "arsenal", "liverpool", "milan", "inter",
"nba finals", "super bowl", "superbowl", "world series",
"champions league", "premier league", "la liga", "serie a",
"atp", "wta", "wimbledon", "us open", "french open",
"call of duty", "faze", "esport", "gaming",
"oscar", "sag award", "golden globe", "emmy",
"proof of love", "reality show", "bachelor",
"ucl", "uefa", "fifa",
"club brugge", "marseille", "brugge",
"golf", "pga", "lpga", "masters",
"nfl", "nba", "mlb", "nhl",
"combined points", "margin of victory",
]

RELEVANT_KEYWORDS = [
    "election", "elected", "elect ", "primary", "candidate",
    "president", "presidential", "prime minister", "chancellor",
    "governor", "senator", "congress", "parliament", "cabinet",
    "poll ", "polling", "approval rating", "vote ", "votes ",
    "ballot", "referendum", "impeach", "resign", "resignation",
    "fired", "removed from", "step down",
    "nomination", "nominee", "appointed", "appointment",
    "who will be", "who wins", "who becomes",
    "war ", "attack ", "invasion", "ceasefire", "peace deal",
    "sanctions", "sanction ", "troops", "military",
    "treaty ", "agreement ", "negotiations", "summit",
    "nato", "united nations", "un security",
    "coup", "protest", "revolution",
    "federal reserve", "fed rate", "interest rate",
    "gdp", "recession", "inflation rate",
    "ipo ", "merger", "acquisition", "takeover",
    "bankrupt", "default ", "bailout",
    "tariff", "trade deal", "trade war",
    "sec ", "regulation", "ban ", "lawsuit",
    "indicted", "arrested", "convicted", "charged",
    "fine ", "penalty", "settlement",
    "ceo", "chairman", "appointed as", "named as",
    "steps down", "leaving ", "replace",
    "transfer", "signed by", "signs with",
    "fired as manager", "new manager", "new coach",
    "will play for", "championship winner",
    "world cup winner", "superbowl winner",
"win the", "winner", "will win", "will be", "will the",
"next ", "first ", "when will", "how many",
"2024", "2025", "2026",
"trump", "biden", "harris", "republican", "democrat",
"ukraine", "russia", "china", "israel", "iran", "gaza",
"supreme court", "white house", "congress",
"elon", "musk", "tesla", "spacex",
"fed ", "powell", "yellen",
"oil ", "gold ", "dollar",
]

def is_relevant_market(question):
    q = question.lower()
    for noise in NOISE_KEYWORDS:
        if noise in q:
            return False
    for rel in RELEVANT_KEYWORDS:
        if rel in q:
            return True
    return False

def fetch_user_positions(wallet):
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
    market_id = act.get("conditionId") or ""
    question  = act.get("title") or act.get("question") or market_id[:24]
    side      = act.get("type", "").upper()
    if side not in ("BUY", "SELL"):
        side = act.get("side", "").upper()
    if side not in ("BUY", "SELL"):
        return None

    size    = float(act.get("usdcSize") or act.get("size") or 0)
    price   = float(act.get("price") or 0)
    outcome = act.get("outcome") or ""

    ts_raw = act.get("timestamp")
    try:
        ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat() if ts_raw else ""
    except Exception:
        ts = str(ts_raw) if ts_raw else ""

    relevant    = 1 if is_relevant_market(question) else 0
    outcome_won = -1

    if market_id and side == "BUY" and outcome:
        if market_id not in market_cache:
            market_cache[market_id] = fetch_market_result(market_id)
            time.sleep(0.1)
        winner = market_cache[market_id]
        if winner:
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

def scrape_seed(n_markets=30):
    print("[scraper] Descarga inicial de wallets (seed)...")
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={
            "closed": "true", "limit": 500,
            "order": "volume", "ascending": "false",
            "volumeNum_gte": 10000
        }, timeout=15)
        all_markets = r.json()
    except Exception as e:
        print(f"[scraper] Error obteniendo mercados: {e}")
        return 0

    markets = [m for m in all_markets if is_relevant_market(m.get("question", ""))][:n_markets]
    print(f"[scraper] {len(markets)} mercados relevantes de {len(all_markets)} totales.")

    seed_trades = []
    for idx, m in enumerate(markets):
        cid      = m.get("conditionId") or ""
        question = m.get("question", "")
        if not cid:
            continue

        print(f"  [{idx+1}/{len(markets)}] {question[:60]}")

        try:
            trades_r = requests.get(f"{DATA_API}/trades",
                params={"market": cid, "limit": 500}, timeout=15)
            if trades_r.status_code != 200:
                continue
            raw_trades = trades_r.json()
        except Exception:
            continue

        for t in raw_trades:
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
                "is_relevant": 1,
            })
        time.sleep(0.3)

    if seed_trades:
        upsert_trades(seed_trades)

    conn = get_conn()
    n_wallets = conn.execute("SELECT COUNT(DISTINCT wallet) FROM trades").fetchone()[0]
    conn.close()
    print(f"[scraper] Seed completado. {len(seed_trades)} trades, {n_wallets} wallets.")
    return n_wallets

def scrape_top_traders(n_traders=100, min_pnl=200, max_trades=200):
    conn = get_conn()
    wallets = [r[0] for r in conn.execute(
        "SELECT DISTINCT wallet FROM trades ORDER BY size DESC LIMIT 3000"
    ).fetchall()]
    conn.close()

    if not wallets:
        print("[scraper] No hay wallets. Ejecutando seed...")
        scrape_seed()
        conn = get_conn()
        wallets = [r[0] for r in conn.execute(
            "SELECT DISTINCT wallet FROM trades LIMIT 3000"
        ).fetchall()]
        conn.close()

    print(f"[scraper] Calculando PnL real de {len(wallets)} wallets...")
    trader_pnl = []

    for i, wallet in enumerate(wallets):
        positions = fetch_user_positions(wallet)
        if not positions:
            time.sleep(0.05)
            continue

        total_pnl      = sum(float(p.get("cashPnl") or 0) for p in positions)
        total_invested = sum(float(p.get("initialValue") or 0) for p in positions)
        n_positions    = len(positions)
        alias          = ""
        for p in positions:
            alias = p.get("name") or p.get("pseudonym") or ""
            if alias:
                break

        if total_pnl >= min_pnl and n_positions <= max_trades:
            trader_pnl.append((wallet, alias, total_pnl, total_invested, n_positions))

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(wallets)} procesadas... ({len(trader_pnl)} selectivos)")
        time.sleep(0.1)

    trader_pnl.sort(key=lambda x: x[2], reverse=True)
    top_traders = trader_pnl[:n_traders]

    print(f"\n[scraper] Top {len(top_traders)} traders selectivos:")
    for rank, (w, alias, pnl, inv, n_pos) in enumerate(top_traders[:15]):
        print(f"  #{rank+1} {alias or w[:14]} | PnL: +{pnl:.0f} USDC | Posiciones: {n_pos}")

    total_trades = 0
    market_cache = {}

    for rank, (wallet, alias, pnl, invested, n_pos) in enumerate(top_traders):
        print(f"\n[{rank+1}/{len(top_traders)}] {alias or wallet[:14]} | PnL: +{pnl:.0f} USDC | {n_pos} pos.")
        upsert_trader(wallet, alias, pnl, invested)

        print(f"  Descargando actividad...")
        activity = fetch_user_activity(wallet, limit=500, offset=0)

        if not activity:
            print(f"  Sin actividad.")
            continue

        if len(activity) >= 500:
            all_activity = activity
            offset = 500
            while len(all_activity) < max_trades * 2:
                batch = fetch_user_activity(wallet, limit=500, offset=offset)
                if not batch:
                    break
                all_activity.extend(batch)
                if len(batch) < 500:
                    break
                offset += 500
                time.sleep(0.2)
            activity = all_activity[:max_trades * 2]

        print(f"  {len(activity)} registros")

        trades = []
        for act in activity:
            t = parse_activity(act, wallet, market_cache)
            if t and t["size"] > 0:
                trades.append(t)

        if trades:
            upsert_trades(trades)
            total_trades += len(trades)
            relevant = sum(1 for t in trades if t["is_relevant"])
            won      = sum(1 for t in trades if t["outcome_won"] == 1)
            print(f"  {len(trades)} trades | {relevant} relevantes | {won} ganados")
        else:
            print(f"  Sin trades validos.")

        time.sleep(0.3)

    print(f"\n[scraper] Completado. {total_trades} trades de {len(top_traders)} traders.")
    return total_trades

def run_scraper(pages=10):
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    if count < 100:
        scrape_seed(n_markets=30)
    return scrape_top_traders(n_traders=100, min_pnl=200, max_trades=200)

if __name__ == "__main__":
    init_db()
    scrape_seed(n_markets=30)
    scrape_top_traders(n_traders=200, min_pnl=200, max_trades=200)
