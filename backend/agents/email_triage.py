"""
ARP Platform – agents/email_triage.py
CIO Email Triage Agent.

Maps directly to ARP's NOW (0–1 month) roadmap priority:
  "Email AI Triage Agent: personal to the CIO (and later scalable to the team).
   Filters noise, highlights key conclusions, outlines action items."

From the ARP discovery document:
  Pain point #1: "Email Overload (CIO) — the daily time drain of triaging email
  manually because standard filters are too blunt."
  Ideal: "A pre-sorted inbox digest."

This module:
  - Accepts raw email batches (subject + sender + body snippet)
  - Classifies each by priority (URGENT / HIGH / NORMAL / LOW / NOISE)
  - Extracts action items and deadlines
  - Produces a structured digest the CIO can read in under 3 minutes
  - Runs locally — email content never leaves the machine

In production: integrates with Microsoft 365 Graph API to pull the CIO's
inbox at 3:30 AM alongside the overnight briefing.
"""

from __future__ import annotations
import json, os
from datetime import datetime, timezone
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend import llm

# ── Priority classification rules (deterministic, no LLM needed) ─────────────
# Applied before LLM to reduce token usage and give instant classification
URGENT_SIGNALS = [
    "margin call", "regulatory", "dfsa", "breach", "compliance", "urgent",
    "immediately", "critical", "fund redemption", "redemption request",
    "wire transfer", "drawdown", "position limit", "stop loss",
]
HIGH_SIGNALS = [
    "investor", "client", "board", "cio", "aum", "subscription",
    "performance", "monthly report", "quarterly", "meeting request",
    "counterparty", "prime broker", "bloomberg",
]
NOISE_SIGNALS = [
    "unsubscribe", "newsletter", "webinar", "conference invitation",
    "linkedin", "sales", "demo request", "trial", "free",
]

# LLM system prompt for email triage
TRIAGE_PROMPT = """You are the CIO's email triage AI for ARP Global Capital.
Analyse each email and respond ONLY with valid JSON in this exact format:
{
  "emails": [
    {
      "id": <original email id>,
      "priority": "URGENT|HIGH|NORMAL|LOW|NOISE",
      "summary": "<one sentence — what this email is actually about>",
      "action_required": true|false,
      "action_items": ["<specific action>", ...],
      "deadline": "<date or null>",
      "sender_type": "INVESTOR|REGULATOR|COUNTERPARTY|INTERNAL|VENDOR|OTHER",
      "reply_suggested": "<draft reply opening line or null>"
    }
  ],
  "digest_summary": "<2-3 sentence overview of the inbox — what needs CIO attention today>"
}

Rules:
- URGENT: regulatory, margin calls, redemptions, compliance deadlines
- HIGH: investor communications, board items, counterparty risk
- NORMAL: routine operational, team updates, scheduled reports
- LOW: FYI items, reading material, non-time-sensitive
- NOISE: marketing, newsletters, unsolicited sales — CIO should never see these
- Be decisive. No hedging. Every email gets a priority.
- action_items must be concrete and actionable, not vague.
- Do NOT reproduce full email text. Summaries only."""


def _classify_priority(subject: str, sender: str, snippet: str) -> str:
    """
    Fast deterministic pre-classifier. Reduces LLM calls for obvious cases.
    Returns priority string or None to fall through to LLM.
    """
    text = f"{subject} {sender} {snippet}".lower()
    if any(s in text for s in NOISE_SIGNALS):
        return "NOISE"
    if any(s in text for s in URGENT_SIGNALS):
        return "URGENT"
    if any(s in text for s in HIGH_SIGNALS):
        return "HIGH"
    return None   # let LLM decide


