"""
ARP Platform – tools/broadridge_pipeline.py
Broadridge Data Pipeline Monitor.

Maps to ARP's NOW roadmap priority:
  "Trading Data Lake (AWS / Broadridge): ingesting historical trade data
   into a clean schema via automated syncs (ideally every 1–15 minutes)."

From the ARP discovery document:
  Current state: Broadridge sends daily manual email files.
  Target state:  Live automated sync every 1–15 minutes.

This module:
  - Monitors the health of the data ingestion pipeline
  - Tracks last successful sync, record counts, and data freshness
  - Detects staleness (gap between expected and actual sync time)
  - Simulates the Broadridge → SQLite ingestion flow
  - Provides a /pipeline/status endpoint for the dashboard health strip
  - In production: replaces the stub connector with Broadridge REST API
    calls authenticated via OAuth2 client credentials

Architecture note:
  The pipeline runs as a separate process (cron or daemon).
  This module exposes the monitoring/status layer only.

  DEMO: simulates Broadridge → SQLite ingestion for dashboard health metrics.
  PRODUCTION: AWS Glue or Lambda (me-central-1) writes to PostgreSQL using
  the Broadridge canonical schema; this status API and RBAC layer unchanged.
"""

from __future__ import annotations
import hashlib
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.config import get_data_scope, is_demo_book
from backend.data_scope import book_freshness_label
from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect

# Expected sync interval (minutes) — configurable via env
SYNC_INTERVAL_MINUTES = int(os.getenv("BROADRIDGE_SYNC_INTERVAL", "15"))


