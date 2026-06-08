"""
ARP Platform – shadow_book.py
Trade idea capture and shadow portfolio tracker.

Maps directly to ARP's roadmap item:
  "Trade Tracker & Shadow Book — tracking every trade idea (executed or not)
   alongside a live shadow book to drive data-backed end-of-year bonus reviews."

Design:
  - Any analyst or risk user can submit a trade idea with a thesis and conviction score
  - Ideas are tracked in trade_ideas table independently of actual trade execution
  - Outcome P&L can be recorded when an idea resolves (position closed / thesis invalidated)
  - Shadow book report shows hit rate, conviction-weighted performance, and top contributors
"""

from __future__ import annotations
import sqlite3
from typing import Optional
from backend.rbac import User, check_permission, is_admin
from backend.audit import log
from backend.db import connect


def _can_modify_idea(user: User, idea: dict) -> bool:
    """Submitter may edit/delete own OPEN ideas; risk and admin may modify any."""
    if user.role in ("risk", "admin") or is_admin(user):
        return True
    return idea.get("submitted_by") == user.email


# ── Tool: submit_trade_idea ───────────────────────────────────────────────────

def submit_trade_idea(
    user:         User,
    ticker:       str,
    direction:    str,       # BUY | SELL | SHORT
    thesis:       str,
    conviction:   int,       # 1 (low) – 5 (high)
    target_price: Optional[float] = None,
    stop_loss:    Optional[float] = None,
    db_path:      Optional[str]   = None,
) -> dict:
    """
    Capture a trade idea in the shadow book.
    Requires analyst or risk role (portfolio access).
    The idea is logged whether or not it gets executed.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "submit_trade_idea", perm.allowed,
        question=f"{direction} {ticker} conviction={conviction}",
        deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    direction = direction.upper()
    if direction not in ("BUY", "SELL", "SHORT"):
        return {"error": "direction must be BUY, SELL, or SHORT", "allowed": True}
    if not 1 <= conviction <= 5:
        return {"error": "conviction must be 1–5", "allowed": True}
    if not thesis.strip():
        return {"error": "thesis cannot be empty", "allowed": True}

    con = connect(db_path)
    try:
        cur = con.execute(
            """INSERT INTO trade_ideas
               (ticker, direction, thesis, target_price, stop_loss,
                conviction, submitted_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), direction, thesis.strip(),
             target_price, stop_loss, conviction, user.email),
        )
        con.commit()
        idea_id = cur.lastrowid
        return {
            "allowed":  True,
            "idea_id":  idea_id,
            "ticker":   ticker.upper(),
            "direction": direction,
            "conviction": conviction,
            "message":  (
                f"Trade idea #{idea_id} captured in shadow book. "
                "Idea is tracked independently of execution."
            ),
        }
    finally:
        con.close()


# ── Tool: get_shadow_book ─────────────────────────────────────────────────────

