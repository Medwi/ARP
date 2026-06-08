"""
ARP Platform – agents/cio_digest.py
CIO Morning Digest Agent.

Internal operations control tower — distinct from the external overnight briefing.
Aggregates P&L attribution, risk queue, shadow book, data pipeline, CRM, and
regulatory deadlines. No market essay and no inbox (those are in briefing.py).

In production: scheduled at 03:30 AM Dubai time (UTC+4), after data lake sync.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend import llm
from backend.config import get_db_path

CIO_DIGEST_PROMPT = """You are preparing the INTERNAL CIO Morning Digest for Yusuf Alireza at ARP Global Capital.
This is the operations control tower — NOT the external overnight market/inbox briefing.

The CIO already has (or will read separately) overnight markets and email highlights.
Do NOT write a market essay. Do NOT summarise the inbox. At most one sentence of market
context only if needed to explain a P&L or risk item.

Use ONLY the data in context. Structure your response with these exact section headers:

=== DATA TRUST & PIPELINE ===
[Pipeline health, minutes since last sync, whether book data is trustworthy for decisions today.]

=== P&L ATTRIBUTION ===
[3–4 sentences: total P&L, best and worst contributors by name and $, asset-class and sector
 drivers from attribution data. Use the narrative field if present.]

=== RISK & COMPLIANCE QUEUE ===
[Bullet list: each open alert and flagged trade with ticker, status, and required action.
 If clear: state rules checked count and "no open breaches".]

=== SHADOW BOOK ===
[Open ideas: count, top conviction idea with thesis snippet, any ideas awaiting resolution.]

=== INVESTOR & LP ACTIONS ===
[Overdue follow-ups, KYC alerts, pending flows — specific names and due dates from context.]

=== REGULATORY & ADMIN ===
[DFSA test status, urgent filings, management accounts items — be specific.]

=== DECISIONS REQUIRED TODAY ===
[Numbered list, max 5: sign-offs, pipeline fixes, LP callbacks, risk remediations.
 Rank by business impact. Name tickers, amounts, deadlines.]

