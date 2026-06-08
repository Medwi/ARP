"""
ARP Platform – risk_tools.py
Tool functions for the Risk & Compliance Agent.
Each function: validates permission → queries DB → writes audit log → returns data.
"""

from __future__ import annotations
import sqlite3
from typing import Optional
from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect
from backend.risk_constants import (
    SINGLE_ISSUER_ASSET_CLASSES,
    WARNING_BAND_RATIO,
    ELEVATED_RISK_SCORE_THRESHOLD,
    default_threshold,
)


def _threshold(
    rules: list[sqlite3.Row],
    metric: str,
    rule_name: Optional[str] = None,
) -> float:
    """Read threshold from active risk_rules; fall back to mandate constants."""
    if rule_name:
        row = next((r for r in rules if r["rule_name"] == rule_name), None)
        if row is not None:
            return row["threshold"]
    row = next((r for r in rules if r["metric"] == metric), None)
    if row is not None:
        return row["threshold"]
    return default_threshold(metric, rule_name)

# ── Tool: get_risk_alerts ─────────────────────────────────────────────────────

def get_risk_alerts(user: User, question: str = "", db_path: Optional[str] = None) -> dict:
    """
    Evaluates active risk rules against current portfolio and trade data.
    Returns a list of triggered alerts with severity.
    Required role: risk (full detail) or manager (executive counts only via summary).
    """
    perm = check_permission(user, "risk_alerts")
    executive_only = False
    # COO/manager: counts only via summary permission — not granted to analyst
    if not perm.allowed and user.role == "manager":
        perm = check_permission(user, "summary")
        executive_only = perm.allowed
    log(user.email, user.role, "get_risk_alerts", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    alerts = []
    try:
        rules   = con.execute("SELECT * FROM risk_rules WHERE active=1").fetchall()
        holdings = con.execute("SELECT * FROM portfolio_holdings").fetchall()
        risk_score_thr = _threshold(rules, "risk_score", "high_risk_score")
        trades   = con.execute(
            "SELECT * FROM trades WHERE status='FLAGGED' OR risk_score > ?",
            (risk_score_thr,),
        ).fetchall()
        total_mv = sum(h["market_value"] for h in holdings) or 1

        for rule in rules:
            metric    = rule["metric"]
            threshold = rule["threshold"]
            triggered = False
            detail    = ""

            if metric == "weight_pct":
                # Single-issuer concentration: applies to single names and digital
                # assets only. Pooled funds (ETFs, bond funds, cash) are diversified
                # by construction and are excluded from the single-name limit.
                over = [
                    h for h in holdings
                    if h["asset_class"] in SINGLE_ISSUER_ASSET_CLASSES and h["weight_pct"] > threshold
                ]
                if over:
                    triggered = True
                    detail = ", ".join(f"{h['ticker']} ({h['weight_pct']:.1f}%)" for h in over)

            elif metric == "asset_class_pct":
                # Crypto specifically
                crypto_mv = sum(h["market_value"] for h in holdings if h["asset_class"] == "Crypto")
                crypto_pct = crypto_mv / total_mv * 100
                if crypto_pct > threshold:
                    triggered = True
                    detail = f"Crypto exposure: {crypto_pct:.1f}%"

            elif metric == "notional":
                # Individual trade notional
                large = [t for t in trades if t["notional"] > threshold]
                if large:
                    triggered = True
                    detail = f"{len(large)} trade(s) exceed ${threshold:,.0f} notional"

            elif metric == "risk_score":
                # Flagged trades
                flagged = [t for t in trades if t["risk_score"] > threshold]
                if flagged:
                    triggered = True
                    detail = f"{len(flagged)} trade(s) with risk score > {threshold}"

            elif metric == "top3_pct":
                sorted_h = sorted(holdings, key=lambda h: h["market_value"], reverse=True)
                top3_pct = sum(h["weight_pct"] for h in sorted_h[:3])
                if top3_pct > threshold:
                    triggered = True
                    detail = f"Top-3 concentration: {top3_pct:.1f}%"

            if triggered:
                alerts.append({
                    "rule":        rule["rule_name"],
                    "description": rule["description"],
                    "severity":    rule["severity"],
                    "detail":      detail,
                })

        if executive_only:
            by_severity: dict[str, int] = {
                "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0,
            }
            for a in alerts:
                sev = a.get("severity", "LOW")
                by_severity[sev] = by_severity.get(sev, 0) + 1
            return {
                "allowed":           True,
                "executive_summary": True,
                "alert_count":       len(alerts),
                "by_severity":       by_severity,
                "rules_checked":     len(rules),
            }
        return {
            "allowed":       True,
            "alerts":        alerts,
            "alert_count":   len(alerts),
            "rules_checked": len(rules),
        }
    finally:
        con.close()

# ── Tool: get_flagged_trades ──────────────────────────────────────────────────

def get_flagged_trades(user: User, question: str = "", db_path: Optional[str] = None) -> dict:
    """
    Returns all FLAGGED trades or trades with risk_score > 0.7 with explanations.
    Required role: risk (resource: trades)
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "get_flagged_trades", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        rules = con.execute("SELECT * FROM risk_rules WHERE active=1").fetchall()
        risk_thr = _threshold(rules, "risk_score", "high_risk_score")
        notional_thr = _threshold(rules, "notional", "large_notional_trade")
        rows = con.execute(
            """SELECT ticker, direction, quantity, price, notional,
                      trader, status, risk_score, notes, traded_at
               FROM trades
               WHERE status = 'FLAGGED' OR risk_score > ?
               ORDER BY risk_score DESC, traded_at DESC""",
            (ELEVATED_RISK_SCORE_THRESHOLD,),
        ).fetchall()

        trades = []
        for r in rows:
            t = dict(r)
            # Generate rule-based explanation
            reasons = []
            if t["risk_score"] > risk_thr:
                reasons.append(
                    f"risk score {t['risk_score']:.2f} exceeds critical threshold ({risk_thr:.2f})"
                )
            if t["notional"] > notional_thr:
                reasons.append(
                    f"notional ${t['notional']:,.0f} exceeds single-trade limit "
                    f"(USD {notional_thr:,.0f})"
                )
            if t["status"] == "FLAGGED":
                reasons.append("manually flagged for review")
            t["explanation"] = "; ".join(reasons) if reasons else "elevated risk score"
            trades.append(t)

        return {
            "allowed": True,
            "flagged_trades": trades,
            "count": len(trades),
        }
    finally:
        con.close()

# ── Tool: check_overexposure ──────────────────────────────────────────────────

def check_overexposure(user: User, question: str = "", db_path: Optional[str] = None) -> dict:
    """
    Checks whether any single asset or asset class exceeds exposure limits.
    Required role: risk (resource: risk_alerts)
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "check_overexposure", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        rules = con.execute("SELECT * FROM risk_rules WHERE active=1").fetchall()
        single_thr = _threshold(rules, "weight_pct", "max_single_position")
        crypto_thr = _threshold(rules, "asset_class_pct", "max_crypto_exposure")
        holdings = con.execute("SELECT * FROM portfolio_holdings ORDER BY weight_pct DESC").fetchall()
        total_mv = sum(h["market_value"] for h in holdings) or 1

        overweight = [
            {
                "ticker": h["ticker"],
                "asset_class": h["asset_class"],
                "weight_pct": h["weight_pct"],
                "limit_pct": single_thr,
                "breach_type": "max_single_position",
            }
            for h in holdings
            if h["asset_class"] in SINGLE_ISSUER_ASSET_CLASSES and h["weight_pct"] > single_thr
        ]

        by_class: dict[str, float] = {}
        for h in holdings:
            ac = h["asset_class"]
            by_class[ac] = by_class.get(ac, 0) + h["market_value"]
        class_pcts = {k: round(v / total_mv * 100, 2) for k, v in by_class.items()}

        crypto_pct = class_pcts.get("Crypto", 0.0)
        crypto_breach = crypto_pct > crypto_thr

        return {
            "allowed":             True,
            "overweight_positions": overweight,
            "asset_class_weights": class_pcts,
            "limits": {
                "max_single_position_pct": single_thr,
                "max_crypto_exposure_pct": crypto_thr,
            },
            "crypto_breach":       crypto_breach,
            "any_overexposed":     len(overweight) > 0 or crypto_breach,
        }
    finally:
        con.close()

# ── Tool: get_badge_metrics ───────────────────────────────────────────────────

def get_badge_metrics(user: User, db_path: Optional[str] = None) -> dict:
    """
    Returns the top-line risk badge strip for the dashboard header.
    Evaluates key thresholds on every load — surfaces breaches before
    anyone has to ask a question.
    Requires: risk_alerts permission (risk role).
    Falls back to a reduced badge set for analyst role (portfolio only).
    """
    # Analysts can see concentration badges, not the full risk suite
    perm = check_permission(user, "portfolio")
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason, "badges": []}

    can_see_risk = check_permission(user, "risk_alerts").allowed
    con = connect(db_path)

    try:
        rules = con.execute("SELECT * FROM risk_rules WHERE active=1").fetchall()
        single_thr = _threshold(rules, "weight_pct", "max_single_position")
        crypto_thr = _threshold(rules, "asset_class_pct", "max_crypto_exposure")
        risk_score_thr = _threshold(rules, "risk_score", "high_risk_score")
        single_warn = single_thr * WARNING_BAND_RATIO
        crypto_warn = crypto_thr * WARNING_BAND_RATIO

        holdings = con.execute("SELECT * FROM portfolio_holdings").fetchall()
        total_mv = sum(h["market_value"] for h in holdings) or 1

        # 1. Largest single-name position (single issuers only; pooled funds excluded)
        names = [h for h in holdings if h["asset_class"] in SINGLE_ISSUER_ASSET_CLASSES]
        top = max(names, key=lambda h: h["weight_pct"], default=None)
        top_pct  = top["weight_pct"] if top else 0.0
        top_tick = top["ticker"]     if top else "-"
        badges = [
            {
                "label":  "Top Single Name",
                "value":  f"{top_pct:.1f}%",
                "sub":    top_tick,
                "status": "BREACH"  if top_pct > single_thr else
                          "WARNING" if top_pct > single_warn else "OK",
            }
        ]

        # 2. Digital-asset exposure
        crypto_mv  = sum(h["market_value"] for h in holdings
                         if h["asset_class"] == "Crypto")
        crypto_pct = round(crypto_mv / total_mv * 100, 1)
        badges.append({
            "label":  "Digital Assets",
            "value":  f"{crypto_pct:.1f}%",
            "sub":    "of AUM",
            "status": "BREACH"  if crypto_pct > crypto_thr else
                      "WARNING" if crypto_pct > crypto_warn else "OK",
        })

        # 3. Flagged trades + risk alerts (risk role only)
        if can_see_risk:
            flagged = con.execute(
                "SELECT COUNT(*) n FROM trades WHERE status='FLAGGED' OR risk_score > ?",
                (risk_score_thr,),
            ).fetchone()["n"]
            badges.append({
                "label":  "Compliance Queue",
                "value":  str(flagged),
                "sub":    "trades pending review",
                "status": "BREACH"  if flagged > 2 else
                          "WARNING" if flagged > 0 else "OK",
            })

            # 4. Active rule breaches
            alerts_data = get_risk_alerts(user, db_path=db_path)
            breach_count = alerts_data.get("alert_count", 0)
            badges.append({
                "label":  "Rule Breaches",
                "value":  str(breach_count),
                "sub":    f"of {alerts_data.get('rules_checked', 0)} rules",
                "status": "BREACH"  if breach_count >= 3 else
                          "WARNING" if breach_count >= 1 else "OK",
            })

        return {"allowed": True, "badges": badges, "role": user.role}
    finally:
        con.close()

