"""
ARP Platform – rag.py
Local retrieval-augmented generation for internal policy and mandate documents.

Vector store backends (selected via VECTOR_DB):
  - local   Persistent JSON vector index (default; zero dependencies)
  - sqlite  SQLite-backed vector database (knowledge_vectors table in arp.db)

Both use:
  - Ollama embeddings (on-device; default nomic-embed-text)
  - Cosine similarity ranking
  - Keyword fallback when embeddings are unavailable

Security:
  - Documents are tagged with role-based audience metadata.
  - Retrieval filters chunks by the requesting user's role before ranking.
  - No document text is sent to external embedding APIs.
"""

from __future__ import annotations

import re
import json
import math
import sqlite3
import hashlib
import urllib.request
from pathlib import Path
from typing import Optional

from backend.config import (
    get_chroma_path,
    get_db_path,
    get_knowledge_dir,
    get_ollama_embed_model,
    rag_chunk_overlap,
    rag_chunk_size,
    rag_enabled,
    rag_top_k,
    resolve_ollama_host,
    vector_db_backend,
)
from backend.db import connect

_DEFAULT_AUDIENCE = ["admin"]
_ollama_embed_ok: Optional[bool] = None


def _index_dir() -> Path:
    return get_chroma_path()


def _index_file() -> Path:
    return _index_dir() / "vectors.json"


def _fingerprint_file() -> Path:
    return _index_dir() / ".corpus_fingerprint"


def _backend() -> str:
    return "sqlite" if vector_db_backend() == "sqlite" else "local"


def _ollama_base() -> str:
    return resolve_ollama_host()


