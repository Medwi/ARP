"""
Central book data scope — demo snapshot vs live ingestion.

All portfolio, risk, attribution, and reporting tools attach these fields so
UI and compliance exports stay aligned with pipeline health semantics.
"""

from __future__ import annotations

from typing import Any

from backend.config import data_scope_note, get_data_scope, is_demo_book, persistence_summary


def attach_data_scope(payload: dict[str, Any]) -> dict[str, Any]:
    """Add data_scope metadata to successful tool/API payloads."""
    if not payload.get("allowed", True):
        return payload
    payload["data_scope"] = get_data_scope()
    payload["data_scope_note"] = data_scope_note()
    payload["synthetic"] = is_demo_book()
    return payload


def book_freshness_label(pipeline_health: str) -> str:
    """
    Book freshness is independent of pipeline heartbeat.

    Demo book never becomes LIVE when sync succeeds — only pipeline health updates.
    """
    if is_demo_book():
        ph = (pipeline_health or "UNKNOWN").lower().replace("_", " ")
        return f"DEMO · pipeline {ph}"
    if pipeline_health == "HEALTHY":
        return "LIVE"
    if pipeline_health == "STALE":
        return "STALE"
    if pipeline_health in ("DEGRADED", "NOT_CONFIGURED"):
        return pipeline_health
    return "UNKNOWN"
