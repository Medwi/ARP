"""
Expansion API routes — shadow book, briefing, research, CRM, batch-2 modules.

Registered from backend/main.py. All handlers use get_db_path() and canonical tools.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import get_db_path
from backend.deps import enforce_tool_result, get_current_user
from backend.rbac import User, can_view_audit, can_view_metrics

router = APIRouter()


def _db():
    return get_db_path()


# ── Request models ────────────────────────────────────────────────────────────

class TradeIdeaRequest(BaseModel):
    ticker:       str
    direction:    str
    thesis:       str
    conviction:   int
    target_price: Optional[float] = None
    stop_loss:    Optional[float] = None


class ResolveIdeaRequest(BaseModel):
    status:      str
    outcome_pnl: Optional[float] = None


class EmailBatch(BaseModel):
    emails: list[dict]
    model:  Optional[str] = None


class ResearchEntry(BaseModel):
    entry_type:  str
    title:       str
    content:     str
    ticker:      Optional[str] = None
    analyst:     Optional[str] = None
    source:      Optional[str] = None
    conviction:  Optional[int] = None
    tags:        Optional[list] = None
    authored_at: Optional[str] = None


class ResearchAskRequest(BaseModel):
    question:  str
    entry_ids: list[int]
    model:     Optional[str] = None


class LetterRequest(BaseModel):
    period:        str = "monthly"
    tier:          str = "INSTITUTIONAL"
    investor_name: str = "Valued Investor"
    model:         Optional[str] = None


class KellyRequest(BaseModel):
    win_rate:       float
    avg_win:        float
    avg_loss:       float
    kelly_fraction: Optional[float] = None


class VolatilitySizeRequest(BaseModel):
    ticker:          str
    annual_vol:      float
    risk_budget_pct: float = 1.0


# ── Shadow book ─────────────────────────────────────────────────────────────

@router.post("/shadow-book/ideas")
def submit_idea(body: TradeIdeaRequest, user: User = Depends(get_current_user)):
    from backend.tools.shadow_book import submit_trade_idea
    return enforce_tool_result(submit_trade_idea(
        user, body.ticker, body.direction, body.thesis,
        body.conviction, body.target_price, body.stop_loss, _db(),
    ))


@router.get("/shadow-book/ideas")
def list_ideas(status: Optional[str] = None, user: User = Depends(get_current_user)):
    from backend.tools.shadow_book import get_shadow_book
    return enforce_tool_result(get_shadow_book(user, status=status, db_path=_db()))


@router.get("/shadow-book/report")
def shadow_report(user: User = Depends(get_current_user)):
    from backend.tools.shadow_book import get_shadow_book_report
    return enforce_tool_result(get_shadow_book_report(user, db_path=_db()))


@router.put("/shadow-book/ideas/{idea_id}")
def update_idea(idea_id: int, body: TradeIdeaRequest, user: User = Depends(get_current_user)):
    from backend.tools.shadow_book import update_trade_idea
    return enforce_tool_result(update_trade_idea(
        user, idea_id, body.ticker, body.direction, body.thesis,
        body.conviction, body.target_price, body.stop_loss, _db(),
    ))


@router.delete("/shadow-book/ideas/{idea_id}")
def delete_idea(idea_id: int, user: User = Depends(get_current_user)):
    from backend.tools.shadow_book import delete_trade_idea
    return enforce_tool_result(delete_trade_idea(user, idea_id, _db()))


@router.patch("/shadow-book/ideas/{idea_id}")
def resolve_idea(idea_id: int, body: ResolveIdeaRequest, user: User = Depends(get_current_user)):
    from backend.tools.shadow_book import resolve_trade_idea
    return enforce_tool_result(resolve_trade_idea(user, idea_id, body.status, body.outcome_pnl, _db()))


# ── Briefing & email triage ───────────────────────────────────────────────────

@router.get("/briefing")
def overnight_briefing(model: Optional[str] = None, user: User = Depends(get_current_user)):
    from backend.agents.briefing import run as briefing_run
    return enforce_tool_result(briefing_run(user, db_path=_db(), model=model))


@router.post("/email-triage")
def triage_emails(body: EmailBatch, user: User = Depends(get_current_user)):
    from backend.agents.email_triage import triage_emails as _triage
    return enforce_tool_result(_triage(user, body.emails, model=body.model, db_path=_db()))


@router.get("/email-triage/demo")
def triage_demo(model: Optional[str] = None, user: User = Depends(get_current_user)):
    from backend.agents.email_triage import triage_emails as _triage, build_sample_inbox
    return enforce_tool_result(_triage(user, build_sample_inbox(), model=model, db_path=_db()))


# ── Research lake ─────────────────────────────────────────────────────────────

@router.post("/research/ingest")
def ingest_research(body: ResearchEntry, user: User = Depends(get_current_user)):
    from backend.tools.research_lake import add_research_entry
    return enforce_tool_result(add_research_entry(
        user, body.entry_type, body.title, body.content,
        body.ticker, body.analyst, body.source,
        body.conviction, body.tags, body.authored_at, _db(),
    ))


@router.get("/research/search")
def search_research(
    q: str,
    entry_type: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 10,
    user: User = Depends(get_current_user),
):
    from backend.tools.research_lake import search_research as _search
    return enforce_tool_result(_search(user, q, entry_type=entry_type, ticker=ticker,
                   limit=limit, db_path=_db()))


@router.get("/research/entries")
def list_research(
    entry_type: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
):
    from backend.tools.research_lake import list_research_entries
    return enforce_tool_result(list_research_entries(
        user, limit=limit, entry_type=entry_type, ticker=ticker, db_path=_db(),
    ))


@router.get("/research/entries/{entry_id}")
def research_entry(entry_id: int, user: User = Depends(get_current_user)):
    from backend.tools.research_lake import get_research_entry
    return enforce_tool_result(get_research_entry(user, entry_id, db_path=_db()))


@router.post("/research/ask")
def research_ask(body: ResearchAskRequest, user: User = Depends(get_current_user)):
    from backend.tools.research_lake import ask_research_question
    return enforce_tool_result(ask_research_question(
        user, body.question, body.entry_ids,
        db_path=_db(), model=body.model,
    ))


@router.get("/research/stats")
def research_stats(user: User = Depends(get_current_user)):
    from backend.tools.research_lake import get_research_stats
    return enforce_tool_result(get_research_stats(user, db_path=_db()))


@router.post("/research/seed-samples")
def seed_research_samples(user: User = Depends(get_current_user)):
    if not can_view_audit(user):
        raise HTTPException(
            status_code=403,
            detail="Requires risk or admin role.",
        )
    from backend.tools.research_lake import seed_sample_research
    count = seed_sample_research(db_path=_db())
    if count:
        message = f"{count} sample entries added to research lake."
    else:
        message = "Research lake already has entries — samples were not re-added."
    return {"allowed": True, "seeded": count, "message": message}


# ── Factor analysis ───────────────────────────────────────────────────────────

@router.get("/portfolio/sectors")
def sector_exposure(user: User = Depends(get_current_user)):
    from backend.tools.factor_analysis import get_sector_exposure
    return enforce_tool_result(get_sector_exposure(user, db_path=_db()))


@router.get("/portfolio/factors")
def factor_exposure(user: User = Depends(get_current_user)):
    from backend.tools.factor_analysis import get_factor_exposure
    return enforce_tool_result(get_factor_exposure(user, db_path=_db()))


@router.get("/risk/stress-tests")
def stress_tests(user: User = Depends(get_current_user)):
    from backend.tools.factor_analysis import run_stress_tests
    return enforce_tool_result(run_stress_tests(user, db_path=_db()))


# ── Compliance export ─────────────────────────────────────────────────────────

@router.get("/report/compliance")
def compliance_report(format: str = "markdown", user: User = Depends(get_current_user)):
    from backend.exporters.report_generator import generate_report
    return enforce_tool_result(generate_report(user, db_path=_db(), format=format))


# ── CRM ───────────────────────────────────────────────────────────────────────

@router.get("/crm/pipeline")
def crm_pipeline(user: User = Depends(get_current_user)):
    from backend.tools.crm_integration import get_investor_pipeline
    return enforce_tool_result(get_investor_pipeline(user, db_path=_db()))


@router.post("/crm/seed-samples")
def crm_seed(user: User = Depends(get_current_user)):
    if not can_view_audit(user):
        raise HTTPException(status_code=403, detail="Requires risk or admin role.")
    from backend.tools.crm_integration import seed_sample_crm
    count = seed_sample_crm(db_path=_db())
    return {"seeded": count, "message": f"{count} sample investors added."}


@router.post("/crm/interaction")
def log_interaction(
    contact_id: int, type_: str, summary: str,
    outcome: Optional[str] = None,
    next_action: Optional[str] = None,
    next_action_due: Optional[str] = None,
    user: User = Depends(get_current_user),
):
    from backend.tools.crm_integration import log_investor_interaction
    return enforce_tool_result(log_investor_interaction(
        user, contact_id, type_, summary,
        outcome=outcome,
        next_action=next_action, next_action_due=next_action_due,
        db_path=_db(),
    ))


@router.get("/crm/draft/{contact_id}")
def investor_draft(contact_id: int, period: str = "monthly", user: User = Depends(get_current_user)):
    from backend.tools.crm_integration import generate_investor_update_draft
    return enforce_tool_result(generate_investor_update_draft(user, contact_id, period, _db()))


# ── Trade optimizer ───────────────────────────────────────────────────────────

@router.post("/trade/optimize")
def trade_optimize(
    ticker: str, direction: str, conviction: int,
    notional: float, time_horizon: str = "medium",
    user: User = Depends(get_current_user),
):
    from backend.tools.trade_optimizer import optimize_trade
    return enforce_tool_result(optimize_trade(user, ticker, direction, conviction,
                          notional, time_horizon, _db()))


@router.get("/trade/score-positions")
def score_positions(user: User = Depends(get_current_user)):
    from backend.tools.trade_optimizer import score_existing_positions
    return enforce_tool_result(score_existing_positions(user, db_path=_db()))


# ── Position sizing (batch 2 + batch 3 canonical paths) ───────────────────────

def _kelly_response(body: KellyRequest, user: User) -> dict:
    from backend.tools.position_sizing import kelly_size, DEFAULT_KELLY_FRACTION
    return enforce_tool_result(kelly_size(
        user, body.win_rate, body.avg_win, body.avg_loss,
        kelly_fraction=body.kelly_fraction or DEFAULT_KELLY_FRACTION,
        db_path=_db(),
    ))


def _volatility_response(body: VolatilitySizeRequest, user: User) -> dict:
    from backend.tools.position_sizing import volatility_size
    return enforce_tool_result(volatility_size(
        user, body.ticker, body.annual_vol,
        risk_budget_pct=body.risk_budget_pct, db_path=_db(),
    ))


@router.post("/trade/size/kelly")
@router.post("/trade/kelly")  # legacy alias
def trade_kelly(body: KellyRequest, user: User = Depends(get_current_user)):
    return _kelly_response(body, user)


@router.post("/trade/size/volatility")
@router.post("/trade/volatility-size")  # legacy alias
def trade_volatility_size(body: VolatilitySizeRequest, user: User = Depends(get_current_user)):
    return _volatility_response(body, user)


@router.get("/portfolio/risk-budget")
@router.get("/trade/risk-budget")  # legacy alias
def trade_risk_budget(user: User = Depends(get_current_user)):
    from backend.tools.position_sizing import portfolio_risk_budget
    return enforce_tool_result(portfolio_risk_budget(user, db_path=_db()))


# ── Analyst benchmarking ──────────────────────────────────────────────────────

@router.get("/analysts/rankings")
def analyst_rankings(user: User = Depends(get_current_user)):
    from backend.tools.analyst_benchmarking import get_analyst_rankings
    return enforce_tool_result(get_analyst_rankings(user, db_path=_db()))


@router.post("/analysts/seed-samples")
def seed_analysts(user: User = Depends(get_current_user)):
    if not can_view_audit(user):
        raise HTTPException(
            status_code=403,
            detail="Requires risk or admin role.",
        )
    from backend.tools.analyst_benchmarking import seed_sample_analysts
    count = seed_sample_analysts(db_path=_db())
    return {"seeded": count, "message": f"{count} analysts seeded with call history."}


# ── Batch 2: letters, pipeline, attribution, reporting, CIO digest ────────────

@router.post("/letters/generate")
def generate_letter(body: LetterRequest, user: User = Depends(get_current_user)):
    from backend.agents.investor_letter import generate_investor_letter
    return enforce_tool_result(generate_investor_letter(
        user, body.period, body.tier, body.investor_name,
        body.model, _db(),
    ))


@router.get("/pipeline/status")
def pipeline_status(user: User = Depends(get_current_user)):
    from backend.tools.broadridge_pipeline import get_pipeline_status
    return enforce_tool_result(get_pipeline_status(user, db_path=_db()))


@router.post("/pipeline/simulate-sync")
def simulate_sync(user: User = Depends(get_current_user)):
    from backend.tools.broadridge_pipeline import simulate_sync_run
    return enforce_tool_result(simulate_sync_run(user, db_path=_db()))


@router.get("/pipeline/stats")
def pipeline_stats(days: int = 7, user: User = Depends(get_current_user)):
    from backend.tools.broadridge_pipeline import get_ingestion_stats
    return enforce_tool_result(get_ingestion_stats(user, days=days, db_path=_db()))


@router.get("/portfolio/attribution")
def portfolio_attribution(user: User = Depends(get_current_user)):
    from backend.tools.portfolio_attribution import get_pnl_attribution
    return enforce_tool_result(get_pnl_attribution(user, db_path=_db()))


@router.get("/reporting/manager-accounts")
def manager_accounts(user: User = Depends(get_current_user)):
    from backend.tools.manager_reporting import get_manager_accounts
    return enforce_tool_result(get_manager_accounts(user, db_path=_db()))


@router.get("/digest")
@router.get("/digest/cio")  # legacy alias
def cio_digest(model: Optional[str] = None, user: User = Depends(get_current_user)):
    from backend.agents.cio_digest import run as cio_digest_run
    return enforce_tool_result(cio_digest_run(user, db_path=_db(), model=model))