def _parse_audience(text: str) -> list[str]:
    match = re.search(r"\*\*Audience:\*\*\s*(.+)", text, re.IGNORECASE)
    if not match:
        return list(_DEFAULT_AUDIENCE)
    roles = [r.strip().lower() for r in match.group(1).split(",") if r.strip()]
    return roles or list(_DEFAULT_AUDIENCE)


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    chunk_size = chunk_size if chunk_size is not None else rag_chunk_size()
    overlap = overlap if overlap is not None else rag_chunk_overlap()
    """Split document text into overlapping chunks for indexing."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _load_documents() -> list[dict]:
    """Load markdown knowledge files from KNOWLEDGE_DIR."""
    knowledge_dir = get_knowledge_dir()
    if not knowledge_dir.is_dir():
        return []

    docs: list[dict] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem.replace("_", " ").title()
        docs.append({
            "source": path.name,
            "title":  title,
            "text":   raw,
            "roles":  _parse_audience(raw),
        })
    return docs


def _corpus_fingerprint(docs: list[dict]) -> str:
    h = hashlib.sha256()
    for doc in docs:
        h.update(doc["source"].encode())
        h.update(doc["text"].encode())
    return h.hexdigest()[:16]


def _role_allowed(roles: list[str], user_role: str) -> bool:
    if user_role == "admin":
        return True
    return user_role in roles


def _hash_embed(text: str, dim: int = 384) -> list[float]:
    """Deterministic fallback embedding for offline / test use."""
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % len(digest)] / 255.0 - 0.5) for i in range(dim)]


def _ollama_embeddings_available() -> bool:
    """Probe Ollama once; avoid per-chunk timeouts when the service is down."""
    global _ollama_embed_ok
    if _ollama_embed_ok is not None:
        return _ollama_embed_ok
    payload = json.dumps({
        "model": get_ollama_embed_model(),
        "input": "ping",
    }).encode()
    try:
        req = urllib.request.Request(
            f"{_ollama_base()}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode())
            _ollama_embed_ok = bool(body.get("embedding"))
    except Exception:
        _ollama_embed_ok = False
    return _ollama_embed_ok


def embed_text(text: str) -> tuple[list[float], str]:
    """
    Embed a single string. Returns (vector, mode) where mode is
    'ollama' or 'hash'.
    """
    if not _ollama_embeddings_available():
        return _hash_embed(text), "hash"

    payload = json.dumps({
        "model": get_ollama_embed_model(),
        "input": text,
    }).encode()
    try:
        req = urllib.request.Request(
            f"{_ollama_base()}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            vec = body.get("embedding")
            if vec:
                return vec, "ollama"
    except Exception:
        global _ollama_embed_ok
        _ollama_embed_ok = False
    return _hash_embed(text), "hash"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_index() -> dict:
    index_file = _index_file()
    if not index_file.exists():
        return {"fingerprint": None, "embed_mode": None, "chunks": []}
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"fingerprint": None, "embed_mode": None, "chunks": []}


def _save_index(data: dict) -> None:
    _index_dir().mkdir(parents=True, exist_ok=True)
    _index_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    fp = data.get("fingerprint")
    if fp:
        _fingerprint_file().write_text(fp, encoding="utf-8")


def _build_chunks(docs: list[dict]) -> tuple[list[dict], str]:
    """Chunk + embed every document. Returns (chunk_rows, embed_mode)."""
    chunks_out: list[dict] = []
    embed_mode = "ollama"
    for doc in docs:
        for i, chunk in enumerate(chunk_text(doc["text"])):
            vec, mode = embed_text(chunk)
            if mode == "hash" and embed_mode == "ollama":
                embed_mode = "hash"
            chunks_out.append({
                "id":       f"{doc['source']}::{i}",
                "document": chunk,
                "metadata": {
                    "source": doc["source"],
                    "title":  doc["title"],
                    "roles":  ",".join(doc["roles"]),
                    "chunk":  i,
                },
                "embedding": vec,
            })
    return chunks_out, embed_mode


def index_corpus(force: bool = False) -> dict:
    """
    Index all knowledge/*.md into the configured vector store backend.
    Returns summary dict with chunk count, embed mode, and backend.
    """
    if not rag_enabled():
        return {"enabled": False, "chunks": 0, "reason": "ENABLE_RAG=0"}

    docs = _load_documents()
    if not docs:
        return {"enabled": True, "chunks": 0, "reason": f"no documents in {get_knowledge_dir()}"}

    fingerprint = _corpus_fingerprint(docs)
    if _backend() == "sqlite":
        result = _index_sqlite(docs, fingerprint, force)
    else:
        result = _index_local(docs, fingerprint, force)
    result.setdefault("backend", _backend())
    return result


def _index_local(docs: list[dict], fingerprint: str, force: bool) -> dict:
    existing = _load_index()
    if not force and existing.get("fingerprint") == fingerprint and existing.get("chunks"):
        return {
            "enabled":     True,
            "chunks":      len(existing["chunks"]),
            "skipped":     True,
            "fingerprint": fingerprint,
        }

    chunks_out, embed_mode = _build_chunks(docs)
    _save_index({
        "fingerprint": fingerprint,
        "embed_mode":  embed_mode,
        "chunks":      chunks_out,
    })
    return {
        "enabled":     True,
        "chunks":      len(chunks_out),
        "fingerprint": fingerprint,
        "embed_mode":  embed_mode,
        "documents":   len(docs),
    }


# ── SQLite vector database backend ──────────────────────────────────────────

def _sqlite_connect() -> sqlite3.Connection:
    Path(get_db_path()).parent.mkdir(parents=True, exist_ok=True)
    return connect()


def _ensure_sqlite_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_vectors (
            id         TEXT PRIMARY KEY,
            source     TEXT NOT NULL,
            title      TEXT NOT NULL,
            roles      TEXT NOT NULL,
            chunk      INTEGER NOT NULL,
            document   TEXT NOT NULL,
            embedding  TEXT NOT NULL,
            embed_mode TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kvec_source ON knowledge_vectors(source);

        CREATE TABLE IF NOT EXISTS knowledge_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )


def _index_sqlite(docs: list[dict], fingerprint: str, force: bool) -> dict:
    con = _sqlite_connect()
    try:
        _ensure_sqlite_schema(con)
        row = con.execute(
            "SELECT value FROM knowledge_meta WHERE key = 'corpus_fingerprint'"
        ).fetchone()
        stored_fp = row[0] if row else None
        count = con.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0]

        if not force and stored_fp == fingerprint and count > 0:
            return {
                "enabled":     True,
                "chunks":      count,
                "skipped":     True,
                "fingerprint": fingerprint,
            }

        chunks_out, embed_mode = _build_chunks(docs)
        con.execute("DELETE FROM knowledge_vectors")
        con.executemany(
            """INSERT OR REPLACE INTO knowledge_vectors
               (id, source, title, roles, chunk, document, embedding, embed_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    c["id"],
                    c["metadata"]["source"],
                    c["metadata"]["title"],
                    c["metadata"]["roles"],
                    c["metadata"]["chunk"],
                    c["document"],
                    json.dumps(c["embedding"]),
                    embed_mode,
                )
                for c in chunks_out
            ],
        )
        con.execute(
            "INSERT OR REPLACE INTO knowledge_meta (key, value) VALUES ('corpus_fingerprint', ?)",
            (fingerprint,),
        )
        con.execute(
            "INSERT OR REPLACE INTO knowledge_meta (key, value) VALUES ('embed_mode', ?)",
            (embed_mode,),
        )
        con.commit()
        return {
            "enabled":     True,
            "chunks":      len(chunks_out),
            "fingerprint": fingerprint,
            "embed_mode":  embed_mode,
            "documents":   len(docs),
        }
    finally:
        con.close()


def _keyword_score(query: str, text: str) -> float:
    q_tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2}
    if not q_tokens:
        return 0.0
    text_l = text.lower()
    hits = sum(1 for t in q_tokens if t in text_l)
    return hits / len(q_tokens)


def _keyword_retrieve(query: str, user_role: str, top_k: int) -> list[dict]:
    docs = _load_documents()
    scored: list[tuple[float, dict]] = []

    for doc in docs:
        if not _role_allowed(doc["roles"], user_role):
            continue
        for i, chunk in enumerate(chunk_text(doc["text"])):
            score = _keyword_score(query, chunk)
            if score <= 0:
                continue
            scored.append((score, {
                "source":    doc["source"],
                "title":     doc["title"],
                "text":      chunk,
                "score":     round(score, 3),
                "chunk":     i,
                "retrieval": "keyword",
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def _vector_retrieve(query: str, user_role: str, top_k: int) -> list[dict]:
    if _backend() == "sqlite":
        return _vector_retrieve_sqlite(query, user_role, top_k)
    return _vector_retrieve_local(query, user_role, top_k)


def _vector_retrieve_local(query: str, user_role: str, top_k: int) -> list[dict]:
    store = _load_index()
    chunks = store.get("chunks") or []
    if not chunks:
        return []

    vec, mode = embed_text(query)
    scored: list[tuple[float, dict]] = []

    for row in chunks:
        meta = row.get("metadata") or {}
        roles = [r.strip() for r in (meta.get("roles") or "").split(",") if r.strip()]
        if not _role_allowed(roles, user_role):
            continue
        sim = _cosine_similarity(vec, row.get("embedding") or [])
        if sim <= 0:
            continue
        scored.append((sim, {
            "source":    meta.get("source", "unknown"),
            "title":     meta.get("title", "Policy document"),
            "text":      row.get("document", ""),
            "score":     round(sim, 3),
            "chunk":     meta.get("chunk", 0),
            "retrieval": "vector" if mode == "ollama" else "vector_hash",
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def _vector_retrieve_sqlite(query: str, user_role: str, top_k: int) -> list[dict]:
    con = _sqlite_connect()
    try:
        _ensure_sqlite_schema(con)
        rows = con.execute(
            "SELECT source, title, roles, chunk, document, embedding FROM knowledge_vectors"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    if not rows:
        return []

    vec, mode = embed_text(query)
    scored: list[tuple[float, dict]] = []

    for row in rows:
        roles = [r.strip() for r in (row["roles"] or "").split(",") if r.strip()]
        if not _role_allowed(roles, user_role):
            continue
        try:
            embedding = json.loads(row["embedding"])
        except (json.JSONDecodeError, TypeError):
            continue
        sim = _cosine_similarity(vec, embedding)
        if sim <= 0:
            continue
        scored.append((sim, {
            "source":    row["source"],
            "title":     row["title"],
            "text":      row["document"],
            "score":     round(sim, 3),
            "chunk":     row["chunk"],
            "retrieval": "vector_db" if mode == "ollama" else "vector_db_hash",
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def retrieve(query: str, user_role: str, top_k: Optional[int] = None) -> list[dict]:
    """
    Return top-k knowledge chunks relevant to query, filtered by RBAC audience.

    Routing:
      - Real (Ollama) embeddings available -> semantic vector search, keyword fallback.
      - Only hash-fallback embeddings (offline) -> lexical keyword search first, since
        hash vectors carry no semantic signal; vector search is the last resort.
    """
    if not rag_enabled() or not query.strip():
        return []

    k = top_k or rag_top_k()
    if user_role == "intern":
        return []

    if _ollama_embeddings_available():
        vector_hits = _vector_retrieve(query, user_role, k)
        if vector_hits:
            return vector_hits
        return _keyword_retrieve(query, user_role, k)

    keyword_hits = _keyword_retrieve(query, user_role, k)
    if keyword_hits:
        return keyword_hits
    return _vector_retrieve(query, user_role, k)


def format_for_prompt(chunks: list[dict]) -> str:
    """Format retrieved chunks for inclusion in the LLM context block."""
    if not chunks:
        return ""
    parts = []
    for i, ch in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {ch['title']} ({ch['source']}, chunk {ch.get('chunk', 0)})\n"
            f"{ch['text']}"
        )
    return "\n\n".join(parts)


def build_context(user_role: str, question: str, live_data_json: str) -> tuple[str, list[dict]]:
    """
    Merge RAG retrieval with live database context for agent prompts.
    Returns (combined_context_string, rag_chunks).
    """
    chunks = retrieve(question, user_role)
    rag_block = format_for_prompt(chunks)
    if rag_block:
        combined = (
            "--- INTERNAL KNOWLEDGE (retrieved; cite when relevant) ---\n"
            f"{rag_block}\n"
            "--- LIVE DATABASE (authoritative for positions and trades) ---\n"
            f"{live_data_json}\n"
            "--- END CONTEXT ---"
        )
    else:
        combined = live_data_json
    return combined, chunks


def _sqlite_status() -> tuple[int, Optional[str], Optional[str], str]:
    """Return (chunk_count, fingerprint, embed_mode, location) for the SQLite backend."""
    try:
        con = _sqlite_connect()
        try:
            _ensure_sqlite_schema(con)
            count = con.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0]
            fp_row = con.execute(
                "SELECT value FROM knowledge_meta WHERE key = 'corpus_fingerprint'"
            ).fetchone()
            em_row = con.execute(
                "SELECT value FROM knowledge_meta WHERE key = 'embed_mode'"
            ).fetchone()
            return count, (fp_row[0] if fp_row else None), (em_row[0] if em_row else None), get_db_path()
        finally:
            con.close()
    except sqlite3.Error:
        return 0, None, None, get_db_path()


def status(*, lite: bool = False) -> dict:
    """RAG subsystem status for /health and observability."""
    if lite:
        return {
            "enabled":       rag_enabled(),
            "backend":       _backend(),
            "knowledge_dir": str(get_knowledge_dir()),
            "lite":          True,
        }
    doc_count = len(_load_documents())

    if _backend() == "sqlite":
        chunk_count, fingerprint, embed_mode, location = _sqlite_status()
        store_kind = "sqlite_vector_db (knowledge_vectors)"
    else:
        store = _load_index()
        chunk_count = len(store.get("chunks") or [])
        fingerprint = store.get("fingerprint")
        embed_mode = store.get("embed_mode")
        location = str(_index_dir())
        store_kind = "local_json_vector_index"

    ollama_embed = False
    try:
        req = urllib.request.Request(f"{_ollama_base()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            ollama_embed = any(get_ollama_embed_model() in m for m in models)
    except Exception:
        pass

    return {
        "enabled":         rag_enabled(),
        "backend":         _backend(),
        "knowledge_dir":   str(get_knowledge_dir()),
        "index_path":      location,
        "documents":       doc_count,
        "chunks_indexed":  chunk_count,
        "fingerprint":     fingerprint,
        "embed_model":     get_ollama_embed_model(),
        "embed_available": ollama_embed,
        "embed_mode":      embed_mode,
        "top_k":           rag_top_k(),
        "store":           store_kind,
    }
