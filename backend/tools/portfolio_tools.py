"""
ARP Platform – portfolio_tools.py
Tool functions for the Portfolio Analyst Agent.
Each function: validates permission → queries DB → writes audit log → returns data.
"""

from __future__ import annotations
from typing import Optional
from backend.data_scope import attach_data_scope
from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect
from backend.risk_model import (
    ASSET_CLASS_BETA,
    ASSET_CLASS_VOL,
    CROSS_CORRELATION,
    RISK_FREE_RATE,
    TRADING_DAYS,
    VAR_Z_95,
    VAR_Z_99,
)

# ── Tool: get_portfolio_summary ───────────────────────────────────────────────

def get_portfolio_summary(user: User, question: str = "", db_path: Optional[str] = None) -> dict:
    """
    Returns high-level portfolio summary: total AUM, number of positions,
    asset class breakdown, top 3 holdings, overall P&L.

    Required role: analyst or risk (resource: portfolio) or manager (resource: summary).
    Managers receive view_tier=executive: KPIs plus top-3 concentration weights only
    (ticker + weight) — no security names beyond tickers, market values, or position P&L.
    """
    perm = check_permission(user, "portfolio")
    executive = False
    if not perm.allowed:
        perm = check_permission(user, "summary")
        executive = perm.allowed and user.role == "manager"
    log(user.email, user.role, "get_portfolio_summary", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        holdings = con.execute(
            "SELECT * FROM portfolio_holdings ORDER BY market_value DESC"
        ).fetchall()
        if not holdings:
            return attach_data_scope({"error": "No holdings data available.", "allowed": True})

        total_mv = sum(h["market_value"] for h in holdings)
        total_cost = sum(h["quantity"] * h["avg_cost"] for h in holdings)
        total_pnl = total_mv - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0

        # Asset class breakdown
        by_class: dict[str, float] = {}
        for h in holdings:
            ac = h["asset_class"]
            by_class[ac] = by_class.get(ac, 0) + h["market_value"]
        breakdown = {k: round(v / total_mv * 100, 2) for k, v in by_class.items()}

        if executive:
            top3 = [
                {"ticker": h["ticker"], "weight": h["weight_pct"]}
                for h in holdings[:3]
            ]
        else:
            top3 = [
                {
                    "ticker": h["ticker"],
                    "name":   h["name"],
                    "weight": h["weight_pct"],
                    "mv":     h["market_value"],
                    "pnl_pct": round((h["current_price"] - h["avg_cost"]) / h["avg_cost"] * 100, 2),
                }
                for h in holdings[:3]
            ]
        return attach_data_scope({
            "allowed": True,
            "view_tier": "executive" if executive else "full",
            "total_aum": round(total_mv, 2),
            "num_positions": len(holdings),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "asset_class_breakdown": breakdown,
            "top_3_holdings": top3,
        })
    finally:
        con.close()

# ── Tool: get_asset_exposure ──────────────────────────────────────────────────

def get_asset_exposure(user: User, question: str = "", db_path: Optional[str] = None) -> dict:
    """
    Returns per-position exposure: ticker, weight %, market value, asset class.
    Required role: analyst, risk (resource: portfolio)
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_asset_exposure", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        rows = con.execute(
            """SELECT ticker, name, asset_class, quantity, current_price,
                      market_value, weight_pct,
                      ROUND((current_price - avg_cost) / avg_cost * 100, 2) as pnl_pct
               FROM portfolio_holdings ORDER BY market_value DESC"""
        ).fetchall()
        return attach_data_scope({
            "allowed": True,
            "positions": [dict(r) for r in rows],
        })
    finally:
        con.close()

# ── Tool: get_recent_trades ───────────────────────────────────────────────────

def get_recent_trades(
    user: User,
    question: str = "",
    db_path: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Returns recent trades with status and risk scores.
    Required role: risk (resource: trades)
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "get_recent_trades", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        rows = con.execute(
            """SELECT ticker, direction, quantity, price, notional,
                      trader, status, risk_score, notes, traded_at
               FROM trades ORDER BY traded_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return attach_data_scope({
            "allowed": True,
            "trades":  [dict(r) for r in rows],
            "count":   len(rows),
        })
    finally:
        con.close()

# ── Tool: get_market_movers ───────────────────────────────────────────────────

def get_market_movers(
    user: User,
    top_n: int = 5,
    question: str = "",
    db_path: Optional[str] = None,
) -> dict:
    """
    Returns the biggest % movers in market_prices (up and down).
    Required role: analyst, risk (resource: market_prices)
    """
    perm = check_permission(user, "market_prices")
    log(user.email, user.role, "get_market_movers", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        # Most recent price per ticker
        rows = con.execute(
            """SELECT ticker, price, change_pct, source, fetched_at
               FROM market_prices
               WHERE fetched_at = (SELECT MAX(fetched_at) FROM market_prices mp2 WHERE mp2.ticker = market_prices.ticker)
               ORDER BY ABS(change_pct) DESC LIMIT ?""",
            (top_n * 2,),
        ).fetchall()
        movers = [dict(r) for r in rows]
        return attach_data_scope({
            "allowed": True,
            "top_gainers": [m for m in movers if m["change_pct"] > 0][:top_n],
            "top_losers":  [m for m in movers if m["change_pct"] < 0][:top_n],
        })
    finally:
        con.close()

# ── Tool: get_var_metrics ─────────────────────────────────────────────────────

def get_var_metrics(user: User, question: str = "", db_path: Optional[str] = None) -> dict:
    """
    Portfolio risk metrics computed with an ex-ante parametric
    (variance-covariance) model:

      - Annualised volatility from asset-class vols and a flat correlation.
      - 1-day Value-at-Risk at 95% and 99% (parametric / Gaussian).
      - Sharpe ratio from realised return vs the risk-free rate.
      - Equity beta from asset-class betas.
      - Per-asset-class P&L attribution.

    These are model estimates against a single price snapshot, not a live
    time-series risk system. Required role: analyst, risk (resource: portfolio).
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_var_metrics", perm.allowed, question, perm.deny_reason, db_path)
    if not perm.allowed:
        return {"error": perm.deny_reason, "allowed": False}

    con = connect(db_path)
    try:
        holdings = con.execute("SELECT * FROM portfolio_holdings").fetchall()
        if not holdings:
            return attach_data_scope({"allowed": True, "error": "No holdings data."})

        total_mv   = sum(h["market_value"] for h in holdings) or 1.0
        total_cost = sum(h["quantity"] * h["avg_cost"] for h in holdings) or 1.0

        positions = []
        for h in holdings:
            ret = (h["current_price"] - h["avg_cost"]) / h["avg_cost"] if h["avg_cost"] else 0.0
            positions.append({
                "ticker":      h["ticker"],
                "asset_class": h["asset_class"],
                "weight_pct":  h["weight_pct"],
                "weight_frac": h["market_value"] / total_mv,
                "pnl_pct":     round(ret * 100, 2),
                "pnl_abs":     round((h["current_price"] - h["avg_cost"]) * h["quantity"], 2),
            })

        # ── Parametric portfolio volatility ───────────────────────────────────
        # Var(P) = (1 - rho) * sum_i (w_i*sigma_i)^2 + rho * (sum_i w_i*sigma_i)^2
        weighted_sigmas = [
            p["weight_frac"] * ASSET_CLASS_VOL.get(p["asset_class"], 0.15)
            for p in positions
        ]
        s1 = sum(weighted_sigmas)
        s2 = sum(ws * ws for ws in weighted_sigmas)
        port_var = (1 - CROSS_CORRELATION) * s2 + CROSS_CORRELATION * (s1 * s1)
        annual_vol = port_var ** 0.5
        daily_vol  = annual_vol / (TRADING_DAYS ** 0.5)

        # ── Value-at-Risk (parametric, 1-day) ─────────────────────────────────
        var_95_pct = round(VAR_Z_95 * daily_vol * 100, 2)
        var_99_pct = round(VAR_Z_99 * daily_vol * 100, 2)
        var_95_abs = round(total_mv * VAR_Z_95 * daily_vol, 0)
        var_99_abs = round(total_mv * VAR_Z_99 * daily_vol, 0)

        # ── Sharpe ratio (realised return vs risk-free) ───────────────────────
        port_return = (total_mv - total_cost) / total_cost
        sharpe = round((port_return - RISK_FREE_RATE) / annual_vol, 2) if annual_vol else 0.0

        # ── Equity beta ───────────────────────────────────────────────────────
        beta = round(sum(
            p["weight_frac"] * ASSET_CLASS_BETA.get(p["asset_class"], 0.0)
            for p in positions
        ), 2)

        # ── Largest single-position drawdown contribution ─────────────────────
        worst_position = min(positions, key=lambda p: p["pnl_pct"]) if positions else {}
        max_dd_pct = round(
            worst_position.get("pnl_pct", 0) * worst_position.get("weight_pct", 0) / 100, 2
        ) if worst_position else 0.0

        # ── Asset-class P&L attribution ───────────────────────────────────────
        by_class: dict[str, dict] = {}
        for p in positions:
            ac = p["asset_class"]
            if ac not in by_class:
                by_class[ac] = {"pnl_abs": 0.0, "count": 0}
            by_class[ac]["pnl_abs"] += p["pnl_abs"]
            by_class[ac]["count"]   += 1

        # Drop transient field before returning positions to callers.
        for p in positions:
            p.pop("weight_frac", None)

        return attach_data_scope({
            "allowed":            True,
            "total_aum":          round(total_mv, 2),
            "annual_vol_pct":     round(annual_vol * 100, 2),
            "var_95_pct":         var_95_pct,
            "var_95_abs":         var_95_abs,
            "var_99_pct":         var_99_pct,
            "var_99_abs":         var_99_abs,
            "sharpe_proxy":       sharpe,
            "beta":               beta,
            "return_pct":         round(port_return * 100, 2),
            "max_dd_pct":         max_dd_pct,
            "worst_position":     worst_position.get("ticker", "-"),
            "by_class_pnl":       {k: round(v["pnl_abs"], 2) for k, v in by_class.items()},
            "positions":          positions,
            "risk_free_rate_pct": round(RISK_FREE_RATE * 100, 2),
            "note": (
                "Ex-ante parametric (variance-covariance) estimates using asset-class "
                "volatilities and a flat 0.35 cross-correlation, against a single price "
                "snapshot. Production replaces this with a 252-day return covariance matrix."
            ),
        })
    finally:
        con.close()
