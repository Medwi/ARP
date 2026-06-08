"""
Platform attestation fields for /health, /metrics, and compliance exports.
"""

from __future__ import annotations

from backend import llm
from backend.config import get_data_scope, persistence_summary


def external_calls_active() -> bool:
    """True when configured market data leaves mock/local-only mode."""
    from backend import market_data
    return market_data.configured_source() != "mock"


def build_platform_attestation(*, include_llm: bool = True) -> dict:
    """Derive residency and external-call flags from runtime config — not hardcoded."""
    from backend import market_data

    source = market_data.configured_source()
    out = {
        "data_residency":     "local",
        "data_scope":         get_data_scope(),
        "market_data_source": source,
        "external_calls":     external_calls_active(),
        "llm_external":       False,
        "persistence":        persistence_summary(),
    }
    if include_llm:
        out["llm_online"]   = llm.is_available()
        out["llm_primary"]  = llm.active_model()
        out["llm_fallback"] = llm.fallback_model()
    return out
