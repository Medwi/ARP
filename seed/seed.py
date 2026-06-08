"""
ARP Platform - seed.py
Initialises the database with an institutional-grade book and market data.
Run automatically on `docker compose up` via the seed service, and on every
local launch via ./EXEC (re-seeding is idempotent: the book is rebuilt cleanly;
demo user tokens are reset to the fixed README values each run).

The portfolio is modelled as a USD 1,000,000 diversified multi-asset mandate
for a boutique discretionary manager / single managed account:

    Equities (single names)   ~ 35.5%   (sector-diversified US large cap)
    Equity ETFs (core/intl)   ~ 20.0%   (US core, developed ex-US, EM)
    Fixed income              ~ 30.0%   (aggregate, IG credit, duration, TIPS, HY)
    Commodities (gold)        ~  7.0%
    Digital assets            ~  2.5%   (small strategic sleeve)
    Cash                      ~  5.0%

No single name exceeds an 8% position limit. Concentration, crypto and
duration are all inside mandate. A small live compliance queue (two trades
pending dual sign-off) is seeded so the risk engine has something to surface.

Market prices come from the provider selected by MARKET_DATA_SOURCE
(mock | yahoo | alphavantage) — see backend/market_data.py.
"""

import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `python seed/seed.py` to import the backend package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import market_data
from backend.book_data import MARKET_TICKERS, POSITIONS, holdings_rows
from backend.config import get_bootstrap_tokens_path, get_db_path, rag_enabled
from backend.demo_tokens import DEMO_USER_SPECS, DEMO_USER_TOKENS
from backend.rbac import hash_token, looks_like_stored_digest
from backend.risk_model import ASSET_CLASS_VOL, TARGET_AUM, asset_class_vol
SQL_PATH = Path(__file__).parent.parent / "db" / "schema.sql"

# Deterministic seed so the demo book is reproducible across machines.
random.seed(8675309)

# ── Helpers ───────────────────────────────────────────────────────────────────

def daily_move(asset_class: str) -> float:
    """Plausible single-day percentage move scaled by asset-class volatility."""
    ann_vol = asset_class_vol(asset_class)
    daily_sigma = ann_vol / (252 ** 0.5) * 100.0
    return round(random.gauss(0.0, daily_sigma), 2)


def jitter(base: float, spread: float = 0.015) -> float:
    """Return base price with a small snapshot jitter (default +/-1.5%)."""
    return round(base * random.uniform(1 - spread, 1 + spread), 2)


def ts(days_ago: int = 0, hours: int = 0, minutes: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours, minutes=minutes)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

# ── Schema migration ──────────────────────────────────────────────────────────

def migrate_users_table_for_admin(cur: sqlite3.Cursor) -> None:
    """Upgrade legacy users tables that predate the admin role."""
    row = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not row or "'admin'" in row[0]:
        return
    cur.executescript("""
        CREATE TABLE users_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    NOT NULL UNIQUE,
            role        TEXT    NOT NULL CHECK(role IN ('analyst','risk','manager','intern','admin')),
            token       TEXT    NOT NULL UNIQUE,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO users_new (id, email, role, token, created_at)
            SELECT id, email, role, token, created_at FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
    """)
    print("  - users table migrated (admin role enabled)")

# ── Seed functions ────────────────────────────────────────────────────────────