Rules: Operations tone. No macro commentary. No inbox. Numbers when material.
Flag DFSA sign-off items explicitly. Under 3 minutes to read."""


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": str(e), "allowed": False}


def _resolve_data_freshness(data_sources: dict) -> str:
    from backend.data_scope import book_freshness_label

    pipeline = data_sources.get("pipeline", {})
    health = pipeline.get("pipeline_health") or pipeline.get("health", "UNKNOWN")
    return book_freshness_label(health)


def run(
    user:    User,
    db_path: Optional[str] = None,
    model:   Optional[str] = None,
) -> dict:
    """
    Generate the internal CIO morning digest.
    Requires risk role.
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "cio_digest", perm.allowed,
        question="CIO morning digest request",
        deny_reason=perm.deny_reason, db_path=db_path)

    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    path = db_path or get_db_path()
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    data_sources: dict = {
        "generated_at": ts,
        "digest_focus": "internal_operations — attribution, risk, pipeline, CRM, compliance (not markets/inbox)",
    }

    try:
        from backend.tools.portfolio_attribution import get_pnl_attribution
        attr = _safe_call(get_pnl_attribution, user, db_path=path)
        if attr.get("allowed"):
            data_sources["attribution"] = {
                "total_pnl":          attr.get("total_pnl"),
                "total_pnl_pct":      attr.get("total_pnl_pct"),
                "total_aum":          attr.get("total_aum"),
                "data_scope":         attr.get("data_scope", ""),
                "narrative":          attr.get("narrative", ""),
                "attribution_note":   attr.get("attribution_note", ""),
                "best_contributors":  attr.get("best_contributors", [])[:3],
                "worst_contributors": attr.get("worst_contributors", [])[:3],
                "class_attribution":  attr.get("class_attribution", [])[:5],
                "sector_attribution": attr.get("sector_attribution", [])[:5],
            }
    except Exception:
        pass

    try:
        from backend.tools.risk_tools import get_risk_alerts, get_flagged_trades
        alerts  = _safe_call(get_risk_alerts, user, db_path=path)
        flagged = _safe_call(get_flagged_trades, user, db_path=path)
        if alerts.get("allowed"):
            data_sources["risk"] = {
                "alert_count":    alerts.get("alert_count", 0),
                "rules_checked":  alerts.get("rules_checked", 0),
                "alerts":         alerts.get("alerts", []),
                "by_severity":    alerts.get("by_severity", {}),
            }
        if flagged.get("allowed"):
            data_sources["flagged_trades"] = {
                "count":  flagged.get("count", 0),
                "trades": flagged.get("flagged_trades", [])[:8],
            }
    except Exception:
        pass

    try:
        from backend.tools.shadow_book import get_shadow_book, get_shadow_book_report
        ideas = _safe_call(get_shadow_book, user, status="OPEN", db_path=path)
        report = _safe_call(get_shadow_book_report, user, db_path=path)
        if ideas.get("allowed"):
            open_ideas = ideas.get("ideas", [])
            data_sources["shadow_book"] = {
                "open_count": len(open_ideas),
                "ideas":      open_ideas[:6],
                "report":     report if report.get("allowed") else {},
            }
    except Exception:
        pass

    try:
        from backend.tools.broadridge_pipeline import get_pipeline_status
        pipeline = _safe_call(get_pipeline_status, user, db_path=path)
        if pipeline.get("allowed"):
            data_sources["pipeline"] = {
                "health":               pipeline.get("health"),
                "minutes_since_sync":   pipeline.get("minutes_since_sync"),
                "last_status":          pipeline.get("last_status"),
                "sync_interval_target": pipeline.get("sync_interval_target"),
                "trade_records":        pipeline.get("trade_records"),
                "price_records":        pipeline.get("price_records"),
                "error_rate_pct":       pipeline.get("error_rate_pct"),
                "recent_runs":          pipeline.get("recent_runs", [])[:3],
            }
    except Exception:
        pass

    try:
        from backend.tools.crm_integration import get_investor_pipeline
        crm = _safe_call(get_investor_pipeline, user, db_path=path)
        if crm.get("allowed"):
            data_sources["investors"] = {
                "contact_count":      crm.get("contact_count", 0),
                "overdue_count":      crm.get("overdue_count", 0),
                "overdue_followups":  crm.get("overdue_followups", [])[:5],
                "kyc_alerts":         crm.get("kyc_alerts", [])[:5],
                "pending_flows":      crm.get("pending_flows", []),
                "total_aum_committed": crm.get("total_aum_committed"),
            }
    except Exception:
        pass

    try:
        from backend.tools.manager_reporting import get_manager_accounts
        mgr = _safe_call(get_manager_accounts, user, db_path=path)
        if mgr.get("allowed"):
            data_sources["compliance"] = {
                "all_tests_passed": mgr.get("all_tests_passed"),
                "aum":              mgr.get("aum"),
                "urgent_filings":     mgr.get("urgent_filings", []),
                "upcoming_filings":   mgr.get("upcoming_filings", [])[:4],
                "dfsa_tests":         mgr.get("dfsa_tests", {}),
                "revenue_monthly":    mgr.get("total_revenue_monthly"),
            }
    except Exception:
        pass

    context = json.dumps(data_sources, indent=2, default=str)
    digest = llm.chat(
        user_message=(
            "Generate the internal CIO morning digest: operations, attribution, risk queue, "
            "pipeline trust, investors, and compliance. No market overview and no inbox."
        ),
        context_data=context,
        system_prompt=CIO_DIGEST_PROMPT,
        model=model or llm.active_model(),
        audit_ctx=llm.LlmAuditContext(user.email, user.role, "cio_digest"),
        db_path=path,
    )

    pipeline_health = data_sources.get("pipeline", {}).get("health", "UNKNOWN")
    freshness = _resolve_data_freshness(data_sources)

    return {
        "allowed":           True,
        "digest":            digest,
        "digest_type":       "internal_operations",
        "data_sources":      [k for k in data_sources if k not in ("generated_at", "digest_focus")],
        "generated_at":      ts,
        "data_freshness":    freshness,
        "pipeline_health":   pipeline_health,
        "risk_alert_count":  data_sources.get("risk", {}).get("alert_count", 0),
        "overdue_investors": data_sources.get("investors", {}).get("overdue_count", 0),
        "tool_called":       "cio_digest",
        "sections": [
            "DATA TRUST & PIPELINE",
            "P&L ATTRIBUTION",
            "RISK & COMPLIANCE QUEUE",
            "SHADOW BOOK",
            "INVESTOR & LP ACTIONS",
            "REGULATORY & ADMIN",
            "DECISIONS REQUIRED TODAY",
        ],
        "raw_data": data_sources,
    }
