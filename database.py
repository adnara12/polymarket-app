import sqlite3
import os
from decimal import Decimal

# ── Driver selection ────────────────────────────────────────────────────────
# If DATABASE_URL is set (Render production) → PostgreSQL via psycopg2.
# Otherwise → SQLite for local development.

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Render uses "postgres://" but psycopg2 requires "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DB_TYPE = "postgres" if DATABASE_URL else "sqlite"
DB_PATH = os.path.join(os.path.dirname(__file__), "polymarket.db")
PH = "%s" if DB_TYPE == "postgres" else "?"   # positional placeholder

if DB_TYPE == "postgres":
    import psycopg2
    import psycopg2.extras


def _row(row):
    """Convert a DB row to a plain dict, casting Decimal → float."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in dict(row).items()}


def get_conn():
    if DB_TYPE == "postgres":
        return psycopg2.connect(DATABASE_URL,
                                cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ──────────────────────────────────────────────────────────────────

def init_db():
    conn = get_conn()
    c = conn.cursor()
    for sql in [
        """CREATE TABLE IF NOT EXISTS traders (
            wallet          TEXT PRIMARY KEY,
            alias           TEXT,
            total_pnl       REAL DEFAULT 0,
            total_invested  REAL DEFAULT 0,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS trades (
            id              TEXT PRIMARY KEY,
            wallet          TEXT NOT NULL,
            market_id       TEXT,
            question        TEXT,
            side            TEXT,
            size            REAL,
            price           REAL,
            timestamp       TEXT,
            outcome         TEXT,
            outcome_won     INTEGER DEFAULT -1,
            pnl             REAL DEFAULT 0,
            is_relevant     INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS watchlist (
            wallet          TEXT PRIMARY KEY,
            alias           TEXT,
            added_at        TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_trades_wallet   ON trades(wallet)",
        "CREATE INDEX IF NOT EXISTS idx_trades_relevant ON trades(is_relevant)",
        "CREATE INDEX IF NOT EXISTS idx_traders_pnl     ON traders(total_pnl)",
    ]:
        c.execute(sql)
    conn.commit()
    conn.close()


# ── Writes ──────────────────────────────────────────────────────────────────

def upsert_trader(wallet, alias, total_pnl, total_invested):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""
        INSERT INTO traders (wallet, alias, total_pnl, total_invested, updated_at)
        VALUES ({PH}, {PH}, {PH}, {PH}, CURRENT_TIMESTAMP)
        ON CONFLICT(wallet) DO UPDATE SET
            alias          = EXCLUDED.alias,
            total_pnl      = EXCLUDED.total_pnl,
            total_invested = EXCLUDED.total_invested,
            updated_at     = CURRENT_TIMESTAMP
    """, (wallet, alias, total_pnl, total_invested))
    conn.commit()
    conn.close()


def upsert_trades(trades):
    if not trades:
        return
    conn = get_conn()
    c = conn.cursor()
    if DB_TYPE == "postgres":
        c.executemany("""
            INSERT INTO trades
                (id, wallet, market_id, question, side, size, price,
                 timestamp, outcome, outcome_won, pnl, is_relevant)
            VALUES
                (%(id)s, %(wallet)s, %(market_id)s, %(question)s, %(side)s,
                 %(size)s, %(price)s, %(timestamp)s, %(outcome)s,
                 %(outcome_won)s, %(pnl)s, %(is_relevant)s)
            ON CONFLICT (id) DO NOTHING
        """, trades)
    else:
        c.executemany("""
            INSERT OR IGNORE INTO trades
                (id, wallet, market_id, question, side, size, price,
                 timestamp, outcome, outcome_won, pnl, is_relevant)
            VALUES
                (:id, :wallet, :market_id, :question, :side, :size, :price,
                 :timestamp, :outcome, :outcome_won, :pnl, :is_relevant)
        """, trades)
    conn.commit()
    conn.close()


def update_market_outcomes(outcomes):
    conn = get_conn()
    c = conn.cursor()
    for o in outcomes:
        c.execute(f"""
            UPDATE trades SET outcome_won = {PH}
            WHERE market_id = {PH} AND side = 'BUY' AND outcome = 'Yes'
        """, (o['won'], o['market_id']))
        c.execute(f"""
            UPDATE trades SET outcome_won = {PH}
            WHERE market_id = {PH} AND side = 'BUY' AND outcome = 'No'
        """, (1 - o['won'], o['market_id']))
    conn.commit()
    conn.close()


# ── Reads ───────────────────────────────────────────────────────────────────

def get_ranking(min_trades=0, limit=500):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""
        WITH positions AS (
            SELECT
                wallet,
                market_id,
                outcome,
                MIN(price)       AS price,
                MAX(outcome_won) AS outcome_won,
                MAX(is_relevant) AS is_relevant,
                MAX(timestamp)   AS last_ts,
                MIN(timestamp)   AS first_ts
            FROM trades
            WHERE side = 'BUY'
            GROUP BY wallet, market_id, outcome
        )
        SELECT
            t.wallet,
            t.alias,
            t.total_pnl,
            t.total_invested,
            COUNT(p.market_id)                                        AS total,
            SUM(CASE WHEN p.is_relevant = 1 THEN 1 ELSE 0 END)       AS relevant_trades,
            SUM(CASE WHEN p.outcome_won = 1 THEN 1 ELSE 0 END)       AS trades_ganados,
            SUM(CASE WHEN p.outcome_won != -1 THEN 1 ELSE 0 END)     AS trades_resueltos,
            ROUND(CAST(AVG(p.price) AS NUMERIC), 3)                   AS avg_price,
            MAX(p.last_ts)                                            AS last_seen,
            MIN(p.first_ts)                                           AS first_seen,

            SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                THEN 1 ELSE 0 END)                                    AS longshots_resueltos,
            SUM(CASE WHEN p.price < 0.30 AND p.outcome_won = 1
                THEN 1 ELSE 0 END)                                    AS longshots_ganados,

            ROUND(CAST(
                CASE WHEN SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                                   THEN 1 ELSE 0 END) > 0
                THEN 100.0
                     * SUM(CASE WHEN p.price < 0.30 AND p.outcome_won = 1
                                THEN 1 ELSE 0 END)
                     / SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                                THEN 1 ELSE 0 END)
                ELSE 0 END
            AS NUMERIC), 1)                                           AS longshot_win_rate,

            ROUND(CAST(
                CASE WHEN SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                                   THEN 1 ELSE 0 END) > 0
                THEN (1.0
                     * SUM(CASE WHEN p.price < 0.30 AND p.outcome_won = 1
                                THEN 1 ELSE 0 END)
                     / SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                                THEN 1 ELSE 0 END))
                     * t.total_pnl
                ELSE 0 END
            AS NUMERIC), 0)                                           AS insider_score

        FROM traders t
        LEFT JOIN positions p ON t.wallet = p.wallet
        GROUP BY t.wallet, t.alias, t.total_pnl, t.total_invested
        HAVING ROUND(CAST(
            CASE WHEN SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                               THEN 1 ELSE 0 END) > 0
            THEN 100.0
                 * SUM(CASE WHEN p.price < 0.30 AND p.outcome_won = 1
                            THEN 1 ELSE 0 END)
                 / SUM(CASE WHEN p.price < 0.30 AND p.outcome_won != -1
                            THEN 1 ELSE 0 END)
            ELSE 0 END
        AS NUMERIC), 1) >= 65
        ORDER BY insider_score DESC, total_pnl DESC
        LIMIT {PH}
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return [_row(r) for r in rows]


def get_trader_trades(wallet, limit=200):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""
        SELECT * FROM trades
        WHERE wallet = {PH}
        ORDER BY timestamp DESC
        LIMIT {PH}
    """, (wallet, limit))
    rows = c.fetchall()
    conn.close()
    return [_row(r) for r in rows]


def get_trader_stats(wallet):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT * FROM traders WHERE wallet = {PH}", (wallet,))
    row = c.fetchone()
    conn.close()
    return _row(row) if row else None


def get_watchlist():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM watchlist ORDER BY added_at DESC")
    rows = c.fetchall()
    conn.close()
    return [_row(r) for r in rows]


def add_to_watchlist(wallet, alias=""):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""
        INSERT INTO watchlist (wallet, alias)
        VALUES ({PH}, {PH})
        ON CONFLICT (wallet) DO UPDATE SET alias = EXCLUDED.alias
    """, (wallet, alias))
    conn.commit()
    conn.close()


def remove_from_watchlist(wallet):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"DELETE FROM watchlist WHERE wallet = {PH}", (wallet,))
    conn.commit()
    conn.close()
