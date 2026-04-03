from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from database import (init_db, get_ranking, get_trader_trades,
                      get_watchlist, add_to_watchlist, remove_from_watchlist)
from scraper import run_scraper
import atexit

app = Flask(__name__)
CORS(app)

# ── Rutas HTML ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

# ── API JSON ───────────────────────────────────────────────────────
@app.route("/api/ranking")
def api_ranking():
    min_trades = int(request.args.get("min_trades", 3))
    limit      = int(request.args.get("limit", 100))
    data = get_ranking(min_trades=min_trades, limit=limit)
    return jsonify(data)

@app.route("/api/trader/<wallet>")
def api_trader(wallet):
    trades = get_trader_trades(wallet)
    if not trades:
        return jsonify({"error": "Trader no encontrado"}), 404
    total      = len(trades)
    buys       = [t for t in trades if t["side"] == "BUY"]
    longshots  = [t for t in buys if t["price"] < 0.3]
    avg_price  = round(sum(t["price"] for t in buys) / len(buys), 3) if buys else 0
    insider_score = round(len(longshots) / total * 100, 1) if total else 0
    return jsonify({
        "wallet":        wallet,
        "total_trades":  total,
        "buys":          len(buys),
        "avg_price":     avg_price,
        "longshots":     len(longshots),
        "insider_score": insider_score,
        "trades":        trades
    })

@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    return jsonify(get_watchlist())

@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    body   = request.get_json()
    wallet = body.get("wallet", "").strip()
    alias  = body.get("alias", "").strip()
    if not wallet:
        return jsonify({"error": "wallet requerida"}), 400
    add_to_watchlist(wallet, alias)
    return jsonify({"ok": True})

@app.route("/api/watchlist/<wallet>", methods=["DELETE"])
def api_watchlist_delete(wallet):
    remove_from_watchlist(wallet)
    return jsonify({"ok": True})

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    n = run_scraper(pages=5)
    return jsonify({"ok": True, "new_trades": n})

# ── Scheduler: actualiza cada 5 minutos ───────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(lambda: run_scraper(pages=5), "interval", minutes=5)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ── Arranque ──────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=False, port=5000)