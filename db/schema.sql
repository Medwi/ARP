-- ARP Global Capital | Local AI Investment Intelligence Platform
-- Canonical SQLite schema for data/arp.db
--
-- Core tables (always created on seed):
--   users, portfolio_holdings, trades, market_prices, risk_rules,
--   audit_logs, agent_memory, trade_ideas
--
-- Optional RAG tables (created when VECTOR_DB=sqlite; also ensured at runtime by backend/rag.py):
--   knowledge_vectors, knowledge_meta
--
-- Mandate limits are seeded from backend/risk_constants.py into risk_rules.
-- Position master for the book lives in backend/book_data.py (shared with seed + tests).

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Users & auth ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    NOT NULL UNIQUE,
    role        TEXT    NOT NULL CHECK(role IN ('analyst','risk','manager','intern','admin')),
    token       TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Portfolio holdings (live book snapshot) ───────────────────────────────────

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    asset_class   TEXT    NOT NULL,
    quantity      REAL    NOT NULL,
    avg_cost      REAL    NOT NULL,
    current_price REAL    NOT NULL,
    market_value  REAL    NOT NULL,
    weight_pct    REAL    NOT NULL,
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Trade blotter ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK(direction IN ('BUY','SELL')),
    quantity    REAL    NOT NULL,
    price       REAL    NOT NULL,
    notional    REAL    NOT NULL,
    trader      TEXT    NOT NULL,
    status      TEXT    NOT NULL CHECK(status IN ('EXECUTED','PENDING','FLAGGED','CANCELLED')),
    risk_score  REAL    NOT NULL DEFAULT 0.0,
    notes       TEXT,
    traded_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Market prices (latest snapshot per ticker) ────────────────────────────────

CREATE TABLE IF NOT EXISTS market_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT    NOT NULL,
    price       REAL    NOT NULL,
    change_pct  REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'mock',
    fetched_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Mandate risk rules (thresholds enforced by tools + knowledge graph) ─────

CREATE TABLE IF NOT EXISTS risk_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name   TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL,
    threshold   REAL    NOT NULL,
    metric      TEXT    NOT NULL,
    severity    TEXT    NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    active      INTEGER NOT NULL DEFAULT 1
);

-- ── Audit logs (append-only, SHA-256 hash-chained) ──────────────────────────
-- Agent /ask requests log once via run_agent(); direct REST calls log at tool level.

CREATE TABLE IF NOT EXISTS audit_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email   TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    question     TEXT,
    tool_called  TEXT,
    allowed      INTEGER NOT NULL CHECK(allowed IN (0,1)),
    deny_reason  TEXT,
    timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
    record_hash  TEXT                   -- SHA-256(prev_hash || fields)
);

-- ── Agent conversational memory (per-user short-term recall) ────────────────

CREATE TABLE IF NOT EXISTS agent_memory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email   TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    agent        TEXT    NOT NULL,
    question     TEXT    NOT NULL,
    answer       TEXT    NOT NULL,
    tool_called  TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Shadow book: trade ideas (expansion module; table reserved in core schema) ─

CREATE TABLE IF NOT EXISTS trade_ideas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    direction    TEXT    NOT NULL CHECK(direction IN ('BUY','SELL','SHORT')),
    thesis       TEXT    NOT NULL,
    target_price REAL,
    stop_loss    REAL,
    conviction   INTEGER NOT NULL CHECK(conviction BETWEEN 1 AND 5),
    submitted_by TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'OPEN'
                         CHECK(status IN ('OPEN','EXECUTED','REJECTED','EXPIRED')),
    outcome_pnl  REAL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT
);

-- ── RAG vector store (optional; VECTOR_DB=sqlite) ─────────────────────────────

CREATE TABLE IF NOT EXISTS knowledge_vectors (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL,
    roles      TEXT NOT NULL,
    chunk      INTEGER NOT NULL,
    document   TEXT NOT NULL,
    embedding  TEXT NOT NULL,
    embed_mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ── Indexes ─────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_audit_user       ON audit_logs(user_email);
CREATE INDEX IF NOT EXISTS idx_audit_time       ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_status    ON trades(status);
CREATE INDEX IF NOT EXISTS idx_prices_ticker    ON market_prices(ticker);
CREATE INDEX IF NOT EXISTS idx_memory_user      ON agent_memory(user_email);
CREATE INDEX IF NOT EXISTS idx_memory_time      ON agent_memory(created_at);
CREATE INDEX IF NOT EXISTS idx_ideas_status     ON trade_ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_ticker     ON trade_ideas(ticker);
CREATE INDEX IF NOT EXISTS idx_ideas_submitter  ON trade_ideas(submitted_by);
CREATE INDEX IF NOT EXISTS idx_kvec_source      ON knowledge_vectors(source);
