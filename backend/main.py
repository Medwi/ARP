"""
ARP Platform – main.py
FastAPI application: token auth, RBAC enforcement, agent routing, audit logs.
No secrets in code – all config via environment variables.
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from backend.rbac import User
from backend.audit import recent_logs
from backend.agents import portfolio as portfolio_agent
from backend.agents import risk as risk_agent
from backend import llm
from backend.config import docs_public, get_db_path, getenv_bool, health_detail_public
from backend.db import connect
from backend.deps import enforce_tool_result, get_current_user, get_optional_user
from backend.config import get_data_scope, persistence_summary
from backend.platform_attestation import build_platform_attestation
from backend.expansion_routes import router as expansion_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup checks: DB tables, Ollama, env — warnings only (demo resilience)."""
    import sqlite3

    db_path = get_db_path()
    print("\n🚀 ARP Platform starting up...")

    try:
        con = sqlite3.connect(db_path)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required = {"users", "portfolio_holdings", "trades", "market_prices",
                    "risk_rules", "audit_logs"}
        missing = required - tables
        if missing:
            print(f"  ⚠️  DB missing tables: {missing}. Run seed first.")
        else:
            count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            print(f"  ✅ Database OK — {count} users, {len(tables)} tables at {db_path}")
        con.close()
    except Exception as e:
        print(f"  ❌ Database error: {e}")

    if llm.is_available():
        models = llm.list_models()
        model = llm.active_model()
        pulled = any(model in m for m in models)
        if pulled:
            print(f"  ✅ Ollama OK — model '{model}' ready ({len(models)} available)")
        else:
            print(f"  ⚠️  Ollama reachable but model '{model}' not pulled.")
    else:
        print(f"  ⚠️  Ollama not reachable. Agents use fallback until it starts.")

    print("  🌐 Serving on http://0.0.0.0:8000 — /docs for API reference\n")
    yield
    print("\n🛑 ARP Platform shutting down.")


