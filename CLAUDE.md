# Polymarket Tracker — Referencia del proyecto

## Qué hace esta app

Rastrea a los mejores traders de Polymarket (mercado de predicciones), calcula un
**Insider Score** basado en su historial de aciertos en apuestas de baja probabilidad
(longshots), y envía alertas por Telegram cuando hacen nuevas apuestas relevantes.

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3 + Flask |
| Base de datos | SQLite (`polymarket.db`) — no se sube a git |
| Scraper | `requests` + `ThreadPoolExecutor` (5 workers) |
| Scheduler | APScheduler (jobs cada 5 min dentro del proceso Flask) |
| Frontend | HTML/CSS/JS vanilla en `templates/index.html` |
| Alertas | Telegram Bot API |
| Hosting | Render (ver sección de despliegue) |

---

## Estructura de archivos

```
polymarket-app/
├── app.py          # Flask app + scheduler (scraper + alertas cada 5 min)
├── scraper.py      # Descarga traders del leaderboard y sus trades
├── database.py     # SQLite: schema, upserts, get_ranking()
├── alerts.py       # Monitor watchlist → alertas Telegram
├── templates/
│   └── index.html  # UI single-page (ranking + watchlist)
├── .env            # Credenciales locales — NO subir a git
├── .env.example    # Plantilla de variables de entorno
├── .gitignore      # Excluye .env, polymarket.db, __pycache__
└── requirements.txt
```

---

## Base de datos

### Tabla `traders`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| wallet | TEXT PK | Dirección proxy de Polymarket |
| alias | TEXT | Nombre público del trader |
| total_pnl | REAL | PnL total en USDC (del leaderboard) |
| total_invested | REAL | Volumen total operado |
| updated_at | TEXT | Última actualización |

### Tabla `trades`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | TEXT PK | MD5 de wallet+market+side+size+price+ts |
| wallet | TEXT | Wallet del trader |
| market_id | TEXT | conditionId del mercado |
| question | TEXT | Texto de la pregunta |
| side | TEXT | BUY o SELL |
| size | REAL | Tamaño en USDC |
| price | REAL | Precio implícito (0-1) |
| outcome | TEXT | "Yes" o "No" |
| outcome_won | INT | 1=ganó, 0=perdió, -1=sin resolver |
| is_relevant | INT | 1 si el mercado es política/geo/economía |

### Tabla `watchlist`
Wallets monitorizadas para alertas Telegram. Gestionada desde la UI.

---

## APIs de Polymarket utilizadas

| Endpoint | Uso |
|----------|-----|
| `data-api.polymarket.com/v1/leaderboard` | Top traders por categoría y PnL |
| `data-api.polymarket.com/activity?user=` | Historial de trades de un trader |
| `clob.polymarket.com/markets/{conditionId}` | Resultado de un mercado (campo `tokens[].winner`) |

### Categorías válidas del leaderboard
`politics`, `overall`, `tech`, `economics`, `sports`, `crypto`, `culture`, `finance`

> ⚠️ `geopolitics` y `economy` **no son categorías válidas** — devuelven 0 resultados.
> Usar `overall` como sustituto de geopolítica.

### Por qué NO usar gamma-api para resultados de mercados
`gamma-api.polymarket.com/markets/{conditionId}` devuelve 422.
`gamma-api.polymarket.com/markets?conditionId=` devuelve resultados aleatorios y
`outcomePrices` es `["0","0"]` en mercados resueltos.
**Usar siempre `clob.polymarket.com/markets/{conditionId}`** → campo `tokens[i].winner = true`.

---

## Insider Score

### Fórmula
```
insider_score = (longshots_ganados / longshots_resueltos) × total_pnl
```

### Definición de longshot
Posición BUY con `price < 0.30` (probabilidad implícita < 30 %).

### Reglas
- Solo aparecen traders con **≥ 65 % de acierto en longshots**
- Sin mínimo de apuestas (1/1 = 100 % aparece)
- Se cuentan **posiciones únicas** `(market_id, outcome)`, no transacciones individuales
  — un trader que compra el mismo mercado 50 veces cuenta como 1 predicción

### Por qué posiciones únicas y no transacciones
Theo4 tiene 7 posiciones reales (≈14 trades incluyendo ventas, coincide con Polymarket)
pero 377 transacciones de compra. Sin deduplicación aparecería con 83 "longshots"
en lugar de 2. La query usa un CTE que agrupa por `(wallet, market_id, outcome)` antes
de contar.