def triage_emails(
    user:   User,
    emails: list[dict],   # [{ "id": str, "from": str, "subject": str, "snippet": str }]
    model:  Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    """
    Triage a batch of emails for the CIO.
    Each email needs: id, from, subject, snippet (first ~200 chars of body).

    Returns:
        {
          allowed, triaged: [...], digest_summary, stats, generated_at
        }

    Required role: risk (full triage) or analyst (own emails only, simplified).
    """
    # Email triage is a CIO-level tool — risk role required
    perm = check_permission(user, "summary")
    log(user.email, user.role, "email_triage", perm.allowed,
        f"batch_size={len(emails)}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    if not emails:
        return {"allowed": True, "triaged": [], "digest_summary": "No emails to triage.",
                "stats": {}, "generated_at": datetime.now(timezone.utc).isoformat()}

    # ── Pre-classify obvious cases ────────────────────────────────────────────
    pre_classified: dict[str, str] = {}
    llm_batch: list[dict] = []

    for email in emails:
        pre = _classify_priority(
            email.get("subject", ""),
            email.get("from", ""),
            email.get("snippet", ""),
        )
        if pre == "NOISE":
            pre_classified[email["id"]] = "NOISE"
        else:
            llm_batch.append(email)

    # ── LLM triage for non-obvious emails ────────────────────────────────────
    llm_results: list[dict] = []
    if llm_batch:
        context = json.dumps({"emails_to_triage": llm_batch}, indent=2)
        raw = llm.chat(
            user_message="Triage these emails for the CIO.",
            context_data=context,
            system_prompt=TRIAGE_PROMPT,
            model=model,
            audit_ctx=llm.LlmAuditContext(
                user.email, user.role, "email_triage",
                question_hint=f"batch={len(llm_batch)}",
            ),
            db_path=db_path,
        )
        try:
            # Strip any LLM preamble and parse JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            llm_results = parsed.get("emails", [])
            digest_summary = parsed.get("digest_summary", "")
        except (json.JSONDecodeError, KeyError):
            # LLM returned non-JSON — build basic triage from pre-classification
            llm_results = [
                {
                    "id": e["id"],
                    "priority": pre_classified.get(e["id"], "NORMAL"),
                    "summary": e.get("subject", "No subject"),
                    "action_required": False,
                    "action_items": [],
                    "deadline": None,
                    "sender_type": "OTHER",
                    "reply_suggested": None,
                }
                for e in llm_batch
            ]
            digest_summary = "LLM triage unavailable — basic classification applied."
    else:
        digest_summary = "All emails classified as NOISE by pre-filter."

    # ── Merge pre-classified NOISE back in ───────────────────────────────────
    noise_items = [
        {
            "id":             eid,
            "priority":       "NOISE",
            "summary":        "Filtered as noise — not shown to CIO.",
            "action_required": False,
            "action_items":   [],
            "deadline":       None,
            "sender_type":    "VENDOR",
            "reply_suggested": None,
        }
        for eid in pre_classified
    ]
    all_triaged = llm_results + noise_items

    # ── Sort by priority ──────────────────────────────────────────────────────
    priority_order = {"URGENT": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3, "NOISE": 4}
    all_triaged.sort(key=lambda x: priority_order.get(x.get("priority", "NORMAL"), 2))

    # ── Stats ─────────────────────────────────────────────────────────────────
    from collections import Counter
    stats = dict(Counter(e.get("priority", "NORMAL") for e in all_triaged))
    stats["action_required"] = sum(1 for e in all_triaged if e.get("action_required"))
    stats["noise_filtered"]  = len(pre_classified)

    return {
        "allowed":       True,
        "triaged":       all_triaged,
        "digest_summary": digest_summary,
        "stats":         stats,
        "total":         len(all_triaged),
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "hitl_note":     "All priority classifications are AI-assisted — CIO reviews before action.",
    }


def build_sample_inbox() -> list[dict]:
    """
    Generate a realistic sample inbox for demo purposes.
    Used by the /email-triage/demo endpoint.
    """
    return [
        {
            "id": "em001",
            "from": "prime.broker@goldmansachs.com",
            "subject": "URGENT: Margin Call Notice — ARP Global Capital",
            "snippet": "Please be advised that as of today's close, your portfolio margin "
                       "requirement has increased by $2.3M. Please arrange coverage by 10 AM tomorrow.",
        },
        {
            "id": "em002",
            "from": "investor@sovereign.ae",
            "subject": "Q2 Performance Review Meeting Request",
            "snippet": "Dear Yusuf, following our recent conversation, we would like to schedule "
                       "a formal review of Q2 performance. Our investment committee is available...",
        },
        {
            "id": "em003",
            "from": "dfsa.notifications@dfsa.ae",
            "subject": "DFSA Thematic Review — AI Systems in Asset Management",
            "snippet": "The DFSA is conducting a thematic review of AI system usage in regulated "
                       "entities. Please complete the attached questionnaire by 30 June 2026.",
        },
        {
            "id": "em004",
            "from": "research@jpmorgan.com",
            "subject": "GCC Macro Outlook — June 2026 Update",
            "snippet": "Our GCC macro team has updated its outlook following the latest UAE PMI data. "
                       "Key changes to our rate expectations for H2 2026...",
        },
        {
            "id": "em005",
            "from": "ops@broadridge.com",
            "subject": "Daily Trade Confirmation — ARP Global Capital",
            "snippet": "Please find attached your daily trade confirmations for 04 June 2026. "
                       "Total trades processed: 12. Total notional: $4.2M.",
        },
        {
            "id": "em006",
            "from": "noreply@fintech-conference.com",
            "subject": "You're invited: FinTech Middle East Summit 2026 — Free Early Bird Tickets",
            "snippet": "Join 500+ fintech leaders at the region's premier conference. "
                       "Register now for free early bird access. Unsubscribe here.",
        },
        {
            "id": "em007",
            "from": "innes.harding@arpglobalcapital.com",
            "subject": "Monthly Management Accounts — May 2026 Draft",
            "snippet": "Yusuf, please find the draft May accounts attached for your review "
                       "before we submit to the board. A few items flagged for discussion...",
        },
        {
            "id": "em008",
            "from": "bloomberg.news@bloomberg.net",
            "subject": "Breaking: Fed signals three more rate cuts in 2026",
            "snippet": "Federal Reserve Chair signals a more aggressive easing path as inflation "
                       "data comes in below target for the third consecutive month...",
        },
    ]