_docs_enabled = docs_public()
app = FastAPI(
    title="ARP Investment Intelligence Platform",
    description="Local AI-powered investment operations. All inference on-device.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],  # Streamlit only
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

if getenv_bool("ENABLE_RATE_LIMITER", "1"):
    from backend.config import get_rate_limit_default
    from backend.middleware.rate_limiter import SlidingWindowRateLimiter
    _rl_max, _rl_window = get_rate_limit_default()
    app.add_middleware(
        SlidingWindowRateLimiter,
        default_limit=_rl_max,
        default_window=_rl_window,
    )

if getenv_bool("ENABLE_REQUEST_LOGGER", "1"):
    from backend.middleware.request_logger import RequestLogger, SlowRequestDetector
    app.add_middleware(SlowRequestDetector)
    app.add_middleware(RequestLogger)

app.include_router(expansion_router)

# ── Request / Response models ─────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    question: str
    agent:    str = "portfolio"   # "portfolio" | "risk"
    model:    Optional[str] = None  # override Ollama model, e.g. "mistral:7b"

class AgentResponse(BaseModel):
    tool_called: str
    response:    str
    allowed:     bool
    user_email:  str
    role:        str
    data:        Optional[dict] = None   # structured DB context passed to LLM
    rag_sources: Optional[list] = None  # retrieved policy excerpts (transparency)
    graph_facts: Optional[list] = None  # knowledge-graph relationships (transparency)
    memory_used: int = 0                # count of recalled prior interactions
    llm_error:   bool = False           # True when Ollama was unreachable
    tools_called:      Optional[list] = None  # multi-tool orchestration
    grounding:         Optional[dict] = None  # live_only | full | degraded
    llm_model:         Optional[str] = None
    llm_fallback_used: bool = False

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health(
    lite: bool = Query(False, description="Fast probe for UI sidebar (skips heavy RAG/graph builds)"),
    user: Optional[User] = Depends(get_optional_user),
):
    from backend import market_data, rag, memory, graph
    source = market_data.configured_source()
    show_detail = health_detail_public() or user is not None

    if not show_detail:
        return {
            "status":     "ok",
            "llm_online": llm.is_available(),
            "lite":       lite,
        }

    return {
        "status":             "ok",
        "llm_online":         llm.is_available(),
        "llm_models":         llm.list_models(),
        "llm_primary":        llm.active_model(),
        "llm_fallback":       llm.fallback_model(),
        "market_data_source": source,
        "rag":                rag.status(lite=lite),
        "graph":              graph.status(db_path=get_db_path(), lite=lite),
        "memory":             memory.status(db_path=get_db_path()),
        "rate_limit":         _rate_limit_status(),
        "data_scope":         get_data_scope(),
        "persistence":        persistence_summary(),
        "data_residency":     "local",
        "external_calls":     build_platform_attestation(include_llm=False)["external_calls"],
        "lite":               lite,
        "authenticated":      user is not None,
    }


def _rate_limit_status() -> dict:
    from backend.config import getenv_bool, rate_limit_backend
    if not getenv_bool("ENABLE_RATE_LIMITER", "1"):
        return {"enabled": False, "backend": "disabled"}
    from backend.middleware.rate_limit_store import get_rate_limit_store
    _, label = get_rate_limit_store()
    return {"enabled": True, "backend": label, "configured": rate_limit_backend()}

@app.get("/me")
def whoami(user: User = Depends(get_current_user)):
    from backend.rbac import get_user_permissions, role_summary
    return {
        "email":       user.email,
        "role":        user.role,
        "permissions": get_user_permissions(user.role),
        "description": role_summary(user.role),
    }

@app.post("/ask", response_model=AgentResponse)
def ask(body: QuestionRequest, request: Request,
        user: User = Depends(get_current_user)):
    """
    Route a natural-language question to the portfolio or risk agent.

    Tool choice is keyword-scored before the LLM runs (see `route_tools` in
    agents/base.py) — not native LLM tool-calling. Response includes
    `tools_called` and grounding metadata for transparency.
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if len(question) > 1000:
        raise HTTPException(status_code=400, detail="Question too long (max 1000 chars).")

    # Enrich audit context with client IP (for anomaly detection)
    client_ip = request.client.host if request.client else "unknown"

    if body.agent == "risk":
        result = risk_agent.run(
            user, question, get_db_path(), model=body.model, client_ip=client_ip,
        )
    else:
        result = portfolio_agent.run(
            user, question, get_db_path(), model=body.model, client_ip=client_ip,
        )

    # Persist successful LLM answers only — not Ollama outage strings.
    if result["allowed"] and not result.get("llm_error"):
        from backend import memory
        memory.remember(
            user.email, user.role, body.agent,
            question, result.get("response", ""),
            tool_called=result.get("tool_called"),
            db_path=get_db_path(),
        )

    return AgentResponse(
        tool_called=result["tool_called"],
        response=result["response"],
        allowed=result["allowed"],
        user_email=user.email,
        role=user.role,
        llm_error=result.get("llm_error", False),
        data=result.get("data"),
        rag_sources=result.get("rag_sources"),
        graph_facts=result.get("graph_facts"),
        memory_used=result.get("memory_used", 0),
        tools_called=result.get("tools_called"),
        grounding=result.get("grounding"),
        llm_model=result.get("llm_model"),
        llm_fallback_used=result.get("llm_fallback_used", False),
    )

@app.get("/memory")
def memory_history(limit: int = 20, user: User = Depends(get_current_user)):
    """Return the requesting user's own recent agent interactions (memory)."""
    from backend import memory
    return {
        "user":    user.email,
        "status":  memory.status(user_email=user.email, db_path=get_db_path()),
        "history": memory.history(user.email, limit=limit, db_path=get_db_path()),
    }

@app.delete("/memory")
def memory_clear(user: User = Depends(get_current_user)):
    """Clear the requesting user's own conversational memory."""
    from backend import memory
    removed = memory.clear(user.email, db_path=get_db_path())
    return {"user": user.email, "cleared": removed}

@app.get("/portfolio/summary")
def portfolio_summary(user: User = Depends(get_current_user)):
    from backend.tools.portfolio_tools import get_portfolio_summary
    return enforce_tool_result(get_portfolio_summary(user, db_path=get_db_path()))

@app.get("/portfolio/exposure")
def portfolio_exposure(user: User = Depends(get_current_user)):
    from backend.tools.portfolio_tools import get_asset_exposure
    return enforce_tool_result(get_asset_exposure(user, db_path=get_db_path()))

@app.get("/trades/recent")
def recent_trades(limit: int = 20, user: User = Depends(get_current_user)):
    from backend.tools.portfolio_tools import get_recent_trades
    return enforce_tool_result(get_recent_trades(user, limit=limit, db_path=get_db_path()))

@app.get("/risk/alerts")
def risk_alerts(user: User = Depends(get_current_user)):
    from backend.tools.risk_tools import get_risk_alerts
    return enforce_tool_result(get_risk_alerts(user, db_path=get_db_path()))

@app.get("/risk/flagged")
def flagged_trades(user: User = Depends(get_current_user)):
    from backend.tools.risk_tools import get_flagged_trades
    return enforce_tool_result(get_flagged_trades(user, db_path=get_db_path()))

@app.get("/market/movers")
def market_movers(top_n: int = 5, user: User = Depends(get_current_user)):
    from backend.tools.portfolio_tools import get_market_movers
    return enforce_tool_result(get_market_movers(user, top_n=top_n, db_path=get_db_path()))

@app.get("/audit/logs")
def audit_logs_endpoint(limit: int = 50, user: User = Depends(get_current_user)):
    """Audit log access is restricted to risk role only."""
    from backend.rbac import can_view_audit
    if not can_view_audit(user):
        raise HTTPException(
            status_code=403,
            detail="Audit log access restricted to risk and admin roles."
        )
    return {"logs": recent_logs(limit=limit, db_path=get_db_path())}

@app.get("/risk/badges")
def risk_badges(user: User = Depends(get_current_user)):
    """
    Risk badge strip: concentration, crypto exposure, flagged trades, rule breaches.
    Called on every dashboard load — surfaces breaches before any question is asked.
    Accessible to analyst (subset) and risk (full).
    """
    from backend.tools.risk_tools import get_badge_metrics
    return enforce_tool_result(get_badge_metrics(user, db_path=get_db_path()))

@app.get("/portfolio/var")
def portfolio_var(user: User = Depends(get_current_user)):
    """
    Portfolio risk metrics: VaR 95% proxy, Sharpe proxy, max drawdown,
    per-asset-class P&L. Heuristic model against snapshot data.
    """
    from backend.tools.portfolio_tools import get_var_metrics
    return enforce_tool_result(get_var_metrics(user, db_path=get_db_path()))

@app.get("/audit/verify")
def audit_verify(user: User = Depends(get_current_user)):
    """
    Walk the full audit log and verify the SHA-256 hash chain is intact.
    Any deletion, reordering, or modification of records returns valid=False.
    Accessible to risk role only.
    """
    from backend.rbac import can_view_audit
    if not can_view_audit(user):
        raise HTTPException(
            status_code=403,
            detail="Audit verification restricted to risk and admin roles."
        )
    from backend.audit import verify_chain
    return verify_chain(db_path=get_db_path())

@app.post("/risk/pre-trade")
def pre_trade_check(
    ticker:    str,
    direction: str,
    quantity:  float,
    price:     float,
    user: User = Depends(get_current_user),
):
    """
    Pre-trade compliance check. Validates a proposed trade against active
    risk rules and portfolio concentration limits. Returns CLEAR / WARNING / BLOCKED.
    Does NOT execute the trade — HITL required.
    """
    direction = direction.upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="direction must be BUY or SELL")
    if quantity <= 0 or price <= 0:
        raise HTTPException(status_code=400, detail="quantity and price must be positive")
    from backend.tools.risk_tools import check_pre_trade
    return enforce_tool_result(
        check_pre_trade(user, ticker, direction, quantity, price, get_db_path())
    )

