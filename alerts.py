import sys
import requests
import hashlib
from datetime import datetime, timezone, timedelta
from scraper import fetch_user_activity, is_relevant_market
from database import get_watchlist

TELEGRAM_TOKEN   = "8619776671:AAHCHbUETDVtnAg-HbAV-E5lJvSbvQ-CZZE"
TELEGRAM_CHAT_ID = "2001871424"

# Tracks already-alerted trades to avoid duplicates within the same session
_seen_trade_ids: set = set()

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     message,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[alerts] Telegram error: {e}")
        return False

def check_watchlist_alerts():
    """
    Checks recent activity for every wallet in the watchlist.
    Sends a Telegram alert for each BUY that meets all three conditions:
      - size  > 100 USDC
      - price < 0.30  (implied probability < 30 %)
      - market is relevant (politics / geopolitics / economics)
    """
    watchlist = get_watchlist()
    if not watchlist:
        return

    # Only look at trades from the last 10 minutes (2× the polling interval)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
    new_alerts = 0

    for entry in watchlist:
        wallet = entry["wallet"]
        alias  = entry["alias"] or wallet[:14]

        activity = fetch_user_activity(wallet, limit=50)
        if not activity:
            continue

        for act in activity:
            ts_raw = act.get("timestamp")
            try:
                ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            except Exception:
                continue

            # Activity comes sorted newest-first; stop once we pass the cutoff
            if ts < cutoff:
                break

            side = act.get("type", "").upper() or act.get("side", "").upper()
            if side != "BUY":
                continue

            size         = float(act.get("usdcSize") or act.get("size") or 0)
            price        = float(act.get("price") or 0)
            question     = act.get("title") or act.get("question") or ""
            outcome      = act.get("outcome") or ""
            condition_id = act.get("conditionId") or ""

            # ── Filters ──────────────────────────────────────────────
            if size <= 100:
                continue
            if price >= 0.30:
                continue
            if not is_relevant_market(question):
                continue

            # ── Deduplication ─────────────────────────────────────────
            trade_hash = hashlib.md5(
                f"{wallet}{condition_id}{side}{size}{price}{ts_raw}".encode()
            ).hexdigest()
            if trade_hash in _seen_trade_ids:
                continue
            _seen_trade_ids.add(trade_hash)

            # ── Build & send alert ────────────────────────────────────
            prob_pct = round(price * 100, 1)
            msg = (
                f"<b>ALERTA POLYMARKET</b>\n\n"
                f"Trader: <b>{alias}</b>\n"
                f"Mercado: {question}\n"
                f"Apuesta: <b>{outcome}</b>\n"
                f"Probabilidad: <b>{prob_pct}%</b>\n"
                f"Tamano: <b>{size:,.0f} USDC</b>\n"
                f"Perfil: https://polymarket.com/profile/{wallet}"
            )
            if send_telegram(msg):
                new_alerts += 1
                print(
                    f"[alerts] Enviada: {alias} | {question[:45]} | "
                    f"{prob_pct}% | {size:,.0f} USDC"
                )

    if new_alerts:
        print(f"[alerts] {new_alerts} alerta(s) enviada(s).")

if __name__ == "__main__":
    # ── Quick test: send a test message and run one check ─────────────
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("[alerts] Enviando mensaje de prueba a Telegram...")
    ok = send_telegram(
        "<b>ALERTA TEST</b>\n\n"
        "Sistema de alertas Polymarket activo.\n"
        "Monitorizando wallets en watchlist cada 5 min."
    )
    print("[alerts] Telegram OK" if ok else "[alerts] Telegram FALLO")

    print("[alerts] Ejecutando revision de watchlist...")
    check_watchlist_alerts()
    print("[alerts] Listo.")
