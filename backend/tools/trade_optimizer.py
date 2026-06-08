"""
ARP Platform – tools/trade_optimizer.py
Convexity-Optimised Trade Construction.

Maps to ARP's LATER (3 months+) roadmap priority:
  "Convexity-Optimised Trade Construction AI: recommending the most convex
   trade expressions across asset classes while accounting for costs."

From Yusuf Alireza's vision:
  "Trade Execution: AI proposes optimal instrument expressions (derivatives,
   CFD, cash) with cost-adjusted conviction scores."

This tool:
  - Takes a directional view (ticker + direction + conviction)
  - Proposes up to 3 trade expressions (cash equity, CFD, options)
  - Scores each on convexity (asymmetric upside/downside), cost, liquidity
  - Returns a ranked recommendation with cost-adjusted conviction score
  - Flags HITL requirement — no autonomous execution

Convexity definition used here:
  A trade expression is convex if potential upside significantly exceeds
  potential downside for a given notional commitment. Options are inherently
  convex (limited downside = premium paid). CFDs offer leverage with defined
  margin. Cash is linear (1:1 upside/downside).
"""

from __future__ import annotations
import os
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log

from backend.db import connect

# ── Instrument characteristics ────────────────────────────────────────────────
# (convexity_score, typical_cost_bps, liquidity, leverage, description)
INSTRUMENTS = {
    "cash_equity": {
        "convexity":    0.0,   # linear — 1:1 upside/downside
        "cost_bps":     5,     # commission + spread
        "liquidity":    "HIGH",
        "leverage":     1.0,
        "description":  "Direct share purchase/sale. Linear payoff. "
                        "Full capital commitment. No expiry.",
        "pros":         ["No expiry risk", "Full dividend/voting rights", "High liquidity"],
        "cons":         ["Linear P&L — no convexity", "Full capital at risk"],
    },
    "cfd": {
        "convexity":    0.2,   # slight convexity via leverage
        "cost_bps":     8,     # spread + overnight financing
        "liquidity":    "HIGH",
        "leverage":     5.0,
        "description":  "Contract for difference. Leveraged exposure without "
                        "share ownership. Overnight financing cost applies.",
        "pros":         ["Capital efficiency via leverage", "Short-selling straightforward",
                         "No stamp duty in most jurisdictions"],
        "cons":         ["Overnight financing erodes returns", "Margin call risk",
                         "No ownership rights"],
    },
    "call_option": {
        "convexity":    0.9,   # highly convex — limited downside (premium)
        "cost_bps":     25,    # premium + bid-ask spread
        "liquidity":    "MEDIUM",
        "leverage":     10.0,
        "description":  "Long call option. Limited downside (premium paid). "
                        "Significant upside if thesis plays out. Time decay applies.",
        "pros":         ["Maximum convexity — downside capped at premium",
                         "High leverage", "Defined risk"],
        "cons":         ["Time decay (theta)", "Premium cost reduces effective return",
                         "Requires liquid options market", "Expiry date constraint"],
    },
    "put_option": {
        "convexity":    0.9,
        "cost_bps":     25,
        "liquidity":    "MEDIUM",
        "leverage":     10.0,
        "description":  "Long put option for bearish expression. "
                        "Convex payoff — limited downside, uncapped upside.",
        "pros":         ["Convex payoff for bearish views", "Defined risk"],
        "cons":         ["Time decay", "Premium cost", "Expiry constraint"],
    },
    "spread_trade": {
        "convexity":    0.5,   # moderate convexity
        "cost_bps":     15,
        "liquidity":    "MEDIUM",
        "leverage":     2.0,
        "description":  "Long/short pair within sector. Captures relative value "
                        "while hedging market beta.",
        "pros":         ["Beta-neutral", "Exploits relative mispricing",
                         "Reduced market risk"],
        "cons":         ["Both legs must be right", "Higher transaction costs",
                         "Requires correlated pair"],
    },
}


def _cost_adjusted_conviction(conviction: int, cost_bps: float, leverage: float) -> float:
    """
    Adjust raw conviction (1-5) for transaction cost and leverage efficiency.
    conviction × (1 - cost_drag) × leverage_benefit
    """
    cost_drag       = min(cost_bps / 1000, 0.3)   # cap at 30% drag
    leverage_benefit = min(1 + (leverage - 1) * 0.1, 1.5)  # cap at 50% boost
    raw = conviction / 5.0
    adjusted = raw * (1 - cost_drag) * leverage_benefit
    return round(min(adjusted, 1.0), 3)


