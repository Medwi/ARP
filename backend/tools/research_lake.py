"""
ARP Platform – tools/research_lake.py
Research & Investment Decisions Lake.

Maps directly to ARP's SOON (0–3 month) roadmap priority:
  "Research & Investment Decisions Lake: a searchable archive capturing all
   meeting notes, research, and past investment decisions."
  Target: "query 18 months of research, podcasts, and emails in 30 seconds."

This module implements the research lake as a structured SQLite store with
full-text search (FTS5). That is the production path for the current platform:
fast lexical search over meeting notes, decisions, and transcripts without an
external vector database. A future phase may add on-device embeddings (same
Ollama stack as policy RAG in backend/rag.py) for semantic recall; ChromaDB is
not wired in this codebase yet.

Key design decisions:
  - Entries are tagged by type (RESEARCH / MEETING_NOTE / INVESTMENT_DECISION /
    EARNINGS_CALL / EMAIL / PODCAST_TRANSCRIPT)
  - Full-text search using SQLite FTS5 — fast, no external dependency
  - Conviction and outcome tracking for investment decisions
  - Analyst attribution for research supplier benchmarking (LATER roadmap item)
"""

from __future__ import annotations
import json
import sqlite3, os, re
from datetime import datetime, timezone
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend import llm
from backend.db import connect
from backend.prompt_safety import sanitize_research_content

RESEARCH_QA_PROMPT = """You are the research Q&A assistant for ARP Global Capital.
The user has selected specific entries from the Research & Investment Decisions Lake.

Answer ONLY from the selected entries in the data context. For each material claim,
cite the source as [entry_type] "title" (analyst or source if known).
If the answer is not supported by the selected entries, say clearly:
"Not found in selected research."
Do not use outside knowledge. Be concise, institutional, and decision-oriented."""

MAX_QA_ENTRIES = 5
MAX_QA_ENTRY_CHARS = 6_000

ENTRY_TYPES = frozenset({
    "RESEARCH", "MEETING_NOTE", "INVESTMENT_DECISION",
    "EARNINGS_CALL", "EMAIL", "PODCAST_TRANSCRIPT", "RISK_REPORT",
})


def ensure_research_tables(db_path: Optional[str] = None) -> None:
    """
    Create research lake tables if they don't exist.
    Called on first use — safe to call multiple times.
    Uses SQLite FTS5 for full-text search.
    """
    con = connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS research_entries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type    TEXT    NOT NULL,
            title         TEXT    NOT NULL,
            content       TEXT    NOT NULL,
            ticker        TEXT,
            analyst       TEXT,
            source        TEXT,
            conviction    INTEGER,
            outcome       TEXT,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            authored_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS research_tags (
            entry_id  INTEGER REFERENCES research_entries(id),
            tag       TEXT    NOT NULL,
            PRIMARY KEY (entry_id, tag)
        );

        CREATE INDEX IF NOT EXISTS idx_research_type   ON research_entries(entry_type);
        CREATE INDEX IF NOT EXISTS idx_research_ticker ON research_entries(ticker);
        CREATE INDEX IF NOT EXISTS idx_research_analyst ON research_entries(analyst);

        -- FTS5 virtual table for full-text search across title + content
        CREATE VIRTUAL TABLE IF NOT EXISTS research_fts
        USING fts5(
            title, content, ticker, analyst,
            content=research_entries,
            content_rowid=id
        );

        -- Keep FTS index in sync with research_entries
        CREATE TRIGGER IF NOT EXISTS research_ai
        AFTER INSERT ON research_entries BEGIN
            INSERT INTO research_fts(rowid, title, content, ticker, analyst)
            VALUES (new.id, new.title, new.content, new.ticker, new.analyst);
        END;

        CREATE TRIGGER IF NOT EXISTS research_au
        AFTER UPDATE ON research_entries BEGIN
            INSERT INTO research_fts(research_fts, rowid, title, content, ticker, analyst)
            VALUES ('delete', old.id, old.title, old.content, old.ticker, old.analyst);
            INSERT INTO research_fts(rowid, title, content, ticker, analyst)
            VALUES (new.id, new.title, new.content, new.ticker, new.analyst);
        END;

        CREATE TRIGGER IF NOT EXISTS research_ad
        AFTER DELETE ON research_entries BEGIN
            INSERT INTO research_fts(research_fts, rowid, title, content, ticker, analyst)
            VALUES ('delete', old.id, old.title, old.content, old.ticker, old.analyst);
        END;
    """)
    con.commit()
    con.close()


# ── Tool: add_research_entry ──────────────────────────────────────────────────

def add_research_entry(
    user:        User,
    entry_type:  str,
    title:       str,
    content:     str,
    ticker:      Optional[str]  = None,
    analyst:     Optional[str]  = None,
    source:      Optional[str]  = None,
    conviction:  Optional[int]  = None,
    tags:        Optional[list] = None,
    authored_at: Optional[str]  = None,
    db_path:     Optional[str]  = None,
) -> dict:
    """
    Ingest a new research entry into the knowledge lake.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "add_research_entry", perm.allowed,
        f"{entry_type}: {title[:60]}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    entry_type = entry_type.upper()
    if entry_type not in ENTRY_TYPES:
        return {"allowed": True, "error": f"Invalid entry_type. Must be one of: {sorted(ENTRY_TYPES)}"}
    if not title.strip() or not content.strip():
        return {"allowed": True, "error": "title and content cannot be empty"}
    if conviction is not None and not (1 <= conviction <= 5):
        return {"allowed": True, "error": "conviction must be 1–5"}

    safe_content = sanitize_research_content(content)

    ensure_research_tables(db_path)
    con = connect(db_path)
    try:
        cur = con.execute(
            """INSERT INTO research_entries
               (entry_type, title, content, ticker, analyst, source,
                conviction, authored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_type, title.strip(), safe_content,
             ticker.upper() if ticker else None,
             analyst or user.email, source, conviction, authored_at),
        )
        entry_id = cur.lastrowid

        if tags:
            con.executemany(
                "INSERT OR IGNORE INTO research_tags (entry_id, tag) VALUES (?, ?)",
                [(entry_id, t.lower().strip()) for t in tags if t.strip()],
            )
        con.commit()
        return {
            "allowed":    True,
            "entry_id":   entry_id,
            "entry_type": entry_type,
            "title":      title,
            "ticker":     ticker.upper() if ticker else None,
            "message":    f"Research entry #{entry_id} added to the knowledge lake.",
        }
    finally:
        con.close()


# ── Tool: search_research ─────────────────────────────────────────────────────

def search_research(
    user:        User,
    query:       str,
    entry_type:  Optional[str] = None,
    ticker:      Optional[str] = None,
    limit:       int = 10,
    db_path:     Optional[str] = None,
) -> dict:
    """
    Full-text search across the research lake.
    Uses SQLite FTS5 — returns results in relevance order.
    Required role: analyst or risk.
    Target: answer queries in under 30 seconds (per ARP roadmap).
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "search_research", perm.allowed,
        f"query={query[:60]}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_research_tables(db_path)
    con = connect(db_path)
    try:
        # Build FTS5 query — escape special chars
        fts_query = re.sub(r'[^\w\s]', ' ', query).strip()
        if not fts_query:
            return {"allowed": True, "results": [], "count": 0, "query": query}

        sql = """
            SELECT e.id, e.entry_type, e.title, e.ticker, e.analyst,
                   e.source, e.conviction, e.outcome, e.authored_at,
                   SUBSTR(e.content, 1, 300) as snippet,
                   rank
            FROM research_fts
            JOIN research_entries e ON research_fts.rowid = e.id
            WHERE research_fts MATCH ?
        """
        params = [fts_query]

        if entry_type:
            sql += " AND e.entry_type = ?"
            params.append(entry_type.upper())
        if ticker:
            sql += " AND e.ticker = ?"
            params.append(ticker.upper())

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = con.execute(sql, params).fetchall()
        results = [dict(r) for r in rows]

        return {
            "allowed": True,
            "results": results,
            "count":   len(results),
            "query":   query,
            "filters": {"entry_type": entry_type, "ticker": ticker},
        }
    except Exception as e:
        return {"allowed": True, "results": [], "count": 0,
                "query": query, "error": str(e)}
    finally:
        con.close()


# ── Tool: list_research_entries ───────────────────────────────────────────────

def list_research_entries(
    user:        User,
    limit:       int = 50,
    entry_type:  Optional[str] = None,
    ticker:      Optional[str] = None,
    db_path:     Optional[str] = None,
) -> dict:
    """Browse catalog — summary rows for UI selection (newest first)."""
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "list_research_entries", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_research_tables(db_path)
    limit = max(1, min(limit, 100))
    con = connect(db_path)
    try:
        sql = """
            SELECT id, entry_type, title, ticker, analyst, source, conviction,
                   authored_at, created_at,
                   SUBSTR(content, 1, 200) AS snippet
            FROM research_entries
            WHERE 1=1
        """
        params: list = []
        if entry_type:
            sql += " AND entry_type = ?"
            params.append(entry_type.upper())
        if ticker:
            sql += " AND ticker = ?"
            params.append(ticker.upper())
        sql += " ORDER BY COALESCE(authored_at, created_at) DESC LIMIT ?"
        params.append(limit)

        rows = con.execute(sql, params).fetchall()
        entries = [dict(r) for r in rows]
        return {
            "allowed": True,
            "entries": entries,
            "count":   len(entries),
            "filters": {"entry_type": entry_type, "ticker": ticker},
        }
    finally:
        con.close()


# ── Tool: get_research_entry ──────────────────────────────────────────────────

def get_research_entry(
    user:     User,
    entry_id: int,
    db_path:  Optional[str] = None,
) -> dict:
    """Full research entry by id — used when the user selects from the catalog."""
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_research_entry", perm.allowed,
        f"id={entry_id}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_research_tables(db_path)
    con = connect(db_path)
    try:
        row = con.execute(
            """SELECT id, entry_type, title, content, ticker, analyst, source,
                      conviction, outcome, authored_at, created_at
               FROM research_entries WHERE id = ?""",
            (entry_id,),
        ).fetchone()
        if not row:
            return {"allowed": True, "error": f"Research entry #{entry_id} not found."}

        tags = [
            r[0] for r in con.execute(
                "SELECT tag FROM research_tags WHERE entry_id = ? ORDER BY tag",
                (entry_id,),
            ).fetchall()
        ]
        entry = dict(row)
        entry["tags"] = tags
        return {"allowed": True, "entry": entry}
    finally:
        con.close()


# ── Tool: get_research_stats ──────────────────────────────────────────────────

def get_research_stats(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Research lake statistics: entry counts by type, analyst contributions,
    ticker coverage, date range.
    Required role: analyst or risk.
    """
    perm = check_permission(user, "portfolio")
    log(user.email, user.role, "get_research_stats", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_research_tables(db_path)
    con = connect(db_path)
    try:
        total = con.execute("SELECT COUNT(*) FROM research_entries").fetchone()[0]
        if total == 0:
            return {
                "allowed": True, "total_entries": 0,
                "message": "Research lake is empty. Use /research/ingest to add entries.",
            }

        by_type = dict(con.execute(
            "SELECT entry_type, COUNT(*) FROM research_entries GROUP BY entry_type"
        ).fetchall())

        by_analyst = dict(con.execute(
            "SELECT analyst, COUNT(*) FROM research_entries "
            "WHERE analyst IS NOT NULL GROUP BY analyst ORDER BY COUNT(*) DESC LIMIT 10"
        ).fetchall())

        by_ticker = dict(con.execute(
            "SELECT ticker, COUNT(*) FROM research_entries "
            "WHERE ticker IS NOT NULL GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 10"
        ).fetchall())

        date_range = con.execute(
            "SELECT MIN(COALESCE(authored_at, created_at)), "
            "MAX(COALESCE(authored_at, created_at)) FROM research_entries"
        ).fetchone()

        return {
            "allowed":       True,
            "total_entries": total,
            "by_type":       by_type,
            "by_analyst":    by_analyst,
            "top_tickers":   by_ticker,
            "date_range":    {"earliest": date_range[0], "latest": date_range[1]},
        }
    finally:
        con.close()


# ── Tool: ask_research_question ───────────────────────────────────────────────

def ask_research_question(
    user:      User,
    question:  str,
    entry_ids: list[int],
    db_path:   Optional[str] = None,
    model:     Optional[str] = None,
) -> dict:
    """
    Answer a natural-language question grounded in selected research lake entries.
    Required role: analyst or risk (portfolio permission).
    """
    perm = check_permission(user, "portfolio")
    log(
        user.email, user.role, "research_ask", perm.allowed,
        question[:200] if question else None, perm.deny_reason, db_path,
    )
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    q = (question or "").strip()
    if not q:
        return {"allowed": True, "error": "Question cannot be empty."}
    if len(q) > 2000:
        return {"allowed": True, "error": "Question too long (max 2000 characters)."}

    ids = [int(i) for i in entry_ids if i is not None]
    if not ids:
        return {"allowed": True, "error": "Select at least one research entry."}
    ids = list(dict.fromkeys(ids))[:MAX_QA_ENTRIES]

    loaded: list[dict] = []
    for eid in ids:
        row = get_research_entry(user, eid, db_path=db_path)
        if not row.get("allowed") or not row.get("entry"):
            continue
        entry = row["entry"]
        content = entry.get("content") or ""
        if len(content) > MAX_QA_ENTRY_CHARS:
            content = content[:MAX_QA_ENTRY_CHARS] + "… [truncated]"
        loaded.append({
            "id":          entry.get("id"),
            "entry_type":  entry.get("entry_type"),
            "title":       entry.get("title"),
            "ticker":      entry.get("ticker"),
            "analyst":     entry.get("analyst"),
            "source":      entry.get("source"),
            "conviction":  entry.get("conviction"),
            "authored_at": entry.get("authored_at") or entry.get("created_at"),
            "tags":        entry.get("tags", []),
            "content":     content,
        })

    if not loaded:
        return {"allowed": True, "error": "No valid research entries found for selection."}

    context = json.dumps({"selected_entries": loaded}, indent=2, default=str)
    answer = llm.chat(
        user_message=(
            f"Question about the selected research entries:\n{q}\n\n"
            "Answer using only the selected entries. Cite sources inline."
        ),
        context_data=context,
        system_prompt=RESEARCH_QA_PROMPT,
        model=model,
        audit_ctx=llm.LlmAuditContext(
            user.email, user.role, "research_ask", question_hint=q[:200],
        ),
        db_path=db_path,
    )

    return {
        "allowed":    True,
        "answer":     answer,
        "question":   q,
        "entry_ids":  [e["id"] for e in loaded],
        "sources":    [
            {
                "id": e["id"],
                "title": e["title"],
                "entry_type": e["entry_type"],
                "ticker": e.get("ticker"),
            }
            for e in loaded
        ],
        "tool_called": "research_ask",
        "entries_used": len(loaded),
    }


def seed_sample_research(db_path: Optional[str] = None) -> int:
    """
    Populate the research lake with realistic sample entries for demo purposes.
    Returns number of entries added.
    """
    ensure_research_tables(db_path)
    con = connect(db_path)

    # Check if already seeded
    if con.execute("SELECT COUNT(*) FROM research_entries").fetchone()[0] > 0:
        con.close()
        return 0

    samples = [
        ("RESEARCH", "NVIDIA — AI Chip Supply Chain Analysis Q2 2026",
         "NVDA continues to dominate the AI accelerator market with H100/H200 GPUs. "
         "Supply constraints easing into H2 2026. Margin expansion expected as TSMC "
         "capacity increases. Key risk: AMD MI300 gaining enterprise traction. "
         "Recommend maintaining overweight. Price target raised to $950.",
         "NVDA", "analyst@local", "Internal", 4),
        ("EARNINGS_CALL", "Apple Inc Q2 2026 Earnings Call — Key Takeaways",
         "Services revenue grew 18% YoY, now 28% of total revenue. iPhone unit sales "
         "in China declined 8% but ASP increased. Management guided for continued "
         "services margin expansion. AI features (Apple Intelligence) driving upgrade "
         "cycle in the US. Vision Pro remains early-stage. Hold rating maintained.",
         "AAPL", "analyst@local", "Earnings Call", 3),
        ("MEETING_NOTE", "Investment Committee — June 2026 Monthly Review",
         "Committee reviewed Q2 attribution. Crypto allocation debated — consensus "
         "to maintain 16% exposure pending regulatory clarity from DIFC. Goldman Sachs "
         "position discussed: rate sensitivity higher than expected. Decision: trim GS "
         "by 2% and rotate into JPM. Next meeting: July 3rd.",
         None, "risk@local", "Internal IC", 5),
        ("INVESTMENT_DECISION", "Decision: Initiate BTC Position — March 2026",
         "CIO approved initial BTC position of 5% AUM. Thesis: institutional adoption "
         "accelerating post-ETF approval. Custody via qualified custodian. Position "
         "to be built over 3 weeks to minimize market impact. Stop-loss at $50,000. "
         "Reviewed and approved by compliance. DIFC position reporting confirmed.",
         "BTC-USD", "risk@local", "CIO Decision", 5),
        ("RESEARCH", "GCC Macro Outlook H2 2026 — Rate Cut Implications",
         "UAE and Saudi Arabia tracking US Fed with 25-50bp lag. Regional PMI holding "
         "above 55. Oil at $78/bbl supports fiscal positions. AED peg credible. "
         "Implication for portfolio: favour rate-sensitive equities (JPM, GS) and "
         "reduce TLT duration exposure ahead of potential UAE rate action.",
         None, "analyst@local", "Internal Macro", 3),
        ("PODCAST_TRANSCRIPT", "Acquired Podcast — NVIDIA Episode Summary",
         "Key themes: Jensen Huang's long-term vision for AI as a new computing paradigm. "
         "CUDA moat discussed — 10 years of developer lock-in. Data centre capex cycle "
         "still in early innings per hyperscaler guidance. Risk: commoditisation of "
         "inference chips vs. training chips. Relevant to NVDA thesis.",
         "NVDA", "analyst@local", "Acquired Podcast", 4),
        ("RISK_REPORT", "Monthly Risk Review — May 2026",
         "Portfolio VaR (95%, 1-day): $420,000 (0.84% of AUM). Largest contributors: "
         "NVDA (28%), BTC-USD (31%). Concentration limit breached on BTC — flagged for "
         "IC review. Sharpe ratio (trailing 90d): 1.42. Max drawdown (90d): -4.2%. "
         "Stress test: -20% equity shock → -$8.2M P&L. Crypto -30% → -$15M.",
         None, "risk@local", "Internal Risk", None),
    ]

    count = 0
    for entry_type, title, content, ticker, analyst, source, conviction in samples:
        con.execute(
            """INSERT INTO research_entries
               (entry_type, title, content, ticker, analyst, source, conviction)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entry_type, title, content, ticker, analyst, source, conviction),
        )
        count += 1

    con.commit()
    con.close()
    return count