# ── Tool: check_pre_trade ─────────────────────────────────────────────────────

def check_pre_trade(
    user:      User,
    ticker:    str,
    direction: str,      # "BUY" | "SELL"
    quantity:  float,
    price:     float,
    db_path:   Optional[str] = None,
) -> dict:
    """
    Pre-trade compliance check: validates a proposed trade against active
    risk rules and portfolio concentration limits before it reaches execution.

    Maps directly to ARP's roadmap item: Trade Tracker & Shadow Book.
    Enforces the DFSA human-in-the-loop requirement — this tool flags,
    it never executes.

    Required role: risk (resource: trades + risk_alerts)
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "check_pre_trade", perm.allowed,
        f"{direction} {quantity} {ticker} @ {price}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        holdings  = con.execute("SELECT * FROM portfolio_holdings").fetchall()
        rules     = con.execute("SELECT * FROM risk_rules WHERE active=1").fetchall()
        total_mv  = sum(h["market_value"] for h in holdings) or 1

        notional   = round(quantity * price, 2)
        flags: list[dict] = []
        warnings:  list[dict] = []

        # ── Rule 1: Large notional ────────────────────────────────────────────
        notional_rule = next((r for r in rules if r["rule_name"] == "large_notional_trade"), None)
        threshold = _threshold(rules, "notional", "large_notional_trade")
        if notional > threshold:
            flags.append({
                "rule":     "large_notional_trade",
                "severity": notional_rule["severity"] if notional_rule else "MEDIUM",
                "detail":   f"Notional ${notional:,.0f} exceeds limit ${threshold:,.0f}. "
                            "Dual sign-off required.",
            })

        # ── Rule 2: Post-trade concentration ─────────────────────────────────
        existing = next((h for h in holdings if h["ticker"] == ticker), None)
        if direction == "BUY":
            new_mv     = (existing["market_value"] if existing else 0) + notional
            new_weight = round(new_mv / (total_mv + notional) * 100, 2)
            conc_rule  = next((r for r in rules if r["rule_name"] == "max_single_position"), None)
            conc_thresh = _threshold(rules, "weight_pct", "max_single_position")
            if new_weight > conc_thresh:
                flags.append({
                    "rule":     "max_single_position",
                    "severity": "HIGH",
                    "detail":   f"Post-trade {ticker} weight would be {new_weight:.1f}% "
                                f"(limit {conc_thresh:.0f}%). Breaches concentration mandate.",
                })
            elif new_weight > conc_thresh * 0.8:
                warnings.append({
                    "rule":   "max_single_position",
                    "detail": f"Post-trade {ticker} weight would be {new_weight:.1f}% — "
                              f"approaching {conc_thresh:.0f}% limit.",
                })
        else:
            new_weight = round(
                (existing["market_value"] - notional) / total_mv * 100, 2
            ) if existing else 0.0

        # ── Rule 3: Crypto mandate ────────────────────────────────────────────
        asset_class = existing["asset_class"] if existing else "Unknown"
        if asset_class == "Crypto" and direction == "BUY":
            crypto_mv  = sum(h["market_value"] for h in holdings if h["asset_class"] == "Crypto")
            new_crypto_pct = round((crypto_mv + notional) / (total_mv + notional) * 100, 2)
            crypto_rule = next((r for r in rules if r["rule_name"] == "max_crypto_exposure"), None)
            crypto_thresh = _threshold(rules, "asset_class_pct", "max_crypto_exposure")
            if new_crypto_pct > crypto_thresh:
                flags.append({
                    "rule":     "max_crypto_exposure",
                    "severity": "HIGH",
                    "detail":   f"Post-trade crypto exposure would be {new_crypto_pct:.1f}% "
                                f"(mandate limit {crypto_thresh:.0f}%).",
                })

        # ── Verdict ───────────────────────────────────────────────────────────
        verdict = "BLOCKED" if flags else ("WARNING" if warnings else "CLEAR")

        return {
            "allowed":      True,
            "verdict":      verdict,
            "ticker":       ticker,
            "direction":    direction,
            "quantity":     quantity,
            "price":        price,
            "notional":     notional,
            "asset_class":  asset_class,
            "post_trade_weight": new_weight if direction == "BUY" else None,
            "flags":        flags,
            "warnings":     warnings,
            "hitl_note": (
                "This tool flags — it does not execute. "
                "All trades require human approval per DFSA two-person confirmation rule."
            ),
        }
    finally:
        con.close()

# ── Tool: detect_audit_anomalies ──────────────────────────────────────────────

def detect_audit_anomalies(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Analyses audit_logs for insider-threat indicators:
      - After-hours access (outside 07:00–19:00 UTC)
      - Burst queries: >10 requests within any 5-minute window
      - Repeated denied attempts: >3 denials from one user in 24h
      - Unusual tool usage: intern or manager calling data tools

    Returns flagged sessions with severity and recommended action.
    Required role: risk (resource: risk_alerts)
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "detect_audit_anomalies",
        perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    anomalies: list[dict] = []

    try:
        rows = con.execute(
            "SELECT user_email, role, tool_called, allowed, timestamp "
            "FROM audit_logs ORDER BY timestamp ASC"
        ).fetchall()

        if not rows:
            return {"allowed": True, "anomalies": [], "records_checked": 0}

        # ── After-hours access ────────────────────────────────────────────────
        for r in rows:
            try:
                hour = int(r["timestamp"][11:13])   # HH from "YYYY-MM-DD HH:MM:SS"
                if hour < 7 or hour >= 19:
                    anomalies.append({
                        "type":     "AFTER_HOURS_ACCESS",
                        "severity": "MEDIUM",
                        "user":     r["user_email"],
                        "detail":   f"{r['user_email']} ({r['role']}) called "
                                    f"`{r['tool_called']}` at {r['timestamp']} UTC "
                                    f"(outside 07:00–19:00 window).",
                        "action":   "Review session; confirm user intent.",
                    })
            except (ValueError, IndexError):
                pass

        # ── Burst queries: >10 in 5-minute window ────────────────────────────
        from collections import defaultdict
        user_times: defaultdict[str, list[str]] = defaultdict(list)
        for r in rows:
            user_times[r["user_email"]].append(r["timestamp"])

        for email, times in user_times.items():
            times_sorted = sorted(times)
            for i, t_start in enumerate(times_sorted):
                window = [t for t in times_sorted[i:]
                          if _minutes_between(t_start, t) <= 5]
                if len(window) > 10:
                    anomalies.append({
                        "type":     "BURST_QUERY",
                        "severity": "HIGH",
                        "user":     email,
                        "detail":   f"{email} made {len(window)} requests within 5 minutes "
                                    f"starting at {t_start}. Possible automated scraping.",
                        "action":   "Verify user identity; consider token revocation.",
                    })
                    break  # one alert per user per scan

        # ── Repeated denials: >3 in 24h ───────────────────────────────────────
        denied_counts: defaultdict[str, int] = defaultdict(int)
        for r in rows:
            if r["allowed"] == 0:
                denied_counts[r["user_email"]] += 1
        for email, count in denied_counts.items():
            if count > 3:
                anomalies.append({
                    "type":     "REPEATED_DENIALS",
                    "severity": "MEDIUM",
                    "user":     email,
                    "detail":   f"{email} has {count} access denials logged. "
                                "Possible privilege escalation attempt.",
                    "action":   "Review denied requests; confirm role assignment.",
                })

        # Deduplicate (same type + user)
        seen: set[tuple] = set()
        unique: list[dict] = []
        for a in anomalies:
            key = (a["type"], a["user"])
            if key not in seen:
                seen.add(key)
                unique.append(a)

        return {
            "allowed":         True,
            "anomalies":       unique,
            "anomaly_count":   len(unique),
            "records_checked": len(rows),
        }
    finally:
        con.close()


def _minutes_between(t1: str, t2: str) -> float:
    """Return minutes between two 'YYYY-MM-DD HH:MM:SS' strings."""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        from datetime import datetime
        d1 = datetime.strptime(t1, fmt)
        d2 = datetime.strptime(t2, fmt)
        return abs((d2 - d1).total_seconds()) / 60
    except Exception:
        return 999