@app.get("/risk/anomalies")
def audit_anomalies(user: User = Depends(get_current_user)):
    """
    Scan audit logs for insider-threat indicators:
    after-hours access, burst queries, repeated denials.
    Risk role only.
    """
    from backend.tools.risk_tools import detect_audit_anomalies
    return enforce_tool_result(detect_audit_anomalies(user, db_path=get_db_path()))

@app.get("/metrics")
def platform_metrics(user: User = Depends(get_current_user)):
    """
    Lightweight platform metrics: request counts per endpoint, error rate,
    audit log size. In-memory counters reset on restart.
    Risk role only.
    """
    from backend.rbac import can_view_metrics
    if not can_view_metrics(user):
        raise HTTPException(
            status_code=403,
            detail="Metrics restricted to analyst, risk, and admin roles."
        )
    con = connect()
    audit_count = con.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    denied      = con.execute("SELECT COUNT(*) FROM audit_logs WHERE allowed=0").fetchone()[0]
    tools       = con.execute(
        "SELECT tool_called, COUNT(*) n FROM audit_logs GROUP BY tool_called ORDER BY n DESC"
    ).fetchall()
    con.close()
    attestation = build_platform_attestation(include_llm=False)
    return {
        "audit_total":        audit_count,
        "audit_denied":       denied,
        "audit_allowed":      audit_count - denied,
        "deny_rate_pct":      round(denied / audit_count * 100, 1) if audit_count else 0,
        "top_tools":          [{"tool": t[0], "calls": t[1]} for t in tools],
        **attestation,
    }

# ── Knowledge graph ───────────────────────────────────────────────────────────

@app.get("/graph/stats")
def graph_stats(user: User = Depends(get_current_user)):
    """
    Node/edge counts for the requesting role's knowledge-graph subgraph.
    The graph is role-filtered: interns see nothing, managers see aggregate
    nodes only, analysts see portfolio entities, risk/admin see the full graph.
    """
    from backend import graph
    return graph.stats(user.role, db_path=get_db_path())

@app.get("/graph/neighbors")
def graph_neighbors(entity: str, user: User = Depends(get_current_user)):
    """
    Return the immediate relationships of an entity (ticker, sector, rule,
    trade, regulation, ...) within the caller's role-filtered graph.
    """
    entity = (entity or "").strip()
    if not entity:
        raise HTTPException(status_code=400, detail="entity query parameter is required.")
    if len(entity) > 100:
        raise HTTPException(status_code=400, detail="entity too long (max 100 chars).")
    from backend import graph
    return graph.neighbors(user.role, entity, db_path=get_db_path())