def _write_bootstrap_tokens(entries: list[tuple[str, str, str]]) -> None:
    """Persist plain tokens once to a gitignored file — never stdout or container logs."""
    if not entries:
        return
    path = get_bootstrap_tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ARP bootstrap bearer tokens — distribute securely, then restrict file permissions.",
        f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "# Format: email<TAB>role<TAB>token",
        "",
    ]
    for email, role, plain in entries:
        lines.append(f"{email}\t{role}\t{plain}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def migrate_plain_tokens(cur: sqlite3.Cursor) -> list[tuple[str, str, str]]:
    """Upgrade legacy plaintext tokens in users.token to SHA-256 digests."""
    migrated: list[tuple[str, str, str]] = []
    for email, role, stored in cur.execute(
        "SELECT email, role, token FROM users ORDER BY email"
    ):
        if looks_like_stored_digest(stored):
            continue
        plain = stored
        cur.execute(
            "UPDATE users SET token = ? WHERE email = ?",
            (hash_token(plain), email),
        )
        migrated.append((email, role, plain))
    if migrated:
        print(f"  - migrated {len(migrated)} legacy plain token(s) to SHA-256 digests")
    return migrated


def ensure_demo_users(cur: sqlite3.Cursor) -> list[tuple[str, str, str]]:
    """Ensure all demo users exist with fixed README bearer tokens (SHA-256 digests in DB)."""
    bootstrap: list[tuple[str, str, str]] = []
    created = 0
    updated = 0
    for email, role in DEMO_USER_SPECS:
        plain = DEMO_USER_TOKENS[email]
        digest = hash_token(plain)
        row = cur.execute(
            "SELECT token FROM users WHERE email = ?", (email,)
        ).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO users (email, role, token) VALUES (?, ?, ?)",
                (email, role, digest),
            )
            created += 1
        elif row[0] != digest:
            cur.execute(
                "UPDATE users SET token = ? WHERE email = ?",
                (digest, email),
            )
            updated += 1
        bootstrap.append((email, role, plain))
    if created:
        print(f"  - {created} demo user(s) created (README tokens)")
    if updated:
        print(f"  - {updated} demo user token(s) reset to README values")
    return bootstrap


def reset_book_tables(cur: sqlite3.Cursor) -> None:
    """
    Make re-seeding idempotent. Clears market/portfolio tables so a repeated
    launch rebuilds a clean book instead of duplicating rows (which previously
    inflated AUM). Demo user rows are kept; tokens are reset to README values each seed.
    """
    for table in ("market_prices", "portfolio_holdings", "trades", "risk_rules"):
        cur.execute(f"DELETE FROM {table}")


def seed_market_prices(cur: sqlite3.Cursor) -> tuple[dict, str]:
    """Seed the latest market snapshot. Returns ({ticker: price}, source_label)."""
    base_lookup = {p[0]: p[4] for p in POSITIONS}
    class_lookup = {p[0]: p[2] for p in POSITIONS}

    source = market_data.configured_source()
    rows = market_data.fetch_prices(
        MARKET_TICKERS, base_lookup, class_lookup, ASSET_CLASS_VOL, source=source
    )
    prices = {r[0]: r[1] for r in rows}

    cur.executemany(
        "INSERT INTO market_prices (ticker, price, change_pct, volume, source) VALUES (?,?,?,?,?)",
        rows,
    )
    return prices, source


