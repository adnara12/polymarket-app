import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "polymarket.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id          TEXT PRIMARY KEY,
            wallet      TEXT NOT NULL,
            market_id   TEXT,
            question    TEXT,
            side        TEXT,
            size        REAL,
            price       REAL,
            timestamp   TEXT,
            outcome     TEXT,
            alias       TEXT,
            outcome_won INTEGER DEFAULT -1
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            wallet      TEXT PRIMARY KEY,
            alias       TEXT,
            added_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet);
        CREATE INDEX IF NOT EXISTS idx_trades_ts     ON trades(timestamp);
    """)
    conn.commit()
    conn.close()

def upsert_trades(trades):
    conn = get_conn()
    c = conn.cursor()
    c.executemany("""
        INSERT OR IGNORE INTO trades
            (id, wallet, market_id, question, side, size, price, timestamp, outcome, alias)
        VALUES
            (:id, :wallet, :market_id, :question, :side, :size, :price, :timestamp, :outcome, :alias)
    """, trades)
    conn.commit()
    conn.close()

def get_ranking(min_trades=3, limit=100):
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute("""
        SELECT
            wallet,
            COUNT(*)                                                AS total,
            SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END)            AS buys,
            ROUND(AVG(price), 3)                                    AS avg_price,
            ROUND(SUM(size), 2)                                     AS total_invested,
            MIN(timestamp)                                          AS first_seen,
            MAX(timestamp)                                          AS last_seen,
            ROUND(AVG(CASE WHEN price < 0.3 THEN 1.0 ELSE 0.0 END) * 100, 1) AS pct_longshot,
            MAX(alias)                                              AS alias,
            GROUP_CONCAT(DISTINCT question)                         AS markets
        FROM trades
        GROUP BY wallet
        HAVING total >= ?
        ORDER BY pct_longshot DESC, total DESC
        LIMIT ?
    """, (min_trades, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_trader_trades(wallet, limit=200):
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute("""
        SELECT * FROM trades
        WHERE wallet = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (wallet, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_watchlist():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_to_watchlist(wallet, alias=""):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO watchlist (wallet, alias) VALUES (?, ?)",
        (wallet, alias)
    )
    conn.commit()
    conn.close()

def remove_from_watchlist(wallet):
    conn = get_conn()
    conn.execute("DELETE FROM watchlist WHERE wallet = ?", (wallet,))
    conn.commit()
    conn.close()