"""
ARP Platform – tools/factor_analysis.py
Portfolio Risk & Factor Analysis Agent.

Maps to ARP's SOON (0–3 month) roadmap priority:
  "Portfolio Risk & Factor Analysis Agent: continuous factor/sector analysis,
   concentration monitoring, and stress testing."

Implements:
  - Sector exposure breakdown (GICS classification)
  - Factor exposures: momentum, value, quality, volatility
  - Correlation heatmap data (cross-position correlation proxy)
  - Stress test scenarios: equity crash, crypto crash, rate spike, oil shock
  - Concentration risk: Herfindahl-Hirschman Index (HHI)
  - Factor-adjusted attribution commentary

In production: uses a 252-day returns window from Broadridge.
This demo runs on the single-snapshot portfolio data.
"""

from __future__ import annotations
import sqlite3, os, math
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect

# ── GICS sector mapping ───────────────────────────────────────────────────────
SECTOR_MAP = {
    "AAPL": "Information Technology",
    "MSFT": "Information Technology",
    "GOOGL": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "NVDA": "Information Technology",
    "META": "Communication Services",
    "JPM":  "Financials",
    "GS":   "Financials",
    "BRK-B":"Financials",
    "XOM":  "Energy",
    "SPY":  "Broad Market ETF",
    "QQQ":  "Technology ETF",
    "GLD":  "Commodities ETF",
    "TLT":  "Fixed Income ETF",
    "VXX":  "Volatility ETF",
    "BTC-USD": "Digital Assets",
    "ETH-USD": "Digital Assets",
}

# ── Factor proxies (simplified, heuristic) ───────────────────────────────────
# Real: Fama-French factors from CRSP/Compustat
# Proxy: hand-coded factor tilts based on known stock characteristics
FACTOR_MAP = {
    "AAPL":  {"momentum": 0.6, "value": 0.2, "quality": 0.8, "low_vol": 0.5},
    "MSFT":  {"momentum": 0.7, "value": 0.3, "quality": 0.9, "low_vol": 0.6},
    "GOOGL": {"momentum": 0.5, "value": 0.4, "quality": 0.7, "low_vol": 0.4},
    "AMZN":  {"momentum": 0.6, "value": 0.2, "quality": 0.7, "low_vol": 0.4},
    "NVDA":  {"momentum": 0.9, "value": 0.1, "quality": 0.7, "low_vol": 0.2},
    "META":  {"momentum": 0.8, "value": 0.4, "quality": 0.7, "low_vol": 0.3},
    "JPM":   {"momentum": 0.4, "value": 0.7, "quality": 0.6, "low_vol": 0.6},
    "GS":    {"momentum": 0.5, "value": 0.6, "quality": 0.6, "low_vol": 0.5},
    "BRK-B": {"momentum": 0.3, "value": 0.9, "quality": 0.9, "low_vol": 0.7},
    "XOM":   {"momentum": 0.4, "value": 0.8, "quality": 0.6, "low_vol": 0.5},
    "SPY":   {"momentum": 0.5, "value": 0.5, "quality": 0.7, "low_vol": 0.7},
    "QQQ":   {"momentum": 0.7, "value": 0.3, "quality": 0.7, "low_vol": 0.5},
    "GLD":   {"momentum": 0.4, "value": 0.5, "quality": 0.5, "low_vol": 0.6},
    "TLT":   {"momentum": 0.3, "value": 0.6, "quality": 0.8, "low_vol": 0.8},
    "VXX":   {"momentum": 0.5, "value": 0.1, "quality": 0.2, "low_vol": 0.1},
    "BTC-USD":{"momentum": 0.8, "value": 0.1, "quality": 0.2, "low_vol": 0.1},
    "ETH-USD":{"momentum": 0.8, "value": 0.1, "quality": 0.2, "low_vol": 0.1},
}

