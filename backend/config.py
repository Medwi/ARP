"""
ARP Platform – config.py
Central configuration: paths, feature flags, and service endpoints.

All modules should read settings through these helpers so local dev
(./data/arp.db, localhost Ollama) and Docker (/data/arp.db, ollama:11434)
stay aligned via DB_PATH / OLLAMA_HOST in .env — no silent per-module defaults.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

_ollama_resolved: Optional[str] = None


# ── Path helpers ──────────────────────────────────────────────────────────────

def _path_from_env(key: str, default: Path) -> Path:
    raw = os.getenv(key)
    if raw:
        return Path(raw).expanduser()
    return default


def get_db_path() -> str:
    """SQLite database file. Default: <project>/data/arp.db (matches .env.example)."""
    return str(_path_from_env("DB_PATH", ROOT / "data" / "arp.db"))


def get_chroma_path() -> Path:
    """Local JSON vector index directory."""
    raw = os.getenv("CHROMA_PATH") or os.getenv("RAG_INDEX_PATH")
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "chroma"


def get_knowledge_dir() -> Path:
    """Markdown policy corpus for RAG."""
    return _path_from_env("KNOWLEDGE_DIR", ROOT / "knowledge")


# ── Env parsing ───────────────────────────────────────────────────────────────

def getenv_bool(key: str, default: str = "1") -> bool:
    return os.getenv(key, default).strip() == "1"


def getenv_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def getenv_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def get_bootstrap_tokens_path() -> Path:
    """One-time plain token file written at seed (gitignored, not logged to stdout)."""
    return Path(get_db_path()).parent / ".bootstrap_tokens"


def allow_legacy_plain_tokens() -> bool:
    """When false (default), only SHA-256 digests are accepted from the users table."""
    return getenv_bool("ARP_ALLOW_LEGACY_PLAIN_TOKENS", "0")


def health_detail_public() -> bool:
    """When false (default), detailed /health requires a valid Bearer token."""
    return getenv_bool("ARP_HEALTH_PUBLIC_DETAIL", "0")


def docs_public() -> bool:
    """When false, OpenAPI/Swagger routes are disabled."""
    return getenv_bool("ARP_DOCS_PUBLIC", "1")


# ── Ollama ────────────────────────────────────────────────────────────────────

def get_ollama_host() -> str:
    return getenv_str("OLLAMA_HOST", "http://localhost:11434")


def get_ollama_model() -> str:
    return getenv_str("OLLAMA_MODEL", "phi3:mini")


def get_ollama_fallback_model() -> str:
    """Distinct from primary so the fallback chain is meaningful when primary fails."""
    return getenv_str("OLLAMA_FALLBACK_MODEL", "llama3.2:latest")


def get_ollama_embed_model() -> str:
    return getenv_str("OLLAMA_EMBED_MODEL", "nomic-embed-text")


def get_ollama_tags_cache_seconds() -> int:
    return getenv_int("OLLAMA_TAGS_CACHE_SECONDS", 45)


# ── Book data scope & persistence (operational architecture) ─────────────────

def get_data_scope() -> str:
    """
    demo_snapshot — seeded assessment book (default).
    live          — production book fed by authorised ingestion pipelines.
    """
    return getenv_str("ARP_DATA_SCOPE", "demo_snapshot")


def is_demo_book() -> bool:
    return get_data_scope() == "demo_snapshot"


def data_scope_note() -> str:
    if is_demo_book():
        return (
            "Modelled demonstration book — not live fund NAV, client data, "
            "or Broadridge-ingested positions."
        )
    return "Live book data from authorised ingestion pipelines."


def get_persistence_mode() -> str:
    """
    sqlite_demo    — single-file SQLite for assessment / local dev (default).
    postgresql     — production target (schema-compatible migration path).
    """
    return getenv_str("ARP_PERSISTENCE", "sqlite_demo")


def persistence_summary() -> dict:
    mode = get_persistence_mode()
    sqlite_demo = mode == "sqlite_demo"
    return {
        "mode":               mode,
        "engine":             "postgresql" if mode == "postgresql" else "sqlite",
        "demo_only":          sqlite_demo,
        "production_target":  "postgresql",
        "description": (
            "SQLite file persistence for local assessment and demos. "
            "Production target: PostgreSQL with the same tool/RBAC layer."
            if sqlite_demo
            else "PostgreSQL persistence (production-shaped deployment)."
        ),
        "assessment_limits": (
            {
                "high_availability":    "single-node SQLite file — no failover",
                "concurrent_writers":   "single-writer; not for multi-replica API without migration",
                "backup_rpo":           "manual backup only (make backup-db) — no automated PITR",
            }
            if sqlite_demo
            else None
        ),
    }


# ── API rate limits (demo-friendly defaults; tighten in production) ───────────

def rate_limit_backend() -> str:
    """memory (single worker) or redis (multi-worker / replicas)."""
    return getenv_str("RATE_LIMIT_BACKEND", "memory").lower()


def get_rate_limit_redis_url() -> str:
    return getenv_str("REDIS_URL", "")


def get_rate_limit_default() -> tuple[int, int]:
    """(max_requests, window_seconds) for general API traffic."""
    return (
        getenv_int("RATE_LIMIT_DEFAULT_MAX", 120),
        getenv_int("RATE_LIMIT_DEFAULT_WINDOW", 60),
    )


def get_rate_limit_ask() -> tuple[int, int]:
    """(max_requests, window_seconds) for POST /ask and other LLM-heavy routes."""
    return (
        getenv_int("RATE_LIMIT_ASK_MAX", 60),
        getenv_int("RATE_LIMIT_ASK_WINDOW", 60),
    )


def get_rate_limit_endpoint_overrides() -> dict[str, tuple[int, int]]:
    """Per-path limits; LLM routes share the ask budget."""
    ask_limit, ask_window = get_rate_limit_ask()
    return {
        "/ask": (ask_limit, ask_window),
        "/briefing": (ask_limit, ask_window),
        "/digest": (ask_limit, ask_window),
        "/digest/cio": (ask_limit, ask_window),
        "/email-triage": (ask_limit, ask_window),
        "/email-triage/demo": (ask_limit, ask_window),
        "/letters/generate": (ask_limit, ask_window),
        "/research/ask": (ask_limit, ask_window),
        "/audit/verify": (5, 60),
        "/risk/pre-trade": (20, 60),
    }


def resolve_ollama_host() -> str:
    """
    Return a reachable Ollama base URL.
    Probes configured host, then falls back to localhost when running outside Docker.
    """
    global _ollama_resolved
    if _ollama_resolved:
        return _ollama_resolved

    candidates = [get_ollama_host()]
    if "localhost" not in candidates[0] and "127.0.0.1" not in candidates[0]:
        candidates.append("http://localhost:11434")

    for host in candidates:
        try:
            req = urllib.request.Request(f"{host}/api/version", method="GET")
            with urllib.request.urlopen(req, timeout=2):
                _ollama_resolved = host
                return host
        except Exception:
            continue

    _ollama_resolved = get_ollama_host()
    return _ollama_resolved


def reset_ollama_cache() -> None:
    """Clear cached Ollama host (for tests or after service restart)."""
    global _ollama_resolved
    _ollama_resolved = None


# ── Feature flags ─────────────────────────────────────────────────────────────

def rag_enabled() -> bool:
    return getenv_bool("ENABLE_RAG", "1")


def graph_enabled() -> bool:
    return getenv_bool("ENABLE_GRAPH", "1")


def memory_enabled() -> bool:
    return getenv_bool("MEMORY_ENABLED", "1")


def rag_reindex_on_seed() -> bool:
    return getenv_bool("RAG_REINDEX", "0")


# ── RAG ───────────────────────────────────────────────────────────────────────

def vector_db_backend() -> str:
    return getenv_str("VECTOR_DB", "local").strip().lower()


def rag_top_k() -> int:
    return getenv_int("RAG_TOP_K", 3)


def rag_chunk_size() -> int:
    return getenv_int("RAG_CHUNK_SIZE", 700)


def rag_chunk_overlap() -> int:
    return getenv_int("RAG_CHUNK_OVERLAP", 100)


# ── Knowledge graph ───────────────────────────────────────────────────────────

def graph_max_hops() -> int:
    return getenv_int("GRAPH_MAX_HOPS", 2)


def graph_max_facts() -> int:
    return getenv_int("GRAPH_MAX_FACTS", 8)


def graph_trade_limit() -> int:
    return getenv_int("GRAPH_TRADE_LIMIT", 40)


# ── Memory ────────────────────────────────────────────────────────────────────

def memory_recall_limit() -> int:
    return getenv_int("MEMORY_RECALL_LIMIT", 3)


def memory_max_per_user() -> int:
    return getenv_int("MEMORY_MAX_PER_USER", 200)
