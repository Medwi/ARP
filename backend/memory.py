"""
ARP Platform – memory.py
Per-user conversational memory for the AI agents.

Design (deliberately simple, functional):
  - Each allowed agent interaction (question + answer) is persisted per user.
  - On a new question, the agent recalls the user's recent interactions and,
    when relevant, the most topically-related past exchange, to maintain
    continuity across follow-up questions ("what about the risk side?").
  - Memory is strictly scoped to the requesting user (no cross-user leakage).

This is short-term episodic memory, not long-term fine-tuning. It runs fully
on-device against the same SQLite database as the rest of the platform.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional

from backend.config import (
    memory_enabled,
    memory_max_per_user,
    memory_recall_limit,
)
from backend.db import connect
from backend.prompt_safety import sanitize_memory_snippet, sanitize_user_question

# Answers can be long; truncate what we feed back into the prompt.
_ANSWER_SNIPPET = 400


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create the memory table if it does not exist (safe to call repeatedly)."""
    con.executescript(
        """
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
        CREATE INDEX IF NOT EXISTS idx_memory_user ON agent_memory(user_email);
        CREATE INDEX IF NOT EXISTS idx_memory_time ON agent_memory(created_at);
        """
    )


def remember(
    user_email: str,
    role: str,
    agent: str,
    question: str,
    answer: str,
    tool_called: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Persist one interaction. Non-blocking — failures are swallowed."""
    if not memory_enabled() or not question.strip() or not answer.strip():
        return
    safe_q = sanitize_user_question(question)
    safe_a = sanitize_memory_snippet(answer)
    try:
        con = connect(db_path)
        ensure_schema(con)
        con.execute(
            """INSERT INTO agent_memory
               (user_email, role, agent, question, answer, tool_called)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_email, role, agent, safe_q, safe_a, tool_called),
        )
        # Trim oldest rows beyond the per-user cap.
        con.execute(
            """DELETE FROM agent_memory
               WHERE user_email = ?
                 AND id NOT IN (
                     SELECT id FROM agent_memory
                     WHERE user_email = ?
                     ORDER BY id DESC LIMIT ?
                 )""",
            (user_email, user_email, memory_max_per_user()),
        )
        con.commit()
        con.close()
    except sqlite3.Error as e:
        print(f"[MEMORY ERROR] Failed to store interaction: {e}")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def recall(
    user_email: str,
    query: str = "",
    limit: int | None = None,
    db_path: Optional[str] = None,
) -> list[dict]:
    """
    Return up to `limit` past interactions for this user, ordered for prompt use
    (oldest -> newest). Recent turns are always included; if a query is supplied,
    the single most topically-relevant older turn is also surfaced.
    """
    if not memory_enabled():
        return []
    limit = limit if limit is not None else memory_recall_limit()
    try:
        con = connect(db_path)
        ensure_schema(con)
        rows = con.execute(
            """SELECT id, agent, question, answer, tool_called, created_at
               FROM agent_memory
               WHERE user_email = ?
               ORDER BY id DESC
               LIMIT ?""",
            (user_email, max(limit * 4, limit)),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []

    if not rows:
        return []

    entries = [dict(r) for r in rows]            # newest first
    recent = entries[:limit]
    selected = {e["id"]: e for e in recent}

    # Add the most relevant older turn, if a query is given.
    if query.strip() and len(entries) > limit:
        q_tokens = _tokens(query)
        if q_tokens:
            older = entries[limit:]
            best, best_score = None, 0.0
            for e in older:
                overlap = len(q_tokens & _tokens(e["question"])) / len(q_tokens)
                if overlap > best_score:
                    best, best_score = e, overlap
            if best and best_score > 0:
                selected[best["id"]] = best

    ordered = sorted(selected.values(), key=lambda e: e["id"])  # chronological
    return ordered


def format_for_prompt(entries: list[dict]) -> str:
    """Render recalled interactions as a conversation-history context block."""
    if not entries:
        return ""
    lines = []
    for e in entries:
        answer = sanitize_memory_snippet((e.get("answer") or "")[:_ANSWER_SNIPPET])
        q_text = sanitize_user_question(e.get("question", ""))
        lines.append(
            f"- Earlier ({e.get('agent', 'agent')} agent) the user asked: "
            f"\"{q_text}\"\n  You answered: {answer}"
        )
    body = "\n".join(lines)
    return (
        "--- CONVERSATION MEMORY (this user's recent interactions; for continuity) ---\n"
        f"{body}\n"
    )


def recall_block(
    user_email: str,
    query: str,
    db_path: Optional[str] = None,
    limit: int | None = None,
) -> tuple[str, list[dict]]:
    """Convenience: return (prompt_block, entries) for agent context building."""
    entries = recall(user_email, query, limit=limit, db_path=db_path)
    return format_for_prompt(entries), entries


def clear(user_email: str, db_path: Optional[str] = None) -> int:
    """Delete all stored memory for a user. Returns rows removed."""
    try:
        con = connect(db_path)
        ensure_schema(con)
        cur = con.execute(
            "DELETE FROM agent_memory WHERE user_email = ?", (user_email,)
        )
        removed = cur.rowcount
        con.commit()
        con.close()
        return removed
    except sqlite3.Error:
        return 0


def history(user_email: str, limit: int = 20, db_path: Optional[str] = None) -> list[dict]:
    """Recent interactions for this user (newest first) for dashboard display."""
    try:
        con = connect(db_path)
        ensure_schema(con)
        rows = con.execute(
            """SELECT agent, question, answer, tool_called, created_at
               FROM agent_memory
               WHERE user_email = ?
               ORDER BY id DESC
               LIMIT ?""",
            (user_email, limit),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def status(user_email: Optional[str] = None, db_path: Optional[str] = None) -> dict:
    """Memory subsystem status for /health and observability."""
    total = 0
    user_count = 0
    try:
        con = connect(db_path)
        ensure_schema(con)
        total = con.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0]
        if user_email:
            user_count = con.execute(
                "SELECT COUNT(*) FROM agent_memory WHERE user_email = ?", (user_email,)
            ).fetchone()[0]
        con.close()
    except sqlite3.Error:
        pass
    return {
        "enabled":       memory_enabled(),
        "recall_limit":  memory_recall_limit(),
        "max_per_user":  memory_max_per_user(),
        "stored_total":  total,
        "stored_user":   user_count,
    }