def seed_holdings(cur: sqlite3.Cursor, prices: dict):
    """Seed holdings from target weights against TARGET_AUM."""
    rows = holdings_rows(prices)

    cur.executemany(
        """INSERT INTO portfolio_holdings
           (ticker,name,asset_class,quantity,avg_cost,current_price,market_value,weight_pct)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    total_mv = sum(r[6] for r in rows)
    print(f"  - {len(rows)} holdings seeded (AUM USD {total_mv:,.0f})")


def seed_trades(cur: sqlite3.Cursor, prices: dict):
    """
    Seed a representative institutional blotter over the last ~10 sessions.
    Most trades are executed and low-risk. Two sit in the compliance queue:
    a large block above the single-trade notional limit, and a single-name
    reduction carrying an elevated model risk score.
    """
    PM_A, PM_B, PM_C = "a.mansour", "l.whitfield", "r.okafor"

    def px(ticker: str) -> float:
        return prices.get(ticker, next(p[4] for p in POSITIONS if p[0] == ticker))

    # Trade sizes are scaled to the USD 1m book: routine orders run ~1-3% of AUM
    # (USD 8k-30k). One large block breaches the USD 50k single-trade limit and
    # one single-name reduction carries an elevated model risk score.
    # (ticker, direction, quantity, trader, status, risk_score, notes, days_ago, hours)
    blotter = [
        ("SPY",   "BUY",   48,  PM_A, "EXECUTED",  0.08, None, 9, 2),
        ("AGG",   "BUY",  200,  PM_B, "EXECUTED",  0.05, None, 9, 1),
        ("MSFT",  "BUY",   30,  PM_A, "EXECUTED",  0.12, None, 8, 5),
        ("VEA",   "BUY",  300,  PM_C, "EXECUTED",  0.07, None, 8, 3),
        ("JPM",   "BUY",   60,  PM_A, "EXECUTED",  0.14, None, 7, 6),
        ("GLD",   "BUY",   50,  PM_B, "EXECUTED",  0.10, None, 7, 2),
        ("AAPL",  "SELL",  80,  PM_A, "EXECUTED",  0.18, "Trim into strength", 6, 4),
        ("LQD",   "BUY",  150,  PM_B, "EXECUTED",  0.06, None, 6, 1),
        ("NVDA",  "BUY",   15,  PM_C, "EXECUTED",  0.31, "Add on pullback", 5, 7),
        ("TLT",   "SELL", 100,  PM_B, "EXECUTED",  0.22, "Reduce duration", 5, 3),
        ("VWO",   "BUY",  250,  PM_C, "EXECUTED",  0.09, None, 4, 5),
        ("HD",    "BUY",   25,  PM_A, "EXECUTED",  0.11, None, 4, 2),
        ("XOM",   "SELL", 120,  PM_A, "EXECUTED",  0.16, None, 3, 6),
        ("BTC-USD","BUY", 0.15, PM_C, "PENDING",   0.46, "Strategic sleeve top-up; awaiting IC note", 2, 4),
        ("HYG",   "BUY",  120,  PM_B, "PENDING",   0.19, None, 2, 1),
        ("MSFT",  "BUY",  200,  PM_A, "FLAGGED",   0.62,
         "Block above single-trade notional limit; dual sign-off pending", 1, 5),
        ("NVDA",  "SELL",  30,  PM_C, "FLAGGED",   0.86,
         "Elevated model risk score; concentrated single-name reduction under review", 1, 2),
        ("V",     "BUY",   40,  PM_A, "EXECUTED",  0.13, None, 1, 1),
        ("PG",    "BUY",   50,  PM_B, "CANCELLED", 0.07, "Cancelled pre-trade; replaced by ETF order", 0, 7),
        ("UNH",   "BUY",   18,  PM_C, "EXECUTED",  0.20, None, 0, 5),
        ("TIP",   "BUY",   90,  PM_B, "EXECUTED",  0.05, None, 0, 3),
        ("GOOGL", "BUY",   60,  PM_A, "EXECUTED",  0.15, None, 0, 1),
    ]

    rows = []
    for ticker, direction, qty, trader, status, risk, notes, days_ago, hours in blotter:
        price = jitter(px(ticker), spread=0.01)
        notional = round(qty * price, 2)
        rows.append((ticker, direction, float(qty), price, notional,
                     trader, status, risk, notes, ts(days_ago, hours)))

    cur.executemany(
        """INSERT INTO trades
           (ticker,direction,quantity,price,notional,trader,status,risk_score,notes,traded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r[6]] = by_status.get(r[6], 0) + 1
    summary = "  ".join(f"{s}:{n}" for s, n in sorted(by_status.items()))
    print(f"  - {len(rows)} trades seeded ({summary})")


def seed_risk_rules(cur: sqlite3.Cursor):
    from backend.risk_constants import rules_as_seed_rows

    rules = rules_as_seed_rows()
    cur.executemany(
        """INSERT OR IGNORE INTO risk_rules
           (rule_name,description,threshold,metric,severity)
           VALUES (?,?,?,?,?)""",
        rules,
    )
    print(f"  - {len(rules)} risk rules seeded")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    db_path = get_db_path()
    print("\nSeeding ARP database ...")
    print(f"  - mandate AUM target: USD {TARGET_AUM:,.0f}")
    print(f"  - market data source: {market_data.configured_source()}")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript(SQL_PATH.read_text())
    migrate_users_table_for_admin(cur)
    reset_book_tables(cur)
    migrate_plain_tokens(cur)
    bootstrap = ensure_demo_users(cur)
    _write_bootstrap_tokens(bootstrap)
    prices, source = seed_market_prices(cur)
    seed_holdings(cur, prices)
    seed_trades(cur, prices)
    seed_risk_rules(cur)
    con.commit()

    bootstrap_path = get_bootstrap_tokens_path()
    print(
        f"\n  Auth: demo tokens match README tester table "
        f"(also in {bootstrap_path}; `make bootstrap-tokens`)."
    )
    con.close()

    if rag_enabled():
        try:
            from seed.index_knowledge import main as index_knowledge
            index_knowledge()
        except Exception as exc:
            print(f"  - RAG indexing skipped ({exc})")

    print(f"\nDatabase ready (prices: {source}).\n")

if __name__ == "__main__":
    main()
