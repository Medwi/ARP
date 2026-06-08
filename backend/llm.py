"""
ARP Platform – llm.py
Thin wrapper around Ollama's local REST API.
All inference stays on-device — no data leaves the machine.

Fallback chain: primary model → OLLAMA_FALLBACK_MODEL → graceful error string.
Successful invocations can be written to llm_audit_logs when audit_ctx is set.
"""

from __future__ import annotations
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

from backend.config import (
    get_ollama_fallback_model,
    get_ollama_model,
    get_ollama_tags_cache_seconds,
    resolve_ollama_host,
)
from backend.prompt_safety import build_chat_prompt

_tags_cache: dict = {"at": 0.0, "online": False, "models": []}


@dataclass(frozen=True)
class LlmAuditContext:
    """Metadata for llm_audit_logs — no prompt or full response stored."""
    user_email:    str
    role:          str
    context:       str
    question_hint: str = ""


@dataclass
class ChatResult:
    """LLM answer plus model metadata for API transparency."""
    text:            str
    model_used:      str
    primary_model:   str
    fallback_model:  str
    fallback_used:   bool
    latency_ms:      int


def _ollama_base() -> str:
    return resolve_ollama_host()


def __getattr__(name: str):
    if name == "OLLAMA_HOST":
        return resolve_ollama_host()
    if name == "OLLAMA_MODEL":
        return get_ollama_model()
    if name == "OLLAMA_FALLBACK_MODEL":
        return get_ollama_fallback_model()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_HOUSE_STYLE = (
    " Write in a precise, institutional tone suitable for a regulated asset "
    "manager. Use only the figures supplied in the data context; never fabricate "
    "numbers. Do not use emojis, decorative symbols, or exclamation marks."
)

PERSONAS: dict[str, str] = {
    "analyst": (
        "You are an investment analyst assistant for ARP Global Capital. "
        "You focus on portfolio composition, performance attribution, and market data. "
        "Be precise and data-driven. Cite the data context. "
        "Format currency with the USD symbol, percentages with %, and thousands separators. "
        "Flag any data gaps explicitly." + _HOUSE_STYLE
    ),
    "risk": (
        "You are a risk officer assistant for ARP Global Capital, operating under "
        "DFSA regulatory oversight in the DIFC. "
        "You focus on risk metrics, limit breaches, flagged trades, and concentration. "
        "Apply conservative interpretation: when in doubt, flag rather than clear. "
        "Cite which risk rule applies and reference thresholds explicitly. "
        "Never approve a trade; only analyse and flag. All outputs are advisory." + _HOUSE_STYLE
    ),
    "compliance": (
        "You are a compliance analyst assistant for ARP Global Capital. "
        "You operate under DFSA regulation, DIFC Data Protection Law No. 5 of 2020, "
        "and DFSA Regulation 10 on Autonomous Systems. "
        "Frame all answers in terms of regulatory obligations and sign-off requirements. "
        "Always note which role must approve a given action (CIO only, CIO plus Compliance, Board). "
        "Recommend human review for any uncertain determination. "
        "Never make a definitive compliance ruling; only surface the relevant framework." + _HOUSE_STYLE
    ),
    "admin": (
        "You are the platform administrator assistant for ARP Global Capital. "
        "You have visibility across portfolio, trades, risk, market data, and audit. "
        "Be precise, cite data from the context only, and note access-control and audit "
        "implications when discussing permissions or compliance." + _HOUSE_STYLE
    ),
    "manager": (
        "You are an executive summary assistant for ARP Global Capital. "
        "Provide high-level, jargon-free summaries suitable for a fund COO or CFO. "
        "Avoid granular trade-level detail. Focus on AUM, P&L direction, and headline risk. "
        "Keep responses to three sentences or fewer unless asked to elaborate." + _HOUSE_STYLE
    ),
}

SYSTEM_PROMPT = PERSONAS["analyst"]

_UNGROUNDED_NOTICE = (
    "[Grounding notice: no policy RAG excerpts matched this question — "
    "answer uses live database data only. Verify figures against the tool output.]"
)


