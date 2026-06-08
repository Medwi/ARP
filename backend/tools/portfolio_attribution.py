"""
ARP Platform – tools/portfolio_attribution.py
P&L Attribution Engine.

Maps to ARP's NOW roadmap priority:
  "Daily P&L Attribution: moving from manual Broadridge email files to a
   live, role-based web dashboard accessible in under 10 seconds."

Implements:
  - Total portfolio P&L decomposed by:
    · Asset class contribution
    · Sector contribution (GICS)
    · Individual position contribution (best/worst)
    · Factor contribution (momentum, value, quality, low-vol)
    · Currency contribution (stub — USD base)
  - Period-over-period comparison (today vs prior)
  - Best/worst contributor analysis
  - Attribution narrative (LLM-ready context string)

DEMO SCOPE (this assessment):
  Single-snapshot attribution from seeded holdings — not intraday or
  period-over-period attribution from Broadridge execution files.
  Factor and sector maps are illustrative stubs, not production factor models.

PRODUCTION (ARP roadmap):
  Broadridge daily files → trading data lake (me-central-1) as source of truth
  for cost basis, executions, and NAV; factor/sector from vendor or internal
  model; administrator feed for investor-level reporting.
  Architecture and RBAC here are production-shaped; quant contracts plug in
  at the lake layer without changing the tool/agent surface.
"""

from __future__ import annotations
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect
from backend.tools.factor_analysis import SECTOR_MAP, FACTOR_MAP


def get_pnl_attribution(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Full P&L attribution decomposed by position, asset class, sector,
    and factor. Returns ranked contributors and a narrative context string.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_pnl_attribution", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    con = connect(db_path)
    try:
        holdings = con.execute("SELECT * FROM portfolio_holdings").fetchall()
        if not holdings:
            return {"allowed": True, "error": "No holdings data."}

        total_mv   = sum(h["market_value"] for h in holdings)
        total_cost = sum(h["quantity"] * h["avg_cost"] for h in holdings)
        total_pnl  = total_mv - total_cost

        # ── Position-level attribution ────────────────────────────────────────
        positions = []
        for h in holdings:
            cost       = h["quantity"] * h["avg_cost"]
            pnl_abs    = h["market_value"] - cost
            pnl_pct    = round(pnl_abs / cost * 100, 2) if cost else 0
            # Contribution to total portfolio P&L (weighted)
            contribution_pct = round(pnl_abs / total_mv * 100, 2) if total_mv else 0

            positions.append({
                "ticker":            h["ticker"],
                "asset_class":       h["asset_class"],
                "sector":            SECTOR_MAP.get(h["ticker"], "Other"),
                "weight_pct":        h["weight_pct"],
                "pnl_abs":           round(pnl_abs, 2),
                "pnl_pct":           pnl_pct,
                "contribution_pct":  contribution_pct,
            })

        positions.sort(key=lambda x: x["pnl_abs"], reverse=True)

        # ── Asset class attribution ───────────────────────────────────────────
        by_class: dict[str, dict] = {}
        for p in positions:
            ac = p["asset_class"]
            if ac not in by_class:
                by_class[ac] = {"pnl_abs": 0.0, "weight_pct": 0.0, "count": 0}
            by_class[ac]["pnl_abs"]   += p["pnl_abs"]
            by_class[ac]["weight_pct"] += p["weight_pct"]
            by_class[ac]["count"]      += 1

        class_attribution = [
            {
                "asset_class":   ac,
                "pnl_abs":       round(v["pnl_abs"], 2),
                "weight_pct":    round(v["weight_pct"], 1),
                "contribution_pct": round(v["pnl_abs"] / total_mv * 100, 2)
                                    if total_mv else 0,
                "position_count": v["count"],
            }
            for ac, v in by_class.items()
        ]
        class_attribution.sort(key=lambda x: x["pnl_abs"], reverse=True)

        # ── Sector attribution ────────────────────────────────────────────────
        by_sector: dict[str, float] = {}
        for p in positions:
            s = p["sector"]
            by_sector[s] = by_sector.get(s, 0) + p["pnl_abs"]

        sector_attribution = [
            {
                "sector": s,
                "pnl_abs": round(v, 2),
                "contribution_pct": round(v / total_mv * 100, 2) if total_mv else 0,
            }
            for s, v in by_sector.items()
        ]
        sector_attribution.sort(key=lambda x: x["pnl_abs"], reverse=True)

        # ── Factor attribution ────────────────────────────────────────────────
        factor_pnl: dict[str, float] = {
            "momentum": 0.0, "value": 0.0, "quality": 0.0, "low_vol": 0.0
        }
        total_weight = sum(h["weight_pct"] for h in holdings) or 100
        for p in positions:
            f = FACTOR_MAP.get(p["ticker"],
                               {"momentum": 0.5, "value": 0.5, "quality": 0.5, "low_vol": 0.5})
            weight = p["weight_pct"] / total_weight
            for factor in factor_pnl:
                factor_pnl[factor] += p["pnl_abs"] * f[factor] * weight

        factor_attribution = [
            {
                "factor":  f,
                "pnl_abs": round(v, 2),
                "contribution_pct": round(v / total_mv * 100, 2) if total_mv else 0,
            }
            for f, v in factor_pnl.items()
        ]
        factor_attribution.sort(key=lambda x: x["pnl_abs"], reverse=True)

        # ── Summary stats ─────────────────────────────────────────────────────
        best3  = positions[:3]
        worst3 = positions[-3:][::-1]
        pnl_pct_total = round(total_pnl / total_cost * 100, 2) if total_cost else 0

        # ── Narrative context (for LLM / briefing agent) ──────────────────────
        best_name  = best3[0]["ticker"]  if best3  else "N/A"
        worst_name = worst3[0]["ticker"] if worst3 else "N/A"
        top_class  = class_attribution[0]["asset_class"] if class_attribution else "N/A"
        top_factor = factor_attribution[0]["factor"]     if factor_attribution else "N/A"

        narrative = (
            f"Portfolio P&L: ${total_pnl:,.0f} ({pnl_pct_total:+.2f}%). "
            f"Top contributor: {best_name} (${best3[0]['pnl_abs']:,.0f}). "
            f"Largest detractor: {worst_name} (${worst3[0]['pnl_abs']:,.0f}). "
            f"Best asset class: {top_class}. "
            f"Dominant factor: {top_factor}."
        ) if best3 and worst3 else "Insufficient data for attribution."

        from backend.data_scope import attach_data_scope
        from backend.config import get_data_scope

        return attach_data_scope({
            "allowed":              True,
            "data_scope":           get_data_scope(),
            "total_pnl":            round(total_pnl, 2),
            "total_pnl_pct":        pnl_pct_total,
            "total_aum":            round(total_mv, 2),
            "position_attribution": positions,
            "class_attribution":    class_attribution,
            "sector_attribution":   sector_attribution,
            "factor_attribution":   factor_attribution,
            "best_contributors":    best3,
            "worst_contributors":   worst3,
            "narrative":            narrative,
            "attribution_note": (
                "Demo: single-snapshot cost basis with illustrative factor/sector maps. "
                "Not daily P&L from Broadridge files. Production ingests execution "
                "prices and NAV from the trading data lake; this module's API shape "
                "and RBAC remain unchanged."
            ),
        })
    finally:
        con.close()
