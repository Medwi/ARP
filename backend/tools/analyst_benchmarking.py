"""
ARP Platform – tools/analyst_benchmarking.py
Research Supplier Benchmarking Agent.

Maps to ARP's LATER (3 months+) roadmap priority:
  "Research Supplier Benchmarking Agent: tracking and ranking external
   research analysts based on historical hit rates to optimise commission
   spending."

This tool:
  - Tracks research ideas submitted per analyst/broker
  - Records outcomes (hit/miss/pending) against actual price moves
  - Computes hit rates, average return per idea, conviction-weighted performance
  - Ranks analysts by adjusted alpha generation
  - Flags underperforming relationships for commission review

In production: pulls from the research lake (research_lake.py) and cross-
references with actual trade outcomes from Broadridge. This demo version
seeds realistic data and computes all metrics locally.
"""

from __future__ import annotations
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect


def ensure_benchmarking_tables(db_path: Optional[str] = None) -> None:
    """Create analyst benchmarking tables. Safe to call multiple times."""
    con = connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS analyst_coverage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            analyst_name    TEXT    NOT NULL,
            broker          TEXT    NOT NULL,
            annual_commission REAL  DEFAULT 0,
            coverage_universe TEXT,
            relationship_since TEXT,
            active          INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS analyst_calls (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            analyst_id      INTEGER REFERENCES analyst_coverage(id),
            ticker          TEXT    NOT NULL,
            direction       TEXT    NOT NULL CHECK(direction IN ('BUY','SELL','NEUTRAL')),
            target_price    REAL,
            conviction      INTEGER NOT NULL CHECK(conviction BETWEEN 1 AND 5),
            issued_at       TEXT    NOT NULL,
            resolved_at     TEXT,
            entry_price     REAL,
            exit_price      REAL,
            outcome         TEXT    CHECK(outcome IN ('HIT','MISS','PARTIAL','PENDING')),
            return_pct      REAL,
            notes           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_calls_analyst  ON analyst_calls(analyst_id);
        CREATE INDEX IF NOT EXISTS idx_calls_outcome  ON analyst_calls(outcome);
        CREATE INDEX IF NOT EXISTS idx_calls_ticker   ON analyst_calls(ticker);
    """)
    con.commit()
    con.close()


# ── Tool: get_analyst_rankings ────────────────────────────────────────────────

def get_analyst_rankings(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Rank all tracked analysts by adjusted performance metrics:
    hit rate, average return per call, conviction-weighted alpha.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_analyst_rankings", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_benchmarking_tables(db_path)
    con = connect(db_path)
    try:
        analysts = con.execute(
            "SELECT * FROM analyst_coverage WHERE active=1"
        ).fetchall()

        if not analysts:
            return {
                "allowed": True,
                "rankings": [],
                "message": "No analysts tracked. Use /analyst/seed-samples to add demo data.",
            }

        rankings = []
        for analyst in analysts:
            calls = con.execute(
                "SELECT * FROM analyst_calls WHERE analyst_id = ?",
                (analyst["id"],)
            ).fetchall()

            resolved = [c for c in calls if c["outcome"] != "PENDING"]
            hits      = [c for c in resolved if c["outcome"] == "HIT"]
            misses    = [c for c in resolved if c["outcome"] == "MISS"]
            partial   = [c for c in resolved if c["outcome"] == "PARTIAL"]
            pending   = [c for c in calls if c["outcome"] == "PENDING"]

            hit_rate = round(len(hits) / len(resolved) * 100, 1) if resolved else 0.0

            # Average return on resolved calls
            returns = [c["return_pct"] for c in resolved if c["return_pct"] is not None]
            avg_return = round(sum(returns) / len(returns), 2) if returns else 0.0

            # Conviction-weighted hit rate
            total_conv = sum(c["conviction"] for c in resolved) or 1
            weighted_hits = sum(c["conviction"] for c in resolved if c["outcome"] == "HIT")
            conviction_hit_rate = round(weighted_hits / total_conv * 100, 1)

            # Commission efficiency: alpha per $10k commission
            commission = analyst["annual_commission"] or 1
            alpha_per_10k = round(avg_return / (commission / 10000), 2)

            # Value rating (composite)
            value_score = round(
                hit_rate * 0.4 +
                min(max(avg_return, -10), 20) * 2 +
                conviction_hit_rate * 0.3, 1
            )

            rankings.append({
                "analyst":             analyst["analyst_name"],
                "broker":              analyst["broker"],
                "annual_commission":   analyst["annual_commission"],
                "coverage":            analyst["coverage_universe"],
                "total_calls":         len(calls),
                "resolved":            len(resolved),
                "pending":             len(pending),
                "hits":                len(hits),
                "misses":              len(misses),
                "partial":             len(partial),
                "hit_rate_pct":        hit_rate,
                "conviction_hit_rate": conviction_hit_rate,
                "avg_return_pct":      avg_return,
                "alpha_per_10k_commission": alpha_per_10k,
                "value_score":         value_score,
                "recommendation": (
                    "INCREASE COMMISSION" if value_score > 60 else
                    "MAINTAIN"            if value_score > 35 else
                    "REDUCE COMMISSION"   if value_score > 15 else
                    "REVIEW RELATIONSHIP"
                ),
            })

        rankings.sort(key=lambda x: x["value_score"], reverse=True)

        return {
            "allowed":       True,
            "rankings":      rankings,
            "analyst_count": len(rankings),
            "total_commission": sum(a.get("annual_commission") or 0
                                    for a in rankings),
        }
    finally:
        con.close()


def seed_sample_analysts(db_path: Optional[str] = None) -> int:
    """Seed realistic analyst data for demo. Returns count added."""
    ensure_benchmarking_tables(db_path)
    con = connect(db_path)
    if con.execute("SELECT COUNT(*) FROM analyst_coverage").fetchone()[0] > 0:
        con.close()
        return 0

    now = datetime.now(timezone.utc)

    analysts = [
        ("Sarah Chen",     "Goldman Sachs",    85000,  "US Technology"),
        ("Mohammed Al-Haj","Emirates NBD",      40000,  "GCC Equities"),
        ("James Whitmore", "Morgan Stanley",    70000,  "Global Macro"),
        ("Priya Sharma",   "JP Morgan",         60000,  "Emerging Markets"),
        ("Lars Eriksson",  "UBS",               45000,  "European Equities"),
    ]

    for name, broker, commission, coverage in analysts:
        cur = con.execute(
            "INSERT INTO analyst_coverage (analyst_name, broker, annual_commission, coverage_universe) "
            "VALUES (?,?,?,?)", (name, broker, commission, coverage)
        )
        analyst_id = cur.lastrowid

        # Generate realistic call history
        sample_calls = [
            # (ticker, direction, conviction, days_ago, outcome, return_pct)
            ("NVDA", "BUY",  5, 180, "HIT",     42.3),
            ("MSFT", "BUY",  4, 120, "HIT",     18.7),
            ("META", "BUY",  3,  90, "HIT",     31.2),
            ("AAPL", "SELL", 2,  60, "MISS",    -8.4),
            ("GOOGL","BUY",  4,  45, "PARTIAL", 12.1),
            ("AMZN", "BUY",  5,  30, "PENDING", None),
            ("XOM",  "SELL", 3,  15, "PENDING", None),
        ]

        # Each analyst gets a randomised subset and performance variation
        import random
        random.seed(hash(name))
        for i, (ticker, direction, conviction, days_ago, outcome, ret) in enumerate(sample_calls):
            if random.random() > 0.3:   # ~70% coverage per analyst
                # Add noise to returns
                actual_ret = None if ret is None else round(ret * random.uniform(0.7, 1.3), 1)
                issued = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                resolved = None if outcome == "PENDING" else \
                    (now - timedelta(days=max(0, days_ago-30))).strftime("%Y-%m-%d")

                con.execute(
                    """INSERT INTO analyst_calls
                       (analyst_id, ticker, direction, conviction,
                        issued_at, resolved_at, outcome, return_pct)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (analyst_id, ticker, direction, conviction,
                     issued, resolved, outcome, actual_ret)
                )

    con.commit()
    count = len(analysts)
    con.close()
    return count
