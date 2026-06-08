"""
ARP Platform – agents/risk.py
Risk & Compliance Agent.
Orchestration flow: question → tool selection → DB query → LLM synthesis → response.
"""

from __future__ import annotations
from typing import Optional

from backend.rbac import User
from backend.agents.base import run_agent
from backend.tools.portfolio_tools import get_recent_trades
from backend.tools.risk_tools import (
    get_risk_alerts,
    get_flagged_trades,
    check_overexposure,
)

TOOL_ROUTER: list[tuple[list[str], str]] = [
    # Flagged/review before generic "trade" — "Which trades need review?" must hit flagged
    (["flag", "flagged", "need review", "needs review", "review", "high risk",
      "suspicious", "explain why", "why was", "which trades", "problem trade"],
     "get_flagged_trades"),
    (["recent trade", "last trade", "transaction", "bought", "sold", "trades"],
     "get_recent_trades"),
    (["overexpos", "overweight", "concentrat", "too much",
      "limit", "single name", "sector"],
     "check_overexposure"),
    (["alert", "breach", "violation", "rule", "compliance",
      "risk alert", "warning", "monitor"],
     "get_risk_alerts"),
]

TOOLS = {
    "get_recent_trades":   get_recent_trades,
    "get_risk_alerts":     get_risk_alerts,
    "get_flagged_trades":  get_flagged_trades,
    "check_overexposure":  check_overexposure,
}

DEFAULT_TOOL = "get_risk_alerts"


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