# ── Stress scenarios ──────────────────────────────────────────────────────────
STRESS_SCENARIOS = {
    "Equity Crash (-20%)": {
        "Equity": -0.20, "ETF": -0.18, "Information Technology": -0.25,
        "Financials": -0.22, "Communication Services": -0.20,
        "Consumer Discretionary": -0.20, "Energy": -0.15,
        "Digital Assets": -0.35, "Broad Market ETF": -0.18,
        "Technology ETF": -0.24, "Fixed Income ETF": +0.08,
        "Commodities ETF": +0.05, "Volatility ETF": +0.60,
    },
    "Crypto Crash (-50%)": {
        "Digital Assets": -0.50, "Information Technology": -0.05,
        "Equity": -0.03, "ETF": -0.02, "Financials": -0.03,
        "Communication Services": -0.04, "Consumer Discretionary": -0.03,
        "Energy": -0.01, "Broad Market ETF": -0.02,
        "Technology ETF": -0.04, "Fixed Income ETF": +0.01,
        "Commodities ETF": +0.02, "Volatility ETF": +0.15,
    },
    "Rate Spike (+200bps)": {
        "Fixed Income ETF": -0.18, "Financials": +0.06,
        "Information Technology": -0.12, "Volatility ETF": +0.20,
        "Equity": -0.08, "Digital Assets": -0.15, "Broad Market ETF": -0.07,
        "Technology ETF": -0.11, "Commodities ETF": -0.03,
        "Communication Services": -0.09, "Consumer Discretionary": -0.10,
        "Energy": +0.04,
    },
    "Oil Shock (+40%)": {
        "Energy": +0.30, "Commodities ETF": +0.15,
        "Consumer Discretionary": -0.08, "Financials": +0.04,
        "Information Technology": -0.03, "Broad Market ETF": -0.05,
        "Technology ETF": -0.04, "Digital Assets": -0.05,
        "Communication Services": -0.02, "Fixed Income ETF": -0.04,
        "Volatility ETF": +0.20, "Equity": -0.04,
    },
}


# ── Tool: get_sector_exposure ─────────────────────────────────────────────────