---

## Cálculo del ranking (`database.py → get_ranking()`)

```sql
WITH positions AS (
    -- Deduplica: cada (wallet, market_id, outcome) BUY = 1 posición
    SELECT wallet, market_id, outcome,
           MIN(price) AS price, MAX(outcome_won) AS outcome_won, ...
    FROM trades WHERE side = 'BUY'
    GROUP BY wallet, market_id, outcome
)
SELECT ...
FROM traders t LEFT JOIN positions p ON t.wallet = p.wallet
GROUP BY t.wallet
HAVING longshot_win_rate >= 65
ORDER BY insider_score DESC
```

---

## Scraper (`scraper.py`)

### Flujo
1. `fetch_all_leaderboards()` — descarga top 100 de cada categoría en paralelo, merge por wallet conservando mayor PnL → ~300 traders únicos
2. `_fetch_trader_activity()` — descarga actividad de cada trader en paralelo (5 workers)
3. `parse_activity()` — parsea cada trade, consulta resultado del mercado vía CLOB API
4. `upsert_trades()` — guarda en SQLite con `INSERT OR IGNORE`

### Rendimiento
- Antes (seed + PnL scan de 2695 wallets): ~30 min
- Ahora (leaderboard directo): ~6 min

---

## Alertas Telegram (`alerts.py`)

### Condiciones para disparar alerta
- Trader en watchlist
- Side = BUY
- `size > 100 USDC`
- `price < 0.30` (longshot)
- Mercado relevante (`is_relevant_market()`)
- Trade de los últimos 10 minutos

### Variables de entorno requeridas
```
TELEGRAM_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>
```

Localmente se leen de `.env` vía `python-dotenv`.
En Render se configuran en **Dashboard → polymarket-app → Environment**.

### Deduplicación
`_seen_trade_ids` (set en memoria) evita re-enviar la misma alerta entre
ejecuciones del job de 5 minutos. Se reinicia al reiniciar el servidor.

---

## Variables de entorno

| Variable | Descripción | Dónde configurar |
|----------|-------------|-----------------|
| `TELEGRAM_TOKEN` | Token del bot de Telegram | `.env` local / Render Dashboard |
| `TELEGRAM_CHAT_ID` | ID del chat donde llegan las alertas | `.env` local / Render Dashboard |

### Configurar en Render
1. Ir a [dashboard.render.com](https://dashboard.render.com)
2. Seleccionar el servicio `polymarket-app`
3. **Environment** → **Add Environment Variable**
4. Añadir `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` con sus valores
5. El servicio se reinicia automáticamente

---

## Despliegue en Render

El proyecto se despliega como **Web Service** en Render conectado al repo
`adnara12/polymarket-app` en GitHub.

### Archivos necesarios para Render
- `requirements.txt` — dependencias Python
- `Procfile` o Start Command: `python app.py`

### Notas
- `polymarket.db` **no está en git** — Render crea una DB vacía en cada deploy.
  Si se necesita persistencia real, usar un volumen de Render o migrar a PostgreSQL.
- El scheduler (APScheduler) corre dentro del mismo proceso Flask —
  no hace falta un worker separado.

---

## Comandos útiles

```bash
# Ejecutar localmente
python app.py

# Ejecutar solo el scraper
python scraper.py

# Probar alertas Telegram
python alerts.py

# Ver ranking en consola
python -c "from database import get_ranking; [print(r['alias'], r['insider_score']) for r in get_ranking(limit=10)]"
```

---

## Historial de decisiones técnicas

| Decisión | Motivo |
|----------|--------|
| Leaderboard API en lugar de seed+scan | El seed escaneaba 2695 wallets (~30 min); el leaderboard da los mejores directamente (~6 min) |
| CLOB API para resultados | gamma-api devuelve 422 en path param y `outcomePrices=["0","0"]` en resueltos |
| Posiciones únicas en lugar de transacciones | Theo4: 377 transacciones → 7 posiciones; sin deduplicar el score era 12x inflado |
| `MIN(price)` como precio representativo de posición | Si el trader compró alguna vez a precio longshot, la posición cuenta como longshot |
| 65% threshold para longshot win rate | Filtra traders con suerte aleatoria; mantiene insider information signal |
