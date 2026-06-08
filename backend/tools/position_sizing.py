"""
ARP Platform – tools/position_sizing.py
Position Sizing Engine.

Supports ARP's trade construction workflow with quantitative sizing models:
  - Kelly Criterion (fractional Kelly for risk management)
  - Volatility-adjusted sizing (position = risk_budget / volatility)
  - Max drawdown constraint sizing
  - Portfolio-level concentration check (post-sizing)

From ARP's LATER roadmap:
  "Convexity-Optimised Trade Construction AI: recommending the most convex
   trade expressions across asset classes while accounting for costs."

Position sizing is the companion to trade_optimizer.py:
  trade_optimizer.py  → WHAT instrument to use
  position_sizing.py  → HOW MUCH to put on

All outputs include the HITL note — no autonomous execution.
"""

from __future__ import annotations
import math
import os
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect
from backend.risk_constants import default_threshold

# Conservative Kelly fraction — full Kelly is too aggressive for most funds
DEFAULT_KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))

# Mandate single-name limit (8% default; override via MAX_POSITION_PCT env)
MAX_POSITION_PCT = float(os.getenv(
    "MAX_POSITION_PCT",
    str(default_threshold("weight_pct", "max_single_position")),
))


def kelly_size(
    user:          User,
    win_rate:      float,   # historical hit rate 0–1
    avg_win:       float,   # average winning return (e.g. 0.15 = 15%)
    avg_loss:      float,   # average losing return as positive number (e.g. 0.07 = 7%)
    kelly_fraction: float  = DEFAULT_KELLY_FRACTION,
    db_path:       Optional[str] = None,
) -> dict:
    """
    Compute the Kelly-optimal position size as % of AUM.
    Uses fractional Kelly (default 25%) for practical risk management.

    Kelly formula: f = (bp - q) / b
      where b = avg_win/avg_loss (odds), p = win_rate, q = 1-p

    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "kelly_size", perm.allowed,
        question=f"win_rate={win_rate} avg_win={avg_win} avg_loss={avg_loss}",
        deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    if not 0 < win_rate < 1:
        return {"allowed": True, "error": "win_rate must be between 0 and 1"}
    if avg_win <= 0 or avg_loss <= 0:
        return {"allowed": True, "error": "avg_win and avg_loss must be positive"}

    # Kelly formula
    b = avg_win / avg_loss          # payoff ratio
    p = win_rate
    q = 1 - p
    full_kelly_pct = max((b * p - q) / b * 100, 0)  # never negative
    frac_kelly_pct = round(full_kelly_pct * kelly_fraction, 2)

    # Cap at MAX_POSITION_PCT
    capped = frac_kelly_pct > MAX_POSITION_PCT
    final_pct = min(frac_kelly_pct, MAX_POSITION_PCT)

    # Convert to dollar amount against current AUM
    con = connect(db_path)
    aum = con.execute("SELECT SUM(market_value) FROM portfolio_holdings").fetchone()[0] or 0
    con.close()

    position_usd = round(aum * final_pct / 100, 0)

    # Edge ratio (expected value per dollar risked)
    edge_ratio = round((p * avg_win - q * avg_loss), 4)

    return {
        "allowed":          True,
        "model":            "Fractional Kelly",
        "full_kelly_pct":   round(full_kelly_pct, 2),
        "kelly_fraction":   kelly_fraction,
        "recommended_pct":  final_pct,
        "capped_at_limit":  capped,
        "max_position_pct": MAX_POSITION_PCT,
        "position_usd":     position_usd,
        "aum":              round(aum, 0),
        "edge_ratio":       edge_ratio,
        "inputs": {
            "win_rate":  win_rate,
            "avg_win":   avg_win,
            "avg_loss":  avg_loss,
            "payoff_ratio": round(b, 3),
        },
        "interpretation": (
            f"Kelly suggests {final_pct:.1f}% of AUM (${position_usd:,.0f}). "
            f"Edge ratio: {edge_ratio:+.4f} — "
            f"{'positive edge, trade has merit' if edge_ratio > 0 else 'negative edge, do not trade'}."
            + (" Position capped at concentration limit." if capped else "")
        ),
        "hitl_note": "Position size is advisory. Requires trader approval before execution.",
    }


def volatility_size(
    user:          User,
    ticker:        str,
    annual_vol:    float,  # annualised volatility e.g. 0.30 = 30%
    risk_budget_pct: float = 1.0,  # % of AUM to risk on this position
    db_path:       Optional[str] = None,
) -> dict:
    """
    Volatility-adjusted position sizing.
    Position = risk_budget / volatility, capped at MAX_POSITION_PCT.

    Intuition: a more volatile asset gets a smaller position for the same
    dollar risk. A 30% vol stock gets half the position of a 15% vol stock
    for the same risk budget.

    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "volatility_size", perm.allowed,
        question=f"ticker={ticker} vol={annual_vol} budget={risk_budget_pct}%",
        deny_reason=perm.deny_reason, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    if annual_vol <= 0:
        return {"allowed": True, "error": "annual_vol must be positive"}
    if not 0 < risk_budget_pct <= 20:
        return {"allowed": True, "error": "risk_budget_pct must be 0–20%"}

    con = connect(db_path)
    aum = con.execute("SELECT SUM(market_value) FROM portfolio_holdings").fetchone()[0] or 0

    # Current position in this ticker
    existing = con.execute(
        "SELECT market_value, weight_pct FROM portfolio_holdings WHERE ticker=?",
        (ticker.upper(),)
    ).fetchone()
    con.close()

    current_weight = existing["weight_pct"] if existing else 0.0

    # Sizing: target weight = risk_budget / volatility
    raw_pct   = round(risk_budget_pct / annual_vol * 100, 2) / 100  # as decimal
    target_pct = min(raw_pct * 100, MAX_POSITION_PCT)
    capped     = raw_pct * 100 > MAX_POSITION_PCT
    position_usd = round(aum * target_pct / 100, 0)

    # 1-day VaR at 95% (normal approximation)
    daily_vol  = annual_vol / math.sqrt(252)
    var_95_pct = round(1.645 * daily_vol * target_pct, 3)
    var_95_usd = round(aum * var_95_pct / 100, 0)

    return {
        "allowed":           True,
        "model":             "Volatility-adjusted sizing",
        "ticker":            ticker.upper(),
        "annual_vol":        annual_vol,
        "risk_budget_pct":   risk_budget_pct,
        "recommended_pct":   round(target_pct, 2),
        "capped_at_limit":   capped,
        "max_position_pct":  MAX_POSITION_PCT,
        "position_usd":      position_usd,
        "current_weight_pct": round(current_weight, 2),
        "size_change_pct":   round(target_pct - current_weight, 2),
        "var_95_pct":        var_95_pct,
        "var_95_usd":        var_95_usd,
        "aum":               round(aum, 0),
        "interpretation": (
            f"For {annual_vol*100:.0f}% annual vol with {risk_budget_pct:.1f}% risk budget: "
            f"target {target_pct:.1f}% AUM (${position_usd:,.0f}). "
            f"1-day VaR (95%): ${var_95_usd:,.0f} ({var_95_pct:.2f}% AUM)."
            + (" Capped at concentration limit." if capped else "")
        ),
        "hitl_note": "Position size is advisory. Requires trader approval before execution.",
    }