def ensure_pipeline_tables(db_path: Optional[str] = None) -> None:
    """Create pipeline monitoring tables. Safe to call multiple times."""
    con = connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT    NOT NULL DEFAULT 'broadridge',
            status          TEXT    NOT NULL CHECK(status IN ('SUCCESS','FAILED','PARTIAL','RUNNING')),
            records_ingested INTEGER DEFAULT 0,
            records_skipped  INTEGER DEFAULT 0,
            records_failed   INTEGER DEFAULT 0,
            duration_ms      INTEGER,
            error_message    TEXT,
            checksum         TEXT,
            started_at       TEXT    NOT NULL DEFAULT (datetime('now')),
            completed_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS pipeline_config (
            key     TEXT PRIMARY KEY,
            value   TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_pipeline_source  ON pipeline_runs(source);
        CREATE INDEX IF NOT EXISTS idx_pipeline_started ON pipeline_runs(started_at);
    """)

    # Default config
    defaults = [
        ("sync_interval_minutes", str(SYNC_INTERVAL_MINUTES)),
        ("source_type", "broadridge"),
        ("target_schema", "arp_trading_lake"),
        ("aws_region", "me-central-1"),
        ("data_residency", "DIFC"),
        ("last_full_reload", "never"),
    ]
    for k, v in defaults:
        con.execute(
            "INSERT OR IGNORE INTO pipeline_config (key, value) VALUES (?, ?)", (k, v)
        )
    con.commit()
    con.close()


# ── Tool: get_pipeline_status ─────────────────────────────────────────────────

def get_pipeline_status(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Returns the current health status of the Broadridge data pipeline.
    Checks: last sync time, data freshness, record counts, error history.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_pipeline_status", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_pipeline_tables(db_path)
    con = connect(db_path)
    try:
        # Most recent run
        last_run = con.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        # Last 10 runs for trend
        recent_runs = [dict(r) for r in con.execute(
            "SELECT status, records_ingested, duration_ms, started_at "
            "FROM pipeline_runs ORDER BY started_at DESC LIMIT 10"
        ).fetchall()]

        # Config
        config = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM pipeline_config"
        ).fetchall()}

        # Current trade/market data counts
        trade_count  = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        price_count  = con.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
        latest_trade = con.execute(
            "SELECT MAX(traded_at) FROM trades"
        ).fetchone()[0]
        latest_price = con.execute(
            "SELECT MAX(fetched_at) FROM market_prices"
        ).fetchone()[0]

        now = datetime.now(timezone.utc)

        # Freshness assessment
        if last_run:
            last_run_dt = datetime.fromisoformat(
                last_run["completed_at"] or last_run["started_at"]
            ).replace(tzinfo=timezone.utc)
            minutes_since = (now - last_run_dt).total_seconds() / 60
            is_fresh = minutes_since <= SYNC_INTERVAL_MINUTES * 2
            last_status = last_run["status"]
        else:
            minutes_since = 9999
            is_fresh = False
            last_status = "NEVER_RUN"

        # Error rate (last 10 runs)
        if recent_runs:
            error_count = sum(1 for r in recent_runs if r["status"] == "FAILED")
            error_rate  = round(error_count / len(recent_runs) * 100, 0)
        else:
            error_count = 0
            error_rate  = 0

        # Overall health
        if last_status == "NEVER_RUN":
            health = "NOT_CONFIGURED"
        elif last_status == "FAILED":
            health = "DEGRADED"
        elif not is_fresh:
            health = "STALE"
        elif error_rate > 20:
            health = "DEGRADED"
        else:
            health = "HEALTHY"

        return {
            "allowed":             True,
            "health":              health,
            "pipeline_health":     health,
            "book_data_scope":     get_data_scope(),
            "book_freshness":      book_freshness_label(health),
            "data_freshness_note": (
                "pipeline_health reflects sync heartbeat only. "
                "Simulated sync does not mutate the book while data_scope is demo_snapshot."
                if is_demo_book()
                else "book_freshness tracks live ingestion when ARP_DATA_SCOPE=live."
            ),
            "last_status":         last_status,
            "minutes_since_sync":  round(minutes_since, 1),
            "is_fresh":            is_fresh,
            "sync_interval_target": SYNC_INTERVAL_MINUTES,
            "trade_records":       trade_count,
            "price_records":       price_count,
            "latest_trade_at":     latest_trade,
            "latest_price_at":     latest_price,
            "error_rate_pct":      error_rate,
            "recent_runs":         recent_runs,
            "config":              config,
            "production_note": (
                "In production: Broadridge REST API → AWS Glue job → "
                "PostgreSQL (me-central-1) every 15 minutes. "
                "Current: mock data loaded at seed time."
            ),
        }
    finally:
        con.close()


# ── Tool: simulate_sync_run ───────────────────────────────────────────────────

def simulate_sync_run(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Simulate a Broadridge sync run for demo purposes.
    Records a pipeline_runs entry with realistic metrics.
    In production: replaced by the actual Broadridge ingestion job.
    Required role: risk.
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "simulate_sync_run", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_pipeline_tables(db_path)
    con = connect(db_path)
    try:
        import random, time
        start = time.time()

        # Simulate ingestion work
        trade_count  = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        price_count  = con.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]

        # Simulate realistic metrics
        records_ingested = random.randint(8, 45)
        records_skipped  = random.randint(0, 3)
        records_failed   = 0
        duration_ms      = random.randint(320, 1800)

        # Checksum of current data state
        checksum_src = f"{trade_count}{price_count}{records_ingested}"
        checksum     = hashlib.sha256(checksum_src.encode()).hexdigest()[:16]

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            """INSERT INTO pipeline_runs
               (source, status, records_ingested, records_skipped,
                records_failed, duration_ms, checksum, started_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("broadridge", "SUCCESS", records_ingested, records_skipped,
             records_failed, duration_ms, checksum, now, now),
        )
        con.commit()

        return {
            "allowed":          True,
            "simulated":        True,
            "book_mutated":     False,
            "status":           "SUCCESS",
            "records_ingested": records_ingested,
            "records_skipped":  records_skipped,
            "duration_ms":      duration_ms,
            "checksum":         checksum,
            "completed_at":     now,
            "book_data_scope":  get_data_scope(),
            "message": (
                f"Sync simulated (audit heartbeat only): {records_ingested} records "
                f"reported in {duration_ms}ms. Book unchanged — checksum {checksum}."
            ),
        }
    finally:
        con.close()


# ── Tool: get_ingestion_stats ─────────────────────────────────────────────────

def get_ingestion_stats(
    user:    User,
    days:    int = 7,
    db_path: Optional[str] = None,
) -> dict:
    """
    Aggregated ingestion stats over the last N days.
    Useful for weekly pipeline health reports.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_ingestion_stats", perm.allowed,
        question=f"days={days}", deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_pipeline_tables(db_path)
    con = connect(db_path)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        runs = con.execute(
            "SELECT * FROM pipeline_runs WHERE started_at >= ? ORDER BY started_at DESC",
            (cutoff,)
        ).fetchall()

        if not runs:
            return {
                "allowed": True,
                "days": days,
                "total_runs": 0,
                "message": "No pipeline runs in the specified period.",
            }

        total    = len(runs)
        success  = sum(1 for r in runs if r["status"] == "SUCCESS")
        failed   = sum(1 for r in runs if r["status"] == "FAILED")
        total_rec = sum(r["records_ingested"] or 0 for r in runs)
        avg_dur  = round(sum(r["duration_ms"] or 0 for r in runs) / total)

        return {
            "allowed":           True,
            "period_days":       days,
            "total_runs":        total,
            "successful_runs":   success,
            "failed_runs":       failed,
            "success_rate_pct":  round(success / total * 100, 1),
            "total_records_ingested": total_rec,
            "avg_duration_ms":   avg_dur,
        }
    finally:
        con.close()