def get_shadow_book(
    user:    User,
    status:  Optional[str] = None,    # filter: OPEN | EXECUTED | REJECTED | EXPIRED
    db_path: Optional[str] = None,
) -> dict:
    """
    Return all trade ideas in the shadow book with optional status filter.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_shadow_book", perm.allowed,
        question=f"status={status}", deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        if status:
            rows = con.execute(
                "SELECT * FROM trade_ideas WHERE status=? ORDER BY created_at DESC",
                (status.upper(),),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM trade_ideas ORDER BY created_at DESC"
            ).fetchall()

        ideas = [dict(r) for r in rows]
        return {
            "allowed": True,
            "ideas":   ideas,
            "count":   len(ideas),
            "filter":  status or "all",
        }
    finally:
        con.close()


# ── Tool: get_shadow_book_report ──────────────────────────────────────────────

def get_shadow_book_report(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Shadow book performance report:
      - Overall hit rate (resolved ideas with positive P&L)
      - Conviction-weighted hit rate (high-conviction ideas weighted more)
      - Top performers by ticker
      - Ideas by submitter (for end-of-year review)
    Required role: risk (needs trades + portfolio context).
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "get_shadow_book_report", perm.allowed,
        db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        all_ideas = con.execute("SELECT * FROM trade_ideas").fetchall()
        resolved  = [r for r in all_ideas if r["outcome_pnl"] is not None]
        winners   = [r for r in resolved if r["outcome_pnl"] > 0]

        # Hit rate
        hit_rate = round(len(winners) / len(resolved) * 100, 1) if resolved else 0.0

        # Conviction-weighted hit rate
        # Weight each idea by conviction score (1-5), count wins
        total_weight = sum(r["conviction"] for r in resolved) or 1
        weighted_wins = sum(r["conviction"] for r in resolved if r["outcome_pnl"] > 0)
        conviction_hit_rate = round(weighted_wins / total_weight * 100, 1)

        # By submitter
        by_submitter: dict[str, dict] = {}
        for r in all_ideas:
            s = r["submitted_by"]
            if s not in by_submitter:
                by_submitter[s] = {"total": 0, "resolved": 0, "wins": 0, "pnl": 0.0}
            by_submitter[s]["total"] += 1
            if r["outcome_pnl"] is not None:
                by_submitter[s]["resolved"] += 1
                if r["outcome_pnl"] > 0:
                    by_submitter[s]["wins"] += 1
                by_submitter[s]["pnl"] += r["outcome_pnl"]

        # Best tickers by avg P&L
        ticker_pnl: dict[str, list] = {}
        for r in resolved:
            t = r["ticker"]
            if t not in ticker_pnl:
                ticker_pnl[t] = []
            ticker_pnl[t].append(r["outcome_pnl"])
        ticker_avgs = [
            {"ticker": t, "avg_pnl": round(sum(v)/len(v), 2), "count": len(v)}
            for t, v in ticker_pnl.items()
        ]
        ticker_avgs.sort(key=lambda x: x["avg_pnl"], reverse=True)

        return {
            "allowed":              True,
            "total_ideas":          len(all_ideas),
            "open_ideas":           sum(1 for r in all_ideas if r["status"] == "OPEN"),
            "resolved_ideas":       len(resolved),
            "hit_rate_pct":         hit_rate,
            "conviction_hit_rate_pct": conviction_hit_rate,
            "total_pnl":            round(sum(r["outcome_pnl"] for r in resolved), 2),
            "by_submitter":         by_submitter,
            "best_tickers":         ticker_avgs[:5],
            "note": (
                "Hit rate and P&L are based on resolved ideas only. "
                "Open ideas are excluded from performance calculation."
            ),
        }
    finally:
        con.close()


# ── Tool: update_trade_idea ─────────────────────────────────────────────────

def update_trade_idea(
    user:         User,
    idea_id:      int,
    ticker:       str,
    direction:    str,
    thesis:       str,
    conviction:   int,
    target_price: Optional[float] = None,
    stop_loss:    Optional[float] = None,
    db_path:      Optional[str]   = None,
) -> dict:
    """Edit an OPEN trade idea. Submitter, risk, or admin."""
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "update_trade_idea", perm.allowed,
        question=f"idea_id={idea_id}", deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    direction = direction.upper()
    if direction not in ("BUY", "SELL", "SHORT"):
        return {"error": "direction must be BUY, SELL, or SHORT", "allowed": True}
    if not 1 <= conviction <= 5:
        return {"error": "conviction must be 1–5", "allowed": True}
    if not thesis.strip():
        return {"error": "thesis cannot be empty", "allowed": True}

    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM trade_ideas WHERE id = ?", (idea_id,),
        ).fetchone()
        if not row:
            return {"allowed": True, "error": f"Idea #{idea_id} not found."}
        idea = dict(row)
        if idea["status"] != "OPEN":
            return {
                "allowed": True,
                "error": f"Idea #{idea_id} is {idea['status']} — only OPEN ideas can be edited.",
            }
        if not _can_modify_idea(user, idea):
            return {
                "allowed": False,
                "error": "You may only edit your own ideas unless you are risk or admin.",
            }

        con.execute(
            """UPDATE trade_ideas
               SET ticker=?, direction=?, thesis=?, target_price=?, stop_loss=?, conviction=?
               WHERE id=? AND status='OPEN'""",
            (
                ticker.upper(), direction, thesis.strip(),
                target_price, stop_loss, conviction, idea_id,
            ),
        )
        con.commit()
        return {
            "allowed":   True,
            "updated":   True,
            "idea_id":   idea_id,
            "ticker":    ticker.upper(),
            "direction": direction,
            "conviction": conviction,
            "message":   f"Idea #{idea_id} updated.",
        }
    finally:
        con.close()


# ── Tool: delete_trade_idea ───────────────────────────────────────────────────

def delete_trade_idea(
    user:    User,
    idea_id: int,
    db_path: Optional[str] = None,
) -> dict:
    """Remove an OPEN trade idea. Submitter, risk, or admin."""
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "delete_trade_idea", perm.allowed,
        question=f"idea_id={idea_id}", deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM trade_ideas WHERE id = ?", (idea_id,),
        ).fetchone()
        if not row:
            return {"allowed": True, "error": f"Idea #{idea_id} not found."}
        idea = dict(row)
        if idea["status"] != "OPEN":
            return {
                "allowed": True,
                "error": f"Idea #{idea_id} is {idea['status']} — only OPEN ideas can be removed.",
            }
        if not _can_modify_idea(user, idea):
            return {
                "allowed": False,
                "error": "You may only remove your own ideas unless you are risk or admin.",
            }

        deleted = con.execute(
            "DELETE FROM trade_ideas WHERE id = ? AND status = 'OPEN'",
            (idea_id,),
        ).rowcount
        con.commit()
        if deleted == 0:
            return {"allowed": True, "deleted": False, "message": f"Idea #{idea_id} not removed."}
        return {
            "allowed": True,
            "deleted": True,
            "idea_id": idea_id,
            "message": f"Idea #{idea_id} removed from the notebook.",
        }
    finally:
        con.close()


# ── Tool: resolve_trade_idea ──────────────────────────────────────────────────

def resolve_trade_idea(
    user:       User,
    idea_id:    int,
    status:     str,         # EXECUTED | REJECTED | EXPIRED
    outcome_pnl: Optional[float] = None,
    db_path:    Optional[str] = None,
) -> dict:
    """
    Mark a trade idea as resolved with its outcome P&L.
    Required role: risk.
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "resolve_trade_idea", perm.allowed,
        question=f"idea_id={idea_id} status={status}",
        deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    status = status.upper()
    if status not in ("EXECUTED", "REJECTED", "EXPIRED"):
        return {"error": "status must be EXECUTED, REJECTED, or EXPIRED", "allowed": True}

    con = connect(db_path)
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows_updated = con.execute(
            """UPDATE trade_ideas
               SET status=?, outcome_pnl=?, resolved_at=?
               WHERE id=? AND status='OPEN'""",
            (status, outcome_pnl, now, idea_id),
        ).rowcount
        con.commit()

        if rows_updated == 0:
            return {
                "allowed": True,
                "updated": False,
                "message": f"Idea #{idea_id} not found or already resolved.",
            }
        return {
            "allowed":    True,
            "updated":    True,
            "idea_id":    idea_id,
            "new_status": status,
            "outcome_pnl": outcome_pnl,
            "message":    f"Idea #{idea_id} resolved as {status}.",
        }
    finally:
        con.close()
