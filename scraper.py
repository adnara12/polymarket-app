import sys
import requests
import hashlib
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from database import init_db, upsert_trader, upsert_trades, get_conn

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

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

LEADERBOARD_CATEGORIES = ["politics", "geopolitics", "tech", "economy", "sports"]

def fetch_leaderboard(category="politics", time_period="all", order_by="PNL", n=100):
    """Fetch top n traders from one leaderboard category (50 per page)."""
    traders = []
    offset = 0
    while len(traders) < n:
        try:
            r = requests.get(f"{DATA_API}/v1/leaderboard", params={
                "timePeriod": time_period,
                "orderBy":    order_by,
                "limit":      50,
                "offset":     offset,
                "category":   category,
            }, timeout=15)
            if r.status_code != 200:
                break
            batch = r.json()
            if not batch:
                break
            traders.extend(batch)
            if len(batch) < 50:
                break
            offset += 50
        except Exception as e:
            print(f"  [!] leaderboard error ({category}): {e}")
            break
    return traders[:n]

def fetch_all_leaderboards(n_per_category=100, workers=5):
    """Fetch top traders from all categories and merge, keeping best PnL per wallet."""
    merged = {}

    def _fetch(cat):
        return cat, fetch_leaderboard(category=cat, n=n_per_category)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch, cat): cat for cat in LEADERBOARD_CATEGORIES}
        for fut in as_completed(futures):
            cat, entries = fut.result()
            print(f"  {cat}: {len(entries)} traders")
            for entry in entries:
                wallet = entry["proxyWallet"]
                pnl    = float(entry.get("pnl") or 0)
                if wallet not in merged or pnl > float(merged[wallet].get("pnl") or 0):
                    merged[wallet] = entry

    return sorted(merged.values(), key=lambda x: float(x.get("pnl") or 0), reverse=True)

def fetch_user_activity(wallet, limit=500, offset=0):
    try:
        r = requests.get(f"{DATA_API}/activity",
            params={"user": wallet, "limit": limit, "offset": offset},
            timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []

def fetch_market_result(condition_id):
    try:
        r = requests.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        for token in data.get("tokens", []):
            if token.get("winner") is True:
                return token.get("outcome")
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

def _fetch_trader_activity(wallet, max_trades):
    """Download full activity list for one wallet."""
    activity = fetch_user_activity(wallet, limit=500, offset=0)
    if not activity:
        return []
    offset = 500
    while len(activity) < max_trades * 2 and len(activity) % 500 == 0:
        batch = fetch_user_activity(wallet, limit=500, offset=offset)
        if not batch:
            break
        activity.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return activity[:max_trades * 2]

def scrape_from_leaderboard(n_traders=100, max_trades=200, workers=5,
                             category=None, time_period="all"):
    print(f"[scraper] Obteniendo traders del leaderboard ({', '.join(LEADERBOARD_CATEGORIES)})...")
    lb = fetch_all_leaderboards(n_per_category=n_traders, workers=workers)
    if not lb:
        print("[scraper] No se pudo obtener el leaderboard.")
        return 0

    print(f"[scraper] {len(lb)} traders unicos encontrados:")
    for i, t in enumerate(lb[:10], 1):
        print(f"  #{i} {t['userName']} | PnL: {float(t.get('pnl',0)):,.0f} USDC")

    print(f"\n[scraper] Descargando actividad ({workers} workers en paralelo)...")
    trader_activities = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_trader_activity, t["proxyWallet"], max_trades): t
                   for t in lb}
        done = 0
        for fut in as_completed(futures):
            t = futures[fut]
            trader_activities[t["proxyWallet"]] = fut.result()
            done += 1
            if done % 10 == 0:
                print(f"  {done}/{len(lb)} actividades descargadas...")

    total_trades = 0
    market_cache = {}

    for idx, t in enumerate(lb):
        wallet = t["proxyWallet"]
        alias  = t.get("userName") or t.get("pseudonym") or ""
        pnl    = float(t.get("pnl") or 0)
        vol    = float(t.get("vol") or 0)

        upsert_trader(wallet, alias, pnl, vol)

        activity = trader_activities.get(wallet, [])
        print(f"\n[{idx+1}/{len(lb)}] {alias} | PnL: {pnl:,.0f} | {len(activity)} registros")

        if not activity:
            print(f"  Sin actividad.")
            continue

        trades = []
        for act in activity:
            tr = parse_activity(act, wallet, market_cache)
            if tr and tr["size"] > 0:
                trades.append(tr)

        if trades:
            upsert_trades(trades)
            total_trades += len(trades)
            relevant = sum(1 for tr in trades if tr["is_relevant"])
            won      = sum(1 for tr in trades if tr["outcome_won"] == 1)
            print(f"  {len(trades)} trades | {relevant} relevantes | {won} ganados")
        else:
            print(f"  Sin trades validos.")

    print(f"\n[scraper] Completado. {total_trades} trades de {len(lb)} traders.")
    return total_trades

def run_scraper(pages=10):
    return scrape_from_leaderboard(n_traders=100, max_trades=200)

if __name__ == "__main__":
    init_db()
    scrape_from_leaderboard(n_traders=100, max_trades=200)
