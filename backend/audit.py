"""
ARP Platform – audit.py
Writes tool and agent interactions to audit_logs with tamper-evident SHA-256 hash chaining.

Hash chain (v2 — current):
  sha256(prev_hash | email | role | tool | allowed | timestamp | question | deny_reason)
  question and deny_reason are normalised (empty string when null).

Hash chain (v1 — legacy records):
  sha256(prev_hash | email | role | tool | allowed | timestamp)

verify_chain() accepts both v1 and v2 rows so existing databases remain valid.

LLM invocations are logged separately in llm_audit_logs (model, latency, response hash).
"""

from __future__ import annotations
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from backend.db import connect

GENESIS_HASH = "0" * 64


def _norm_field(value: Optional[str]) -> str:
    return (value or "").strip()


def _compute_hash_v1(
    prev_hash: str, user_email: str, role: str,
    tool_called: str, allowed: int, timestamp: str,
) -> str:
    payload = f"{prev_hash}|{user_email}|{role}|{tool_called}|{allowed}|{timestamp}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _compute_hash_v2(
    prev_hash: str, user_email: str, role: str,
    tool_called: str, allowed: int, timestamp: str,
    question: Optional[str], deny_reason: Optional[str],
) -> str:
    q = _norm_field(question)
    d = _norm_field(deny_reason)
    payload = (
        f"{prev_hash}|{user_email}|{role}|{tool_called}|{allowed}|{timestamp}|{q}|{d}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _last_hash(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT record_hash FROM audit_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row and row[0] else GENESIS_HASH


def _ensure_hash_column(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_logs)")}
    if "record_hash" not in cols:
        con.execute("ALTER TABLE audit_logs ADD COLUMN record_hash TEXT")


def ensure_llm_audit_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS llm_audit_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email      TEXT    NOT NULL,
            role            TEXT    NOT NULL,
            context         TEXT    NOT NULL,
            model           TEXT,
            fallback_model  TEXT,
            fallback_used   INTEGER NOT NULL DEFAULT 0,
            latency_ms      INTEGER,
            response_hash   TEXT,
            success         INTEGER NOT NULL DEFAULT 1,
            timestamp       TEXT    NOT NULL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_audit_user ON llm_audit_logs(user_email)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_audit_ts ON llm_audit_logs(timestamp)"
    )


def is_agent_request(question: Optional[str]) -> bool:
    return bool(question and question.strip())


def format_agent_question(question: str, client_ip: Optional[str] = None) -> str:
    q = question.strip()
    if client_ip:
        return f"[{client_ip}] {q}"
    return q


def log(
    user_email:  str,
    role:        str,
    tool_called: str,
    allowed:     bool,
    question:    Optional[str] = None,
    deny_reason: Optional[str] = None,
    db_path:     Optional[str] = None,
    *,
    source:      str = "direct",
) -> None:
    """
    Write one audit record with a chained SHA-256 hash (v2).
    Non-blocking — errors are printed but never raised.
    """
    if source == "direct" and is_agent_request(question):
        return
    try:
        con = connect(db_path)
        _ensure_hash_column(con)

        ts        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        prev_hash = _last_hash(con)
        allowed_i = 1 if allowed else 0
        rec_hash  = _compute_hash_v2(
            prev_hash, user_email, role, tool_called, allowed_i, ts,
            question, deny_reason,
        )

        con.execute(
            """INSERT INTO audit_logs
               (user_email, role, question, tool_called, allowed,
                deny_reason, timestamp, record_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_email, role, question, tool_called,
             allowed_i, deny_reason, ts, rec_hash),
        )
        con.commit()
        con.close()
    except sqlite3.Error as e:
        print(f"[AUDIT ERROR] Failed to write log: {e}")


def log_llm_call(
    user_email:     str,
    role:           str,
    context:        str,
    model:          str,
    fallback_model: str,
    fallback_used:  bool,
    latency_ms:     int,
    response_text:  str,
    *,
    success:        bool = True,
    db_path:        Optional[str] = None,
) -> None:
    """Append-only LLM invocation audit (response stored as SHA-256 hash only)."""
    try:
        con = connect(db_path)
        ensure_llm_audit_table(con)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        resp_hash = hashlib.sha256((response_text or "").encode()).hexdigest()
        con.execute(
            """INSERT INTO llm_audit_logs
               (user_email, role, context, model, fallback_model, fallback_used,
                latency_ms, response_hash, success, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_email, role, context, model, fallback_model,
                1 if fallback_used else 0, latency_ms, resp_hash,
                1 if success else 0, ts,
            ),
        )
        con.commit()
        con.close()
    except sqlite3.Error as e:
        print(f"[AUDIT ERROR] Failed to write LLM log: {e}")


def verify_chain(db_path: Optional[str] = None) -> dict:
    """
    Walk the audit log and verify the hash chain (v2 with v1 fallback per row).
    """
    try:
        con = connect(db_path)
        _ensure_hash_column(con)
        rows = con.execute(
            """SELECT id, user_email, role, tool_called, allowed, timestamp,
                      record_hash, question, deny_reason
               FROM audit_logs ORDER BY id ASC"""
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        return {
            "valid": False, "checked": 0, "first_breach": None,
            "message": f"DB error: {e}",
        }

    if not rows:
        return {
            "valid": True, "checked": 0, "first_breach": None,
            "message": "No records to verify.",
        }

    prev_hash = GENESIS_HASH
    for row in rows:
        rid, email, role, tool, allowed, ts, stored_hash, question, deny = row
        expected_v2 = _compute_hash_v2(
            prev_hash, email, role, tool, allowed, ts, question, deny,
        )
        if stored_hash == expected_v2:
            prev_hash = stored_hash
            continue
        # Legacy v1 rows (no question/deny in hash) — skip v1 when those fields are set
        legacy_row = not _norm_field(question) and not _norm_field(deny)
        expected_v1 = _compute_hash_v1(prev_hash, email, role, tool, allowed, ts)
        if legacy_row and stored_hash == expected_v1:
            prev_hash = stored_hash
            continue
        return {
            "valid":        False,
            "checked":      rid,
            "first_breach": rid,
            "message": (
                f"Hash mismatch at record ID {rid}. "
                f"Expected v2 {expected_v2[:16]}… "
                f"got {stored_hash[:16] if stored_hash else 'NULL'}…"
            ),
        }

    return {
        "valid":        True,
        "checked":      len(rows),
        "first_breach": None,
        "message":      f"Chain intact across {len(rows)} records.",
    }


def recent_logs(limit: int = 50, db_path: Optional[str] = None) -> list[dict]:
    try:
        con = connect(db_path)
        _ensure_hash_column(con)
        rows = con.execute(
            """SELECT user_email, role, question, tool_called,
                      CASE allowed WHEN 1 THEN 'ALLOWED' ELSE 'DENIED' END as result,
                      deny_reason, timestamp,
                      SUBSTR(record_hash, 1, 12) as hash_preview
               FROM audit_logs
               ORDER BY timestamp DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def recent_llm_logs(limit: int = 50, db_path: Optional[str] = None) -> list[dict]:
    try:
        con = connect(db_path)
        ensure_llm_audit_table(con)
        rows = con.execute(
            """SELECT user_email, role, context, model, fallback_model,
                      fallback_used, latency_ms, response_hash, success, timestamp
               FROM llm_audit_logs
               ORDER BY timestamp DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
