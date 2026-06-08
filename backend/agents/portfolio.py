"""
ARP Platform – agents/portfolio.py
Portfolio Analyst Agent.
Orchestration flow: question → tool selection → DB query → LLM synthesis → response.
"""

from __future__ import annotations
from typing import Optional

from backend.rbac import User
from backend.agents.base import run_agent
from backend.tools.portfolio_tools import (
    get_portfolio_summary,
    get_asset_exposure,
    get_market_movers,
)

# Keyword → tool routing (simple intent detection, no external NLP needed)
TOOL_ROUTER: list[tuple[list[str], str]] = [
    (["asset allocation", "asset class", "breakdown", "exposure", "weight",
      "how much", "percentage", "percent", "position size"],
     "get_asset_exposure"),
    (["mov", "gainer", "loser", "change", "up today", "down today",
      "market", "price", "mover"],
     "get_market_movers"),
    (["top holding", "top position", "biggest", "largest", "allocation",
      "summary", "overview", "performance", "portfolio", "aum", "p&l",
      "pnl", "profit", "loss", "return"],
     "get_portfolio_summary"),
]

TOOLS = {
    "get_portfolio_summary": get_portfolio_summary,
    "get_asset_exposure":    get_asset_exposure,
    "get_market_movers":     get_market_movers,
}

DEFAULT_TOOL = "get_portfolio_summary"


def run(
    user: User,
    question: str,
    db_path: str | None = None,
    model: str | None = None,
    client_ip: str | None = None,
) -> dict:
    """Main agent entry point."""
    return run_agent(
        user, question, TOOL_ROUTER, TOOLS, DEFAULT_TOOL,
        db_path=db_path, model=model, client_ip=client_ip,
    )
