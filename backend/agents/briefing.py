"""
ARP Platform – agents/briefing.py
Overnight Briefing Agent.

Maps directly to ARP's NOW (0–1 month) roadmap priority:
  "Overnight Briefing Agent: delivering a comprehensive market and email
   summary by 3:30 AM."

External overnight lens: macro, market movers, CIO inbox highlights.
Does NOT duplicate the internal ops digest (pipeline, CRM, attribution detail).

In production: scheduled via cron at 03:30 AM Dubai time (UTC+4),
output pushed to the CIO's email via Microsoft 365 integration.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend import llm
from backend.config import get_db_path

BRIEFING_PROMPT = """You are preparing the EXTERNAL overnight briefing for ARP Global Capital.
This is NOT the internal operations digest. The CIO reads this to understand what changed
in markets and in the inbox while the team slept — before pre-market open.

Do NOT discuss: data pipeline health, CRM/LP follow-ups, DFSA filing deadlines, shadow book
detail, or trade-blotter mechanics. Those belong in the separate CIO Morning Digest.

Use ONLY the data in context. Structure your response with these exact section headers:

=== OVERNIGHT MARKET & MACRO ===
[3–4 sentences: largest movers, macro tone from market data and any macro-related inbox items.
 Name specific tickers and % moves from context.]

=== INBOX HIGHLIGHTS ===
[Bullet list: URGENT and HIGH priority emails only — sender, subject, one-line why it matters.
 State how many emails were filtered as noise. If none urgent: "No URGENT items in overnight inbox."]

=== PORTFOLIO HEADLINE ===
[Exactly 1–2 sentences: total P&L direction and AUM only — no position list, no attribution breakdown.]

=== OVERNIGHT WATCHLIST ===
[Max 2 bullets: only material risk items that intersect with overnight news or inbox — or "None."]

=== ACTIONS BEFORE OPEN ===
[Numbered list, max 3, ranked by urgency — inbox replies and market-driven actions only.]

Rules: No filler. No disclaimers. Numbers when material. Under 90 seconds to read."""


def _summarize_overnight_inbox() -> dict:
    """Deterministic inbox digest for briefing (no extra LLM call)."""
    from backend.agents.email_triage import build_sample_inbox, _classify_priority

    inbox = build_sample_inbox()
    by_priority: dict[str, list] = {"URGENT": [], "HIGH": [], "NORMAL": [], "LOW": [], "NOISE": []}
    for em in inbox:
        pri = _classify_priority(
            em.get("subject", ""),
            em.get("from", ""),
            em.get("snippet", ""),
        ) or "NORMAL"
        by_priority[pri].append({
            "from":    em.get("from"),
            "subject": em.get("subject"),
            "snippet": (em.get("snippet") or "")[:120],
        })

    return {
        "total_messages":     len(inbox),
        "noise_filtered":     len(by_priority["NOISE"]),
        "urgent":             by_priority["URGENT"],
        "high_priority":      by_priority["HIGH"],
        "normal_count":       len(by_priority["NORMAL"]) + len(by_priority["LOW"]),
        "requires_attention": by_priority["URGENT"] + by_priority["HIGH"],
    }


def _gather_briefing_data(user: User, db_path: str) -> dict:
    """
    External overnight context: markets, inbox, portfolio headline, critical risk only.
    """
    data: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "briefing_focus": "external_overnight — markets, macro, inbox (not internal ops)",
    }

    data["inbox"] = _summarize_overnight_inbox()

    try:
        from backend.tools.portfolio_tools import get_market_movers
        movers = get_market_movers(user, db_path=db_path, top_n=8)
        if movers.get("allowed"):
            data["market_gainers"] = movers.get("top_gainers", [])[:5]
            data["market_losers"]  = movers.get("top_losers", [])[:5]
            data["market_source"]  = movers.get("source", "mock")
    except Exception as e:
        data["market_error"] = str(e)

    try:
        from backend.tools.portfolio_tools import get_portfolio_summary
        summary = get_portfolio_summary(user, db_path=db_path)
        if summary.get("allowed"):
            data["portfolio_headline"] = {
                "total_aum":         summary.get("total_aum"),
                "total_pnl":         summary.get("total_pnl"),
                "total_pnl_pct":     summary.get("total_pnl_pct"),
                "direction":         "up" if (summary.get("total_pnl") or 0) >= 0 else "down",
            }
    except Exception as e:
        data["portfolio_error"] = str(e)

    try:
        from backend.tools.risk_tools import get_risk_alerts
        alerts = get_risk_alerts(user, db_path=db_path)
        if alerts.get("allowed"):
            critical = [
                a for a in alerts.get("alerts", [])
                if a.get("severity") in ("CRITICAL", "HIGH")
            ]
            data["material_risk_count"] = len(critical)
            data["material_risks"] = [
                {"rule": a.get("rule"), "severity": a.get("severity"), "detail": a.get("detail", "")[:120]}
                for a in critical[:3]
            ]
    except Exception as e:
        data["risk_error"] = str(e)

    return data


def run(
    user:    User,
    db_path: Optional[str] = None,
    model:   Optional[str] = None,
) -> dict:
    """
    Generate the overnight external briefing.
    Requires risk role — briefing may reference inbox and risk summaries.
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "overnight_briefing", perm.allowed,
        question="overnight briefing request", deny_reason=perm.deny_reason,
        db_path=db_path)

    if not perm.allowed:
        return {
            "allowed":  False,
            "response": f"Access denied. {perm.deny_reason}",
        }

    path = db_path or get_db_path()
    data = _gather_briefing_data(user, path)

    context  = json.dumps(data, indent=2, default=str)
    response = llm.chat(
        user_message=(
            "Generate the external overnight briefing: markets, macro tone, and inbox highlights "
            "only. Do not produce an internal operations report."
        ),
        context_data=context,
        system_prompt=BRIEFING_PROMPT,
        model=model,
        audit_ctx=llm.LlmAuditContext(user.email, user.role, "briefing"),
        db_path=path,
    )

    return {
        "allowed":      True,
        "briefing":     response,
        "data":         data,
        "generated_at": data["generated_at"],
        "tool_called":  "overnight_briefing",
        "briefing_type": "external_overnight",
        "sections": [
            "OVERNIGHT MARKET & MACRO",
            "INBOX HIGHLIGHTS",
            "PORTFOLIO HEADLINE",
            "OVERNIGHT WATCHLIST",
            "ACTIONS BEFORE OPEN",
        ],
    }