def portfolio_risk_budget(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Show how current positions consume the total risk budget.
    Risk budget = MAX_POSITION_PCT × number of positions.
    Identifies positions that are over/under-sized relative to their vol.

    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "portfolio_risk_budget", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    from backend.tools.factor_analysis import FACTOR_MAP

    con = connect(db_path)
    holdings = con.execute("SELECT * FROM portfolio_holdings").fetchall()
    aum = sum(h["market_value"] for h in holdings) or 1
    con.close()

    # Proxy volatility from factor scores (low_vol factor → lower vol)
    # Real: use 252-day rolling std of daily returns from Broadridge
    budget_items = []
    total_risk_used = 0.0
    for h in holdings:
        f = FACTOR_MAP.get(h["ticker"], {"low_vol": 0.5})
        # Map low_vol score (0=high vol, 1=low vol) to annual volatility proxy
        # low_vol=0.1 → ~55% vol; low_vol=0.9 → ~12% vol
        vol_proxy = round(0.60 - f["low_vol"] * 0.50, 3)
        vol_proxy = max(vol_proxy, 0.08)  # floor at 8%

        # Ideal weight given 1% risk budget per position
        ideal_pct  = round(min(1.0 / vol_proxy * 100, MAX_POSITION_PCT), 2)
        actual_pct = h["weight_pct"]
        risk_used  = round(actual_pct * vol_proxy, 3)
        total_risk_used += risk_used

        budget_items.append({
            "ticker":       h["ticker"],
            "actual_pct":   actual_pct,
            "ideal_pct":    ideal_pct,
            "vol_proxy":    round(vol_proxy * 100, 1),
            "risk_used":    round(risk_used, 3),
            "sizing_signal": (
                "OVERWEIGHT" if actual_pct > ideal_pct * 1.2 else
                "UNDERWEIGHT" if actual_pct < ideal_pct * 0.8 else
                "SIZED"
            ),
        })

    budget_items.sort(key=lambda x: x["risk_used"], reverse=True)

    overweight  = [b for b in budget_items if b["sizing_signal"] == "OVERWEIGHT"]
    underweight = [b for b in budget_items if b["sizing_signal"] == "UNDERWEIGHT"]

    return {
        "allowed":          True,
        "total_positions":  len(holdings),
        "total_risk_used":  round(total_risk_used, 3),
        "max_risk_budget":  round(MAX_POSITION_PCT * len(holdings) / 100, 3),
        "overweight":       overweight,
        "underweight":      underweight,
        "positions":        budget_items,
        "note": (
            "Volatility proxied from factor scores. "
            "Production uses 252-day realised vol from Broadridge daily returns."
        ),
    }
