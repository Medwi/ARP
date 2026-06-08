"""
ARP Platform – context.py
Unified agent context assembly: memory + knowledge graph + RAG + live data.

All portfolio and risk agents build prompts through build_agent_context() so
grounding order, feature-flag behaviour, and transparency metadata stay in one
place when we add a third agent or tune the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend import graph, memory, rag
from backend.rbac import User

_HASH_ONLY_RETRIEVAL = frozenset({"vector_hash", "vector_db_hash"})
_GROUNDED_RETRIEVAL = frozenset({"vector", "vector_db", "keyword"})


def assess_grounding(rag_chunks: list[dict]) -> dict:
    """
    Classify how well the prompt is grounded in policy RAG vs live data only.

    quality:
      - full       — semantic or keyword policy excerpts retrieved
      - live_only  — no policy chunks matched
      - degraded   — only hash-fallback vectors (no semantic signal)
    """
    if not rag_chunks:
        return {
            "quality": "live_only",
            "detail":  "No policy excerpts matched; answer uses live database data only.",
        }
    modes = {c.get("retrieval", "") for c in rag_chunks}
    if modes & _GROUNDED_RETRIEVAL:
        return {
            "quality": "full",
            "detail":  "Policy excerpts and live data available.",
        }
    if modes <= _HASH_ONLY_RETRIEVAL:
        return {
            "quality": "degraded",
            "detail":  (
                "RAG index used hash fallback only — semantic retrieval unavailable; "
                "verify policy claims against source documents."
            ),
        }
    return {
        "quality": "partial",
        "detail":  "Mixed retrieval modes; prefer live database figures.",
    }


def filter_rag_sources_for_display(chunks: list[dict]) -> list[dict]:
    """Hide hash-only vector hits from API/UI transparency (still in LLM context)."""
    return [c for c in chunks if c.get("retrieval") not in _HASH_ONLY_RETRIEVAL]


@dataclass
class AgentContext:
    """Structured result of merging every grounding layer for an agent prompt."""
    text: str
    rag_sources: list
    rag_sources_display: list
    graph_facts: list
    memory_used: int
    grounding: dict = field(default_factory=dict)


def build_agent_context(
    user: User,
    question: str,
    live_json: str,
    db_path: Optional[str] = None,
) -> AgentContext:
    """
    Merge memory, knowledge graph, RAG, and live DB data into one prompt block.

    Layer order (outermost → innermost in the string passed to the LLM):
      1. Conversation memory — continuity across follow-up questions
      2. Knowledge graph — entity/relationship links from live data + policy
      3. RAG — retrieved internal policy excerpts
      4. Live database JSON — authoritative positions, trades, alerts

    Each subsystem respects its own ENABLE_* flag and RBAC filters internally.
    """
    context, rag_chunks = rag.build_context(user.role, question, live_json)
    graph_block, graph_facts = graph.build_context_block(user.role, question, db_path)
    if graph_block:
        context = f"{graph_block}\n{context}"
    mem_block, mem_entries = memory.recall_block(user.email, question, db_path)
    if mem_block:
        context = f"{mem_block}\n{context}"

    grounding = assess_grounding(rag_chunks)

    return AgentContext(
        text=context,
        rag_sources=rag_chunks,
        rag_sources_display=filter_rag_sources_for_display(rag_chunks),
        graph_facts=graph_facts,
        memory_used=len(mem_entries),
        grounding=grounding,
    )
