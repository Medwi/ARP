"""
ARP Platform – agents/base.py
Shared agent pipeline: route → tool(s) → context → LLM → response.

Tool selection is a **keyword-scored pre-stage** (`route_tools`) — not LLM
tool-calling or a planner. The model receives tool JSON in context and
synthesizes an answer; it does not choose which tools to invoke.

Production would swap the router for an LLM planner or MCP tool registry while
keeping the same RBAC-gated tool functions and audit hooks.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from backend import llm
from backend.audit import format_agent_question, log
from backend.context import build_agent_context
from backend.prompt_safety import sanitize_user_question
from backend.rbac import User

# Tool signature: (user, question, db_path) -> {allowed, ...}
ToolFn = Callable[[User, str, Optional[str]], dict]

_MAX_TOOLS = 2


def route_tools(
    question: str,
    tool_router: list[tuple[list[str], str]],
    default_tool: str,
    *,
    max_tools: int = _MAX_TOOLS,
) -> list[str]:
    """
    Deterministic keyword scoring — not LLM tool selection.

    Returns up to max_tools distinct tool names (multi-intent). Example:
    "portfolio summary and market movers" can invoke both summary and movers.
    """
    q = question.lower()
    scores: dict[str, int] = {}
    for keywords, tool_name in tool_router:
        hits = sum(1 for kw in keywords if kw in q)
        if hits:
            scores[tool_name] = scores.get(tool_name, 0) + hits
    if not scores:
        return [default_tool]
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [name for name, _ in ranked[:max_tools]]


def route_question(
    question: str,
    tool_router: list[tuple[list[str], str]],
    default_tool: str,
) -> str:
    """Single-tool routing — highest-scoring keyword match (backward compatible)."""
    return route_tools(question, tool_router, default_tool, max_tools=1)[0]


def run_agent(
    user: User,
    question: str,
    tool_router: list[tuple[list[str], str]],
    tools: dict[str, ToolFn],
    default_tool: str,
    db_path: Optional[str] = None,
    model: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> dict:
    """
    Main agent entry point shared by portfolio and risk agents.

    Returns:
        {
            tool_called, tools_called, data, response, allowed,
            rag_sources, graph_facts, memory_used, grounding,
            llm_model, llm_fallback_used, llm_error,
        }
    """
    question = sanitize_user_question(question)
    tool_names = route_tools(question, tool_router, default_tool)
    audit_question = format_agent_question(question, client_ip)

    tool_results: dict[str, dict] = {}
    for tool_name in tool_names:
        tool_fn = tools.get(tool_name) or tools[default_tool]
        data = tool_fn(user, question, db_path)

        log(
            user.email,
            user.role,
            tool_name,
            data.get("allowed", True),
            question=audit_question,
            deny_reason=None if data.get("allowed", True) else data.get("error"),
            db_path=db_path,
            source="agent",
        )

        if not data.get("allowed", True):
            return {
                "tool_called":   tool_name,
                "tools_called":  tool_names,
                "data":          None,
                "response":      f"Access denied. {data.get('error', '')}",
                "allowed":       False,
            }
        tool_results[tool_name] = data

    if len(tool_names) == 1:
        live_payload = tool_results[tool_names[0]]
    else:
        live_payload = {"tools_called": tool_names, "results": tool_results}

    live_json = json.dumps(live_payload, indent=2, default=str)
    ctx = build_agent_context(user, question, live_json, db_path)
    persona = llm.get_persona(user.role)
    chat_result = llm.chat_with_meta(
        question, ctx.text, system_prompt=persona, model=model,
        audit_ctx=llm.LlmAuditContext(
            user.email, user.role, f"agent:{'+'.join(tool_names)}",
            question_hint=question[:200],
        ),
        db_path=db_path,
        grounding_quality=ctx.grounding.get("quality"),
    )
    llm_error = llm.is_failure_response(chat_result.text)
    tool_called_label = tool_names[0] if len(tool_names) == 1 else "+".join(tool_names)

    return {
        "tool_called":       tool_called_label,
        "tools_called":      tool_names,
        "data":              live_payload,
        "rag_sources":       ctx.rag_sources_display,
        "graph_facts":       ctx.graph_facts,
        "memory_used":       ctx.memory_used,
        "grounding":         ctx.grounding,
        "response":          chat_result.text,
        "allowed":           True,
        "llm_error":         llm_error,
        "llm_model":         chat_result.model_used,
        "llm_fallback_used": chat_result.fallback_used,
    }