def optimize_trade(
    user:        User,
    ticker:      str,
    direction:   str,      # LONG | SHORT
    conviction:  int,      # 1–5
    notional:    float,    # target USD notional
    time_horizon: str = "medium",  # short | medium | long
    db_path:     Optional[str] = None,
) -> dict:
    """
    Recommend optimal trade expressions for a given directional view.
    Scores each instrument on convexity, cost, and liquidity, then
    produces a ranked shortlist with cost-adjusted conviction scores.

    Required role: risk.
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "optimize_trade", perm.allowed,
        f"{direction} {ticker} conviction={conviction} notional={notional}",
        perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return {"allowed": True, "error": "direction must be LONG or SHORT"}
    if not 1 <= conviction <= 5:
        return {"allowed": True, "error": "conviction must be 1–5"}
    if notional <= 0:
        return {"allowed": True, "error": "notional must be positive"}

    # ── Select relevant instruments ───────────────────────────────────────────
    if direction == "LONG":
        candidates = ["cash_equity", "cfd", "call_option", "spread_trade"]
    else:
        candidates = ["cfd", "put_option", "spread_trade"]

    # Time horizon adjustments
    horizon_weights = {
        "short":  {"convexity": 0.5, "cost": 0.3, "liquidity": 0.2},
        "medium": {"convexity": 0.4, "cost": 0.3, "liquidity": 0.3},
        "long":   {"convexity": 0.3, "cost": 0.2, "liquidity": 0.5},
    }
    weights = horizon_weights.get(time_horizon, horizon_weights["medium"])

    # ── Score each instrument ─────────────────────────────────────────────────
    recommendations = []
    for name in candidates:
        inst = INSTRUMENTS[name]

        # Composite score
        liq_score = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}.get(inst["liquidity"], 0.5)
        cost_score = 1 - min(inst["cost_bps"] / 100, 1.0)  # lower cost = higher score

        composite = (
            weights["convexity"] * inst["convexity"] +
            weights["cost"]      * cost_score +
            weights["liquidity"] * liq_score
        )

        # Capital required
        capital_required = round(notional / inst["leverage"], 0)
        cost_abs = round(notional * inst["cost_bps"] / 10000, 0)
        adj_conviction = _cost_adjusted_conviction(conviction, inst["cost_bps"], inst["leverage"])

        recommendations.append({
            "instrument":         name,
            "direction":          direction,
            "composite_score":    round(composite, 3),
            "convexity_score":    inst["convexity"],
            "cost_bps":           inst["cost_bps"],
            "cost_abs_usd":       cost_abs,
            "liquidity":          inst["liquidity"],
            "leverage":           inst["leverage"],
            "capital_required":   capital_required,
            "adj_conviction":     adj_conviction,
            "description":        inst["description"],
            "pros":               inst["pros"],
            "cons":               inst["cons"],
        })

    recommendations.sort(key=lambda x: x["composite_score"], reverse=True)

    top = recommendations[0]
    rationale = (
        f"For a {time_horizon}-horizon {direction} view on {ticker.upper()} with "
        f"conviction {conviction}/5, **{top['instrument'].replace('_',' ')}** is recommended. "
        f"Convexity score {top['convexity_score']:.1f}/1.0 with "
        f"cost-adjusted conviction {top['adj_conviction']:.2f}. "
        f"Capital required: ${top['capital_required']:,.0f} "
        f"(vs full notional ${notional:,.0f})."
    )

    return {
        "allowed":           True,
        "ticker":            ticker.upper(),
        "direction":         direction,
        "conviction":        conviction,
        "notional":          notional,
        "time_horizon":      time_horizon,
        "recommendations":   recommendations,
        "top_recommendation": top["instrument"],
        "rationale":         rationale,
        "hitl_note": (
            "This tool recommends — it does not execute. "
            "All trade expressions require human approval per DFSA "
            "two-person confirmation rule before execution."
        ),
    }


def score_existing_positions(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Score all current portfolio positions for expression efficiency.
    Identifies positions that could be expressed more convexly,
    freeing capital or improving risk/reward.
    Required role: risk.
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "score_existing_positions", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    import sqlite3
    con = connect(db_path)
    con.row_factory = sqlite3.Row
    holdings = con.execute(
        "SELECT ticker, asset_class, market_value, weight_pct, "
        "ROUND((current_price-avg_cost)/avg_cost*100,2) as pnl_pct "
        "FROM portfolio_holdings ORDER BY market_value DESC"
    ).fetchall()
    con.close()

    scored = []
    for h in holdings:
        ticker = h["ticker"]
        mv     = h["market_value"]

        # All current positions assumed to be cash equity (default)
        current_instrument = "cash_equity"
        current_convexity  = INSTRUMENTS["cash_equity"]["convexity"]

        # Would a CFD free up capital?
        cfd_capital = round(mv / INSTRUMENTS["cfd"]["leverage"], 0)
        capital_freed = round(mv - cfd_capital, 0)

        # Convexity improvement via options (for large positions)
        option_suggestion = None
        if mv > 1_000_000 and h["pnl_pct"] > 10:
            # Large winning position — consider collar or covered call
            option_suggestion = "Consider covered call to monetise gains while retaining upside."
        elif mv > 500_000 and h["weight_pct"] > 10:
            # Concentrated — consider protective put
            option_suggestion = "Consider protective put to hedge concentration risk."

        scored.append({
            "ticker":              ticker,
            "asset_class":         h["asset_class"],
            "market_value":        mv,
            "weight_pct":          h["weight_pct"],
            "pnl_pct":             h["pnl_pct"],
            "current_expression":  current_instrument,
            "current_convexity":   current_convexity,
            "cfd_capital_freed":   capital_freed,
            "option_suggestion":   option_suggestion,
        })

    improvement_count = sum(1 for s in scored if s.get("option_suggestion"))

    return {
        "allowed":           True,
        "positions_scored":  len(scored),
        "scored":            scored,
        "improvement_opportunities": improvement_count,
        "note": (
            "All positions currently expressed as cash equity. "
            "Capital efficiency analysis assumes CFD at 5x leverage. "
            "Options suggestions are indicative only."
        ),
    }