def get_sector_exposure(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    GICS sector breakdown of the portfolio.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_sector_exposure", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    con = connect(db_path)
    try:
        holdings = con.execute(
            "SELECT ticker, market_value, weight_pct FROM portfolio_holdings"
        ).fetchall()
        total_mv = sum(h["market_value"] for h in holdings) or 1

        sectors: dict[str, dict] = {}
        unclassified = []

        for h in holdings:
            sector = SECTOR_MAP.get(h["ticker"], "Other")
            if sector == "Other":
                unclassified.append(h["ticker"])
            if sector not in sectors:
                sectors[sector] = {"market_value": 0.0, "tickers": []}
            sectors[sector]["market_value"] += h["market_value"]
            sectors[sector]["tickers"].append(h["ticker"])

        sector_weights = [
            {
                "sector":       s,
                "market_value": round(v["market_value"], 2),
                "weight_pct":   round(v["market_value"] / total_mv * 100, 2),
                "tickers":      v["tickers"],
            }
            for s, v in sectors.items()
        ]
        sector_weights.sort(key=lambda x: x["weight_pct"], reverse=True)

        # HHI concentration index (sum of squared weights)
        hhi = round(sum((w["weight_pct"] / 100) ** 2 for w in sector_weights) * 10000, 0)
        hhi_interpretation = (
            "Highly concentrated (>2500)" if hhi > 2500 else
            "Moderately concentrated (1500–2500)" if hhi > 1500 else
            "Well diversified (<1500)"
        )

        return {
            "allowed":             True,
            "sector_weights":      sector_weights,
            "hhi":                 int(hhi),
            "hhi_interpretation":  hhi_interpretation,
            "unclassified_tickers": unclassified,
            "total_sectors":       len(sectors),
        }
    finally:
        con.close()


# ── Tool: get_factor_exposure ─────────────────────────────────────────────────

def get_factor_exposure(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Portfolio factor exposures: momentum, value, quality, low volatility.
    Each factor is the AUM-weighted average of position factor scores (0–1).
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_factor_exposure", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    con = connect(db_path)
    try:
        holdings = con.execute(
            "SELECT ticker, weight_pct FROM portfolio_holdings"
        ).fetchall()
        total_weight = sum(h["weight_pct"] for h in holdings) or 100

        factors: dict[str, float] = {"momentum": 0.0, "value": 0.0,
                                      "quality": 0.0, "low_vol": 0.0}
        per_position = []

        for h in holdings:
            ticker  = h["ticker"]
            weight  = h["weight_pct"] / total_weight
            f_map   = FACTOR_MAP.get(ticker, {"momentum": 0.5, "value": 0.5,
                                               "quality": 0.5, "low_vol": 0.5})
            for factor in factors:
                factors[factor] += f_map[factor] * weight

            per_position.append({
                "ticker":   ticker,
                "weight":   round(h["weight_pct"], 2),
                "momentum": f_map["momentum"],
                "value":    f_map["value"],
                "quality":  f_map["quality"],
                "low_vol":  f_map["low_vol"],
            })

        factors = {k: round(v, 3) for k, v in factors.items()}

        # Factor tilt interpretation
        tilts = []
        if factors["momentum"] > 0.65:
            tilts.append("Strong momentum tilt — portfolio sensitive to trend reversals")
        if factors["value"] < 0.35:
            tilts.append("Low value exposure — portfolio skews growth/quality")
        if factors["quality"] > 0.65:
            tilts.append("High quality tilt — favourable in risk-off environments")
        if factors["low_vol"] < 0.40:
            tilts.append("High volatility profile — elevated beta vs. market")

        return {
            "allowed":          True,
            "portfolio_factors": factors,
            "factor_tilts":     tilts,
            "per_position":     per_position,
            "note": (
                "Factor scores are heuristic proxies (0=min, 1=max). "
                "Production uses Fama-French five-factor model on 252-day returns."
            ),
        }
    finally:
        con.close()


# ── Tool: run_stress_tests ────────────────────────────────────────────────────

def run_stress_tests(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Run all stress scenarios against the current portfolio.
    Returns estimated P&L impact and worst-case positions for each scenario.
    Required role: risk.
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "run_stress_tests", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    con = connect(db_path)
    try:
        holdings = con.execute("SELECT * FROM portfolio_holdings").fetchall()
        total_mv = sum(h["market_value"] for h in holdings) or 1

        results = []
        for scenario_name, shocks in STRESS_SCENARIOS.items():
            scenario_pnl  = 0.0
            position_impacts = []

            for h in holdings:
                ticker = h["ticker"]
                sector = SECTOR_MAP.get(ticker, "Other")
                # Apply most specific shock available
                shock = shocks.get(ticker) or shocks.get(sector) or shocks.get("Equity", 0.0)
                pos_pnl = h["market_value"] * shock
                scenario_pnl += pos_pnl
                position_impacts.append({
                    "ticker":    ticker,
                    "shock_pct": round(shock * 100, 1),
                    "pnl":       round(pos_pnl, 0),
                })

            position_impacts.sort(key=lambda x: x["pnl"])
            results.append({
                "scenario":       scenario_name,
                "total_pnl":      round(scenario_pnl, 0),
                "pnl_pct_aum":    round(scenario_pnl / total_mv * 100, 2),
                "worst_position": position_impacts[0] if position_impacts else {},
                "best_position":  position_impacts[-1] if position_impacts else {},
                "survivable":     scenario_pnl > -total_mv * 0.30,  # <30% drawdown
            })

        results.sort(key=lambda x: x["total_pnl"])
        worst = results[0]

        return {
            "allowed":       True,
            "scenarios":     results,
            "worst_scenario": worst["scenario"],
            "worst_pnl":     worst["total_pnl"],
            "worst_pnl_pct": worst["pnl_pct_aum"],
            "all_survivable": all(r["survivable"] for r in results),
            "note": (
                "Stress shocks are applied at sector/asset-class level. "
                "Production uses correlated factor shocks from historical drawdown periods."
            ),
        }
    finally:
        con.close()