def get_persona(role: str) -> str:
    return PERSONAS.get(role, PERSONAS["analyst"])


def _call_ollama(model: str, prompt: str, timeout: int = 180) -> Optional[str]:
    payload = json.dumps({
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{_ollama_base()}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            text = body.get("response", "").strip()
            return text if text else None
    except Exception:
        return None


def chat_with_meta(
    user_message:  str,
    context_data:  str,
    system_prompt: Optional[str] = None,
    model:         Optional[str] = None,
    audit_ctx:     Optional[LlmAuditContext] = None,
    db_path:       Optional[str] = None,
    *,
    grounding_quality: Optional[str] = None,
) -> ChatResult:
    """Send a question + context to Ollama; return answer and model metadata."""
    t0 = time.time()
    primary = model or get_ollama_model()
    fallback = get_ollama_fallback_model()
    system  = system_prompt or SYSTEM_PROMPT
    prompt  = build_chat_prompt(system, context_data, user_message)

    model_used = primary
    fallback_used = False
    response = _call_ollama(primary, prompt)

    if not response and primary != fallback:
        alt = _call_ollama(fallback, prompt, timeout=60)
        if alt:
            response = alt
            model_used = fallback
            fallback_used = True

    if not response:
        response = (
            f"[LLM UNAVAILABLE] Could not reach Ollama at {_ollama_base()} "
            f"with model '{primary}' or fallback '{fallback}'. "
            "Ensure the ollama service is running and the model is pulled: "
            f"`docker exec arp_ollama ollama pull {primary}`"
        )
    elif grounding_quality == "live_only" and not is_failure_response(response):
        if _UNGROUNDED_NOTICE not in response:
            response = f"{_UNGROUNDED_NOTICE}\n\n{response}"

    latency_ms = int((time.time() - t0) * 1000)
    if audit_ctx:
        from backend.audit import log_llm_call
        log_llm_call(
            audit_ctx.user_email,
            audit_ctx.role,
            audit_ctx.context,
            model_used,
            fallback,
            fallback_used,
            latency_ms,
            response,
            success=not is_failure_response(response),
            db_path=db_path,
        )

    return ChatResult(
        text=response,
        model_used=model_used,
        primary_model=primary,
        fallback_model=fallback,
        fallback_used=fallback_used,
        latency_ms=latency_ms,
    )


def chat(
    user_message:  str,
    context_data:  str,
    system_prompt: Optional[str] = None,
    model:         Optional[str] = None,
    audit_ctx:     Optional[LlmAuditContext] = None,
    db_path:       Optional[str] = None,
    *,
    grounding_quality: Optional[str] = None,
) -> str:
    """Backward-compatible wrapper returning answer text only."""
    return chat_with_meta(
        user_message, context_data,
        system_prompt=system_prompt, model=model,
        audit_ctx=audit_ctx, db_path=db_path,
        grounding_quality=grounding_quality,
    ).text


def is_failure_response(text: str | None) -> bool:
    if not text:
        return True
    return text.startswith("[LLM UNAVAILABLE]")


def _refresh_tags_cache() -> None:
    now = time.time()
    if now - _tags_cache["at"] < get_ollama_tags_cache_seconds():
        return
    try:
        req = urllib.request.Request(f"{_ollama_base()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            _tags_cache["online"] = True
            _tags_cache["models"] = [m["name"] for m in data.get("models", [])]
    except Exception:
        grace = get_ollama_tags_cache_seconds() * 3
        if _tags_cache["online"] and (now - _tags_cache["at"]) < grace:
            return
        _tags_cache["online"] = False
        _tags_cache["models"] = []
    _tags_cache["at"] = now


def is_available() -> bool:
    _refresh_tags_cache()
    return _tags_cache["online"]


def list_models() -> list[str]:
    _refresh_tags_cache()
    return list(_tags_cache["models"])


def active_model() -> str:
    return get_ollama_model()


def fallback_model() -> str:
    return get_ollama_fallback_model()
