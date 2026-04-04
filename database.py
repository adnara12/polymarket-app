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
        CREATE TABLE IF NOT EXISTS traders (
            wallet          TEXT PRIMARY KEY,
            alias           TEXT,
            total_pnl       REAL DEFAULT 0,
            total_invested  REAL DEFAULT 0,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS trades (
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
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            wallet          TEXT PRIMARY KEY,
            alias           TEXT,
            added_at        TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_trades_wallet   ON trades(wallet);
        CREATE INDEX IF NOT EXISTS idx_trades_relevant ON trades(is_relevant);
        CREATE INDEX IF NOT EXISTS idx_traders_pnl     ON traders(total_pnl);
    """)
    conn.commit()
    conn.close()

def upsert_trader(wallet, alias, total_pnl, total_invested):
    conn = get_conn()
    conn.execute("""
        INSERT INTO traders (wallet, alias, total_pnl, total_invested, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(wallet) DO UPDATE SET
            alias          = excluded.alias,
            total_pnl      = excluded.total_pnl,
            total_invested = excluded.total_invested,
            updated_at     = CURRENT_TIMESTAMP
    """, (wallet, alias, total_pnl, total_invested))
    conn.commit()
    conn.close()

def upsert_trades(trades):
    conn = get_conn()
    conn.executemany("""
        INSERT OR IGNORE INTO trades
            (id, wallet, market_id, question, side, size, price,
             timestamp, outcome, outcome_won, pnl, is_relevant)
        VALUES
            (:id, :wallet, :market_id, :question, :side, :size, :price,
             :timestamp, :outcome, :outcome_won, :pnl, :is_relevant)
    """, trades)
    conn.commit()
    conn.close()

def get_ranking(min_trades=0, limit=500):
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            t.wallet,
            t.alias,
            t.total_pnl,
            t.total_invested,
            COUNT(tr.id)                                         AS total,
            SUM(CASE WHEN tr.is_relevant = 1 THEN 1 ELSE 0 END) AS relevant_trades,
            SUM(CASE WHEN tr.outcome_won = 1 THEN 1 ELSE 0 END) AS trades_ganados,
            SUM(CASE WHEN tr.outcome_won != -1 THEN 1 ELSE 0 END) AS trades_resueltos,
            ROUND(AVG(tr.price), 3)                              AS avg_price,
            MAX(tr.timestamp)                                    AS last_seen,
            MIN(tr.timestamp)                                    AS first_seen,

            -- Longshots: BUY with price < 0.30 that are resolved
            SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won != -1
                THEN 1 ELSE 0 END)                               AS longshots_resueltos,
            SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won = 1
                THEN 1 ELSE 0 END)                               AS longshots_ganados,

            -- % acierto en longshots (0 if no resolved longshots)
            ROUND(
                CASE WHEN SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won != -1
                                   THEN 1 ELSE 0 END) > 0
                THEN 100.0
                     * SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won = 1
                                THEN 1 ELSE 0 END)
                     / SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won != -1
                                THEN 1 ELSE 0 END)
                ELSE 0 END
            , 1)                                                 AS longshot_win_rate,

            -- Insider Score = (win_rate / 100) * total_pnl
            ROUND(
                CASE WHEN SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won != -1
                                   THEN 1 ELSE 0 END) > 0
                THEN (1.0
                     * SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won = 1
                                THEN 1 ELSE 0 END)
                     / SUM(CASE WHEN tr.side='BUY' AND tr.price < 0.30 AND tr.outcome_won != -1
                                THEN 1 ELSE 0 END))
                     * t.total_pnl
                ELSE 0 END
            , 0)                                                 AS insider_score

        FROM traders t
        LEFT JOIN trades tr ON t.wallet = tr.wallet
        GROUP BY t.wallet
        HAVING longshot_win_rate >= 65
        ORDER BY insider_score DESC, total_pnl DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_trader_trades(wallet, limit=200):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM trades
        WHERE wallet = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (wallet, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_trader_stats(wallet):
    conn = get_conn()
    trader = conn.execute(
        "SELECT * FROM traders WHERE wallet = ?", (wallet,)
    ).fetchone()
    conn.close()
    return dict(trader) if trader else None

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

def update_market_outcomes(outcomes):
    conn = get_conn()
    c = conn.cursor()
    for o in outcomes:
        c.execute("""
            UPDATE trades SET outcome_won = ?
            WHERE market_id = ? AND side = 'BUY' AND outcome = 'Yes'
        """, (o['won'], o['market_id']))
        c.execute("""
            UPDATE trades SET outcome_won = ?
            WHERE market_id = ? AND side = 'BUY' AND outcome = 'No'
        """, (1 - o['won'], o['market_id']))
    conn.commit()
    conn.close()