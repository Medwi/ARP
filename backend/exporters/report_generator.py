"""
ARP Platform – exporters/report_generator.py
Compliance Report Generator.

Produces timestamped compliance evidence packs in Markdown or CSV.
Designed for DFSA desk-based reviews and internal governance audits.
Required role: risk.
"""

from __future__ import annotations
import csv, io, os
from datetime import datetime, timezone
from typing import Optional

from backend.rbac import User, check_permission, PERMISSIONS
from backend.audit import recent_logs, verify_chain, log
from backend.config import get_db_path
from backend.db import connect


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def generate_report(
    user:    User,
    db_path: Optional[str] = None,
    format:  str = "markdown",
) -> dict:
    """Generate a full compliance evidence pack. format: 'markdown' | 'csv'."""
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "generate_compliance_report",
        perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    path = db_path or get_db_path()
    ts   = _now_utc()
    date = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

    con = connect(path)

    audit_logs = recent_logs(limit=500, db_path=path)
    holdings = [dict(r) for r in con.execute(
        "SELECT ticker, asset_class, market_value, weight_pct, "
        "ROUND((current_price-avg_cost)/avg_cost*100,2) as pnl_pct "
        "FROM portfolio_holdings ORDER BY weight_pct DESC"
    ).fetchall()]
    flagged = [dict(r) for r in con.execute(
        "SELECT ticker, direction, notional, risk_score, status, traded_at "
        "FROM trades WHERE status='FLAGGED' OR risk_score > 0.8 "
        "ORDER BY risk_score DESC LIMIT 20"
    ).fetchall()]
    con.close()

    from backend.tools.risk_tools import get_risk_alerts
    alerts = get_risk_alerts(user, db_path=path).get("alerts", [])
    chain  = verify_chain(db_path=path)

    from backend.platform_attestation import build_platform_attestation
    platform = build_platform_attestation()

    if format.lower() == "csv":
        content, filename = _build_csv(ts, audit_logs, holdings, flagged, alerts, chain, platform)
    else:
        content, filename = _build_markdown(
            ts, date, user, audit_logs, holdings, flagged, alerts, chain, platform
        )

    return {
        "allowed": True, "format": format, "content": content,
        "filename": filename, "generated_at": ts, "generated_by": user.email,
        "sections": ["Platform Attestation","Audit Log","Hash Chain Integrity",
                     "Risk Alerts","Flagged Trades","Portfolio","Access Control"],
        "record_count": len(audit_logs),
    }


def _build_markdown(ts, date, user, audit_logs, holdings, flagged,
                    alerts, chain, platform) -> tuple[str, str]:
    L = []
    def a(s=""): L.append(s)

    a("# ARP Global Capital — Compliance Evidence Pack")
    a(f"**Generated:** {ts}  "); a(f"**By:** {user.email} ({user.role})  ")
    a(f"**Classification:** INTERNAL — CONFIDENTIAL"); a()
    a("---"); a("## 1. Platform Attestation"); a()
    a("| Parameter | Value |"); a("|-----------|-------|")
    a(f"| Data residency | `{platform['data_residency']}` |")
    a(f"| External API calls | `{platform['external_calls']}` |")
    a(f"| LLM primary | `{platform['llm_primary']}` |")
    a(f"| LLM fallback | `{platform['llm_fallback']}` |")
    a(f"| LLM online | `{platform['llm_online']}` |"); a()
    a("**Attestation:** All inference on-device. No data transmitted externally. "
      "Compliant with DIFC DP Law No. 5 of 2020, Art. 24."); a()
    a("---"); a("## 2. Audit Log Extract")
    a(f"*{len(audit_logs)} records (max 500)*"); a()
    a("| Timestamp | User | Role | Tool | Decision | Hash |")
    a("|-----------|------|------|------|----------|------|")
    for e in audit_logs[:50]:
        hp = (e.get("hash_preview") or "")[:8] or "—"
        a(f"| {e['timestamp']} | {e['user_email']} | {e['role']} "
          f"| `{e['tool_called']}` | {e['result']} | `#{hp}…` |")
    if len(audit_logs) > 50:
        a(f"*… {len(audit_logs)-50} further records in CSV export*"); a()
    a("---"); a("## 3. Hash Chain Integrity"); a()
    status_str = "✅ INTACT" if chain["valid"] else "🚨 BREACH DETECTED"
    a(f"**Status:** {status_str}  "); a(f"**Records verified:** {chain['checked']}  ")
    a(f"**Message:** {chain['message']}"); a()
    a("---"); a("## 4. Active Risk Alerts"); a()
    if not alerts:
        a("✅ No active risk alerts.")
    else:
        a(f"⚠️ **{len(alerts)} alert(s) active**"); a()
        for alert in alerts:
            a(f"- **{alert['rule']}** ({alert['severity']}): {alert['detail']}")
    a(); a("---"); a("## 5. Flagged Trades"); a()
    if not flagged:
        a("✅ No flagged trades.")
    else:
        a("| Ticker | Dir | Notional | Risk Score | Status | Time |")
        a("|--------|-----|----------|------------|--------|------|")
        for t in flagged:
            a(f"| {t['ticker']} | {t['direction']} | ${t['notional']:,.0f} "
              f"| {t['risk_score']:.2f} | {t['status']} | {t['traded_at']} |")
    a(); a("---"); a("## 6. Portfolio Concentration"); a()
    a("| Ticker | Class | Mkt Value | Weight % | P&L % |")
    a("|--------|-------|-----------|----------|-------|")
    for h in holdings:
        flag = " ⚠️" if h["weight_pct"] > 20 else ""
        a(f"| {h['ticker']} | {h['asset_class']} | ${h['market_value']:,.0f} "
          f"| {h['weight_pct']:.1f}%{flag} | {h['pnl_pct']:+.2f}% |")
    a(); a("---"); a("## 7. Access Control Role Matrix"); a()
    a("| Role | Permitted Resources |"); a("|------|---------------------|")
    for role, perms in sorted(PERMISSIONS.items()):
        a(f"| `{role}` | {', '.join(sorted(perms)) or 'none'} |")
    a(); a("*RBAC enforced at tool level. All denials logged.*"); a()
    a("---"); a(f"*ARP Global Capital · {ts} · CONFIDENTIAL*")
    return "\n".join(L), f"arp_compliance_{date}.md"


def _build_csv(ts, audit_logs, holdings, flagged, alerts, chain, platform) -> tuple[str, str]:
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["SECTION","Platform Attestation"])
    w.writerow(["Generated", ts]); w.writerow(["Data Residency", platform["data_residency"]])
    w.writerow(["External Calls", platform["external_calls"]]); w.writerow([])
    w.writerow(["SECTION","Hash Chain"]); w.writerow(["Valid", chain["valid"]])
    w.writerow(["Records Checked", chain["checked"]]); w.writerow([])
    w.writerow(["SECTION","Audit Log"])
    w.writerow(["timestamp","user","role","tool","result","hash"])
    for e in audit_logs:
        w.writerow([e["timestamp"],e["user_email"],e["role"],
                    e["tool_called"],e["result"],e.get("hash_preview","")])
    w.writerow([]); w.writerow(["SECTION","Flagged Trades"])
    w.writerow(["ticker","dir","notional","risk_score","status","traded_at"])
    for t in flagged:
        w.writerow([t["ticker"],t["direction"],t["notional"],
                    t["risk_score"],t["status"],t["traded_at"]])
    w.writerow([]); w.writerow(["SECTION","Holdings"])
    w.writerow(["ticker","asset_class","market_value","weight_pct","pnl_pct"])
    for h in holdings:
        w.writerow([h["ticker"],h["asset_class"],h["market_value"],
                    h["weight_pct"],h["pnl_pct"]])
    date = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return buf.getvalue(), f"arp_compliance_{date}.csv"
