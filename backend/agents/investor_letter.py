"""
ARP Platform – agents/investor_letter.py
AI Investor Letter Generator.

Maps to ARP's SOON roadmap:
  "Investor Reporting & Letters: eliminating manual drafting and siloed data
   by utilising AI to draft investor letters in hours and auto-populate Salesforce."

Pain point addressed:
  "Earnings Season Data Loss — less than 1% of the quarterly earnings influx
   is currently retained. Ideal: automated summary pipeline."

This agent:
  - Produces monthly or quarterly investor letters personalised per investor tier
  - Pulls portfolio performance from the demo snapshot book (data_scope tagged)
  - Generates structured narrative sections (performance, positioning, outlook)
  - Flags required compliance review before any letter is sent
  - Outputs both a full draft and a structured JSON version for Salesforce

Compliance gate: ALL output requires CIO + Compliance sign-off per ARP
sign-off matrix before transmission to investors.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional

from backend.config import data_scope_note, get_data_scope, get_db_path
from backend.rbac import User, check_permission
from backend.audit import log
from backend import llm
from backend.db import connect

LETTER_PROMPT = """You are drafting a professional investor letter for ARP Global Capital,
an alternatives asset manager regulated by the DFSA in the DIFC, Dubai.

The letter must:
- Be formal, precise, and suitable for institutional and HNW investors
- Lead with performance attribution (what drove returns this period)
- Include a brief market commentary (macro backdrop)
- Describe current positioning and any significant changes
- Close with forward-looking commentary and next steps
- Never make forward return guarantees or projections
- Include a standard disclaimer at the end

Respond ONLY with valid JSON in this exact structure:
{
  "subject_line": "...",
  "salutation": "Dear [Name],",
  "performance_section": "...",
  "market_commentary": "...",
  "positioning_section": "...",
  "outlook_section": "...",
  "closing": "...",
  "disclaimer": "This letter is for informational purposes only and does not constitute investment advice. Past performance is not indicative of future results. ARP Global Capital is regulated by the DFSA.",
  "word_count_estimate": 0
}"""


def _gather_portfolio_context(db_path: str) -> dict:
    """Pull portfolio metrics needed for letter generation."""
    con = connect(db_path)

    holdings = con.execute(
        "SELECT ticker, asset_class, weight_pct, "
        "ROUND((current_price-avg_cost)/avg_cost*100,2) as pnl_pct, "
        "market_value FROM portfolio_holdings ORDER BY weight_pct DESC"
    ).fetchall()

    total_mv   = sum(h["market_value"] for h in holdings)
    total_cost = sum(
        h["market_value"] / (1 + h["pnl_pct"] / 100)
        for h in holdings if h["pnl_pct"] is not None
    )
    total_pnl_pct = round((total_mv - total_cost) / total_cost * 100, 2) if total_cost else 0

    top3 = [dict(h) for h in holdings[:3]]

    by_class: dict[str, float] = {}
    for h in holdings:
        by_class[h["asset_class"]] = by_class.get(h["asset_class"], 0) + h["weight_pct"]

    alerts_count = con.execute(
        "SELECT COUNT(*) FROM trades WHERE status='FLAGGED'"
    ).fetchone()[0]

    con.close()
    return {
        "total_aum":           round(total_mv, 0),
        "portfolio_return":    total_pnl_pct,
        "top_contributors":    top3,
        "asset_class_weights": {k: round(v, 1) for k, v in by_class.items()},
        "flagged_trades":      alerts_count,
        "data_scope":          get_data_scope(),
        "data_scope_note":     data_scope_note(),
    }


def generate_investor_letter(
    user:        User,
    period:      str = "monthly",        # monthly | quarterly
    tier:        str = "INSTITUTIONAL",  # INSTITUTIONAL | HNW | FAMILY_OFFICE
    investor_name: str = "Valued Investor",
    model:       Optional[str] = None,
    db_path:     Optional[str] = None,
) -> dict:
    """
    Generate a personalised investor letter draft.
    Required role: risk.

    Returns:
        { allowed, letter_sections, full_letter, period, tier,
          generated_at, compliance_gate }
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "generate_investor_letter", perm.allowed,
        question=f"period={period} tier={tier}",
        deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    path = db_path or get_db_path()
    period_label = {"monthly": "Monthly", "quarterly": "Quarterly"}.get(period, "Periodic")
    date_str = datetime.now(timezone.utc).strftime("%B %Y")

    portfolio_ctx = _gather_portfolio_context(path)

    # Tier-specific tone instructions
    tone_map = {
        "INSTITUTIONAL": "formal, data-driven, precise — suitable for pension funds and endowments",
        "HNW":           "warm but professional — focus on wealth preservation and absolute returns",
        "FAMILY_OFFICE": "relationship-focused, holistic — mention long-term alignment of interests",
        "SOVEREIGN":     "highly formal — emphasise governance, compliance, and risk management",
    }
    tone = tone_map.get(tier, tone_map["INSTITUTIONAL"])

    context = json.dumps({
        "period":           period_label,
        "reporting_date":   date_str,
        "investor_name":    investor_name,
        "investor_tier":    tier,
        "tone_guidance":    tone,
        "data_scope":       portfolio_ctx.get("data_scope"),
        "data_scope_note":  portfolio_ctx.get("data_scope_note"),
        "portfolio_data":   portfolio_ctx,
    }, indent=2)

    raw = llm.chat(
        user_message=f"Draft the {period_label} investor letter for {investor_name}.",
        context_data=context,
        system_prompt=LETTER_PROMPT,
        model=model,
        audit_ctx=llm.LlmAuditContext(
            user.email, user.role, "investor_letter",
            question_hint=f"{period_label}/{tier}",
        ),
        db_path=path,
    )

    # Parse JSON response
    sections: dict = {}
    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        sections = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        # Fallback: wrap raw text as performance section
        sections = {
            "subject_line":        f"ARP Global Capital — {period_label} Update {date_str}",
            "salutation":          f"Dear {investor_name},",
            "performance_section": raw,
            "market_commentary":   "[Market commentary to be added by portfolio team]",
            "positioning_section": "[Positioning summary to be added]",
            "outlook_section":     "[Outlook to be added]",
            "closing":             "We thank you for your continued confidence in ARP Global Capital.",
            "disclaimer":          "This letter is for informational purposes only.",
            "word_count_estimate": len(raw.split()),
        }

    # Assemble full letter text
    full_letter = "\n\n".join(filter(None, [
        f"**{sections.get('subject_line','')}**",
        sections.get("salutation", ""),
        sections.get("performance_section", ""),
        sections.get("market_commentary", ""),
        sections.get("positioning_section", ""),
        sections.get("outlook_section", ""),
        sections.get("closing", ""),
        "---",
        sections.get("disclaimer", ""),
    ]))

    return {
        "allowed":         True,
        "letter_sections": sections,
        "full_letter":     full_letter,
        "period":          period_label,
        "tier":            tier,
        "investor_name":   investor_name,
        "generated_at":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "data_scope":      portfolio_ctx.get("data_scope"),
        "data_scope_note": portfolio_ctx.get("data_scope_note"),
        "portfolio_snapshot": portfolio_ctx,
        "compliance_gate": (
            "DRAFT ONLY. This letter requires CIO + Compliance approval before "
            "transmission per DFSA GEN Rule 5.3 and ARP sign-off matrix. "
            "Do not send directly."
        ),
        "salesforce_note": (
            "Structured JSON available in letter_sections for "
            "auto-population into Salesforce CRM fields."
        ),
    }
