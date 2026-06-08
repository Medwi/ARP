"""
ARP Platform – graph.py
Local knowledge graph over live portfolio/trade data and internal policy.

The graph links the entities an investment desk actually reasons about —
tickers, asset classes, sectors, trades, traders, risk rules, policy
documents, and regulations — and the relationships between them
(IN_ASSET_CLASS, IN_SECTOR, ON_TICKER, FLAGGED_BY, BREACHES, REQUIRES_SIGNOFF,
GOVERNS, ...).

Why a graph and not just RAG:
  Flat retrieval surfaces *text*. The graph surfaces *connections* — e.g.
  "NVDA -[ON_TICKER]- trade #42 -[FLAGGED_BY]-> high_risk_score
   -[REQUIRES_SIGNOFF]-> Risk + Compliance". Multi-hop links like
  ticker -> sector -> concentration limit -> escalation are exactly what a
  risk officer chases, and they ground the LLM in relationships instead of
  prose.

Security / governance:
  - The subgraph is rebuilt per requesting role using the same
    check_permission() gate as every tool. Interns get nothing; managers get
    aggregate (summary) nodes only; analysts get portfolio entities; risk and
    admin get trades, flags, surveillance rules, and regulations.
  - Built entirely on-device from SQLite + a curated policy ontology. No
    external calls, no LLM required to build or query the graph.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from backend.config import (
    graph_enabled,
    graph_max_facts,
    graph_max_hops,
    graph_trade_limit,
)
from backend.db import connect
from backend.rbac import User, check_permission

# ── Relationship vocabulary ───────────────────────────────────────────────────
IN_ASSET_CLASS   = "IN_ASSET_CLASS"
IN_SECTOR        = "IN_SECTOR"
IN_PORTFOLIO     = "IN_PORTFOLIO"
ON_TICKER        = "ON_TICKER"
EXECUTED_BY      = "EXECUTED_BY"
FLAGGED_BY       = "FLAGGED_BY"
BREACHES         = "BREACHES"
WITHIN_LIMIT     = "WITHIN_LIMIT"
CONSTRAINED_BY   = "CONSTRAINED_BY"
DEFINED_IN       = "DEFINED_IN"
REQUIRES_SIGNOFF = "REQUIRES_SIGNOFF"
GOVERNS          = "GOVERNS"

# ── Curated policy ontology (sourced from knowledge/*.md) ─────────────────────
# DB risk_rules supply the live thresholds; this map supplies the governance
# linkage (which document defines the rule, who signs off, audience).
_POLICY_RULES: dict[str, dict] = {
    "max_single_position": {
        "audience": {"analyst", "risk", "admin"},
        "doc": "risk_limits_policy.md", "doc_title": "Risk Limits & Monitoring Policy",
        "approver": "Risk", "escalation": "HIGH",
    },
    "max_crypto_exposure": {
        "audience": {"analyst", "risk", "admin"},
        "doc": "risk_limits_policy.md", "doc_title": "Risk Limits & Monitoring Policy",
        "approver": "Risk", "escalation": "HIGH", "asset_class": "Crypto",
    },
    "max_top3_concentration": {
        "audience": {"analyst", "risk", "admin"},
        "doc": "risk_limits_policy.md", "doc_title": "Risk Limits & Monitoring Policy",
        "approver": "Risk", "escalation": "HIGH",
    },
    "large_notional_trade": {
        "audience": {"risk", "admin"},
        "doc": "trade_surveillance.md", "doc_title": "Trade Surveillance & Flagging Procedures",
        "approver": "PM + Compliance", "escalation": "MEDIUM",
    },
    "high_risk_score": {
        "audience": {"risk", "admin"},
        "doc": "trade_surveillance.md", "doc_title": "Trade Surveillance & Flagging Procedures",
        "approver": "Risk + Compliance", "escalation": "CRITICAL",
    },
    "max_daily_turnover": {
        "audience": {"risk", "admin"},
        "doc": "risk_limits_policy.md", "doc_title": "Risk Limits & Monitoring Policy",
        "approver": "Risk", "escalation": "LOW",
    },
}

_REGULATIONS: list[dict] = [
    {
        "label": "DFSA Regulation 10 (Autonomous Systems)",
        "audience": {"risk", "manager", "admin"},
        "doc": "dfsa_ai_governance.md", "doc_title": "DFSA AI Governance Summary",
    },
    {
        "label": "DIFC Data Protection Law No. 5 of 2020",
        "audience": {"risk", "admin"},
        "doc": "compliance_signoff.md", "doc_title": "Compliance & Trade Sign-Off Framework",
    },
]

# Ticker → GICS-style sector. Unknown tickers fall back to an asset-class sector.
_SECTOR_MAP: dict[str, str] = {
    "MSFT": "Information Technology", "AAPL": "Information Technology",
    "NVDA": "Information Technology", "V": "Information Technology",
    "GOOGL": "Communication Services",
    "JPM": "Financials", "BRK-B": "Financials",
    "UNH": "Health Care", "JNJ": "Health Care",
    "PG": "Consumer Staples",
    "HD": "Consumer Discretionary",
    "XOM": "Energy",
    "SPY": "US Broad Equity", "VEA": "Intl Developed Equity",
    "VWO": "Emerging Markets Equity",
    "AGG": "Fixed Income", "LQD": "Fixed Income", "TLT": "Fixed Income",
    "TIP": "Fixed Income", "HYG": "Fixed Income",
    "GLD": "Commodities",
    "BTC-USD": "Digital Assets", "ETH-USD": "Digital Assets",
    "USD.CASH": "Cash",
}

_ASSET_CLASS_SECTOR = {
    "Equity": "Equities (unclassified)",
    "ETF": "Diversified ETF",
    "Fixed Income": "Fixed Income",
    "Commodity": "Commodities",
    "Crypto": "Digital Assets",
    "Cash": "Cash",
}

# Single-issuer asset classes the single-name concentration limit applies to.
_SINGLE_ISSUER = ("Equity", "Crypto")


def _sector_for(ticker: str, asset_class: str) -> str:
    return _SECTOR_MAP.get(ticker) or _ASSET_CLASS_SECTOR.get(asset_class, "Other")


def _role_sees(audience: set[str], role: str) -> bool:
    return role == "admin" or role in audience


# ── Graph container ───────────────────────────────────────────────────────────

@dataclass
class Graph:
    role: str
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)

    def add_node(self, node_id: str, ntype: str, label: str, **attrs) -> str:
        existing = self.nodes.get(node_id)
        if existing:
            existing["attrs"].update({k: v for k, v in attrs.items() if v is not None})
        else:
            self.nodes[node_id] = {"id": node_id, "type": ntype, "label": label, "attrs": dict(attrs)}
        return node_id

    def add_edge(self, src: str, rel: str, dst: str, detail: str = "", source: str = "") -> None:
        if src not in self.nodes or dst not in self.nodes:
            return
        self.edges.append({"src": src, "rel": rel, "dst": dst, "detail": detail, "source": source})

    def neighbors(self, node_id: str) -> list[str]:
        out: set[str] = set()
        for e in self.edges:
            if e["src"] == node_id:
                out.add(e["dst"])
            elif e["dst"] == node_id:
                out.add(e["src"])
        return list(out)

    def is_empty(self) -> bool:
        return not self.nodes


# ── Access resolution ─────────────────────────────────────────────────────────

def _access(role: str) -> dict[str, bool]:
    u = User(email="graph-builder", role=role)
    return {
        "portfolio": check_permission(u, "portfolio").allowed,
        "trades":    check_permission(u, "trades").allowed,
        "risk":      check_permission(u, "risk_alerts").allowed,
        "summary":   check_permission(u, "summary").allowed,
    }


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph(role: str, db_path: Optional[str] = None) -> Graph:
    """Build the role-filtered knowledge graph from live data + policy ontology."""
    g = Graph(role=role)
    if not graph_enabled():
        return g

    acc = _access(role)
    if not any(acc.values()):          # intern — no data access
        return g

    ticker_level  = acc["portfolio"]   # analyst, risk, admin
    summary_level = acc["summary"]      # analyst, risk, manager, admin

    try:
        con = connect(db_path)
    except sqlite3.Error:
        return g

    try:
        holdings = con.execute(
            "SELECT ticker, name, asset_class, weight_pct, market_value, "
            "current_price, avg_cost FROM portfolio_holdings"
        ).fetchall()
    except sqlite3.Error:
        holdings = []

    total_mv = sum(h["market_value"] for h in holdings) or 1.0
    class_weight: dict[str, float] = {}
    sector_weight: dict[str, float] = {}

    for h in holdings:
        ac = h["asset_class"]
        sector = _sector_for(h["ticker"], ac)
        class_weight[ac] = class_weight.get(ac, 0.0) + (h["weight_pct"] or 0.0)
        sector_weight[sector] = sector_weight.get(sector, 0.0) + (h["weight_pct"] or 0.0)

    # Asset-class and sector nodes are visible to summary-level roles (managers
    # included) since they carry no position-level detail.
    if ticker_level or summary_level:
        for ac, w in class_weight.items():
            g.add_node(f"asset_class:{ac}", "asset_class", ac, weight_pct=round(w, 2))
        for sec, w in sector_weight.items():
            g.add_node(f"sector:{sec}", "sector", sec, weight_pct=round(w, 2))

    # Ticker nodes + structural edges are position-level: analyst/risk/admin only.
    if ticker_level:
        book_id = g.add_node(
            "portfolio:book", "portfolio", "Managed Account (USD 1m)",
            total_aum=round(total_mv, 2), positions=len(holdings),
        )
        for h in holdings:
            ac = h["asset_class"]
            sector = _sector_for(h["ticker"], ac)
            pnl_pct = None
            if h["avg_cost"]:
                pnl_pct = round((h["current_price"] - h["avg_cost"]) / h["avg_cost"] * 100, 2)
            tid = g.add_node(
                f"ticker:{h['ticker']}", "ticker", h["ticker"],
                name=h["name"], asset_class=ac, weight_pct=h["weight_pct"], pnl_pct=pnl_pct,
            )
            g.add_edge(tid, IN_ASSET_CLASS, f"asset_class:{ac}", source="live:holdings")
            g.add_edge(tid, IN_SECTOR, f"sector:{sector}", source="live:holdings")
            g.add_edge(tid, IN_PORTFOLIO, book_id, detail=f"{h['weight_pct']:.1f}% of AUM",
                       source="live:holdings")

    # ── Risk rules (live thresholds from DB + curated governance linkage) ──────
    try:
        rule_rows = con.execute(
            "SELECT rule_name, description, threshold, metric, severity "
            "FROM risk_rules WHERE active=1"
        ).fetchall()
    except sqlite3.Error:
        rule_rows = []

    rules: dict[str, dict] = {}
    for r in rule_rows:
        name = r["rule_name"]
        meta = _POLICY_RULES.get(name, {"audience": {"risk", "admin"}})
        if not _role_sees(meta.get("audience", {"risk", "admin"}), role):
            continue
        rules[name] = {
            "threshold": r["threshold"], "metric": r["metric"],
            "severity": r["severity"], "description": r["description"], **meta,
        }
        rid = g.add_node(
            f"rule:{name}", "rule", name,
            threshold=r["threshold"], metric=r["metric"], severity=r["severity"],
        )
        doc = meta.get("doc")
        if doc:
            did = g.add_node(f"policy:{doc}", "policy", meta.get("doc_title", doc), file=doc)
            g.add_edge(rid, DEFINED_IN, did, source=doc)
        approver = meta.get("approver")
        if approver and acc["risk"]:
            aid = g.add_node(f"role:{approver}", "role", approver)
            g.add_edge(rid, REQUIRES_SIGNOFF, aid, detail=meta.get("escalation", ""), source=doc or "")

    # ── Derived breach edges (live evaluation against the limits) ──────────────
    if ticker_level and "max_single_position" in rules:
        thr = rules["max_single_position"]["threshold"]
        for h in holdings:
            if h["asset_class"] not in _SINGLE_ISSUER:
                continue
            w = h["weight_pct"] or 0.0
            rel = BREACHES if w > thr else WITHIN_LIMIT
            g.add_edge(f"ticker:{h['ticker']}", rel, "rule:max_single_position",
                       detail=f"{w:.1f}% vs {thr:.0f}% limit", source="live:holdings")

    if "max_crypto_exposure" in rules and "asset_class:Crypto" in g.nodes:
        thr = rules["max_crypto_exposure"]["threshold"]
        w = class_weight.get("Crypto", 0.0)
        rel = BREACHES if w > thr else WITHIN_LIMIT
        g.add_edge("asset_class:Crypto", rel, "rule:max_crypto_exposure",
                   detail=f"{w:.1f}% vs {thr:.0f}% limit", source="risk_limits_policy.md")

    if ticker_level and "max_top3_concentration" in rules:
        thr = rules["max_top3_concentration"]["threshold"]
        top3 = sorted(holdings, key=lambda x: x["weight_pct"] or 0.0, reverse=True)[:3]
        top3_w = sum(h["weight_pct"] or 0.0 for h in top3)
        names = ", ".join(h["ticker"] for h in top3)
        rel = BREACHES if top3_w > thr else WITHIN_LIMIT
        g.add_edge("portfolio:book", rel, "rule:max_top3_concentration",
                   detail=f"top-3 ({names}) {top3_w:.1f}% vs {thr:.0f}% limit",
                   source="risk_limits_policy.md")

    # ── Trades / surveillance (risk + admin) ───────────────────────────────────
    if acc["trades"]:
        try:
            trades = con.execute(
                "SELECT id, ticker, direction, quantity, price, notional, trader, "
                "status, risk_score, notes FROM trades "
                "ORDER BY traded_at DESC LIMIT ?", (graph_trade_limit(),)
            ).fetchall()
        except sqlite3.Error:
            trades = []

        for t in trades:
            tid = g.add_node(
                f"trade:{t['id']}", "trade",
                f"{t['direction']} {t['ticker']} #{t['id']}",
                status=t["status"], notional=t["notional"], risk_score=t["risk_score"],
            )
            # Ensure the ticker exists even if it is no longer a holding.
            tk = g.add_node(f"ticker:{t['ticker']}", "ticker", t["ticker"])
            g.add_edge(tid, ON_TICKER, tk, source="live:trades")
            if t["trader"]:
                trd = g.add_node(f"trader:{t['trader']}", "trader", t["trader"])
                g.add_edge(tid, EXECUTED_BY, trd, source="live:trades")

            score = t["risk_score"] or 0.0
            if "high_risk_score" in rules:
                thr = rules["high_risk_score"]["threshold"]
                if score >= thr or t["status"] == "FLAGGED":
                    g.add_edge(tid, FLAGGED_BY, "rule:high_risk_score",
                               detail=f"risk {score:.2f} ≥ {thr:.2f} ({rules['high_risk_score']['severity']})",
                               source="trade_surveillance.md")
            if "large_notional_trade" in rules:
                thr = rules["large_notional_trade"]["threshold"]
                if (t["notional"] or 0) >= thr:
                    g.add_edge(tid, BREACHES, "rule:large_notional_trade",
                               detail=f"USD {t['notional']:,.0f} ≥ USD {thr:,.0f}",
                               source="trade_surveillance.md")

    # ── Regulations / autonomous-systems governance ────────────────────────────
    reg_visible = [r for r in _REGULATIONS if _role_sees(r["audience"], role)]
    if reg_visible:
        sys_id = g.add_node("system:arp_agents", "system", "ARP AI agents")
        for reg in reg_visible:
            rid = g.add_node(f"regulation:{reg['label']}", "regulation", reg["label"])
            g.add_edge(rid, GOVERNS, sys_id, source=reg["doc"])
            did = g.add_node(f"policy:{reg['doc']}", "policy", reg["doc_title"], file=reg["doc"])
            g.add_edge(rid, DEFINED_IN, did, source=reg["doc"])

    con.close()
    return g


# ── Question → seed-node matching ─────────────────────────────────────────────

_SECTOR_SYNONYMS = {
    "tech": "Information Technology", "technology": "Information Technology",
    "it ": "Information Technology",
    "financ": "Financials", "bank": "Financials",
    "health": "Health Care", "healthcare": "Health Care",
    "staple": "Consumer Staples",
    "discretionary": "Consumer Discretionary", "retail": "Consumer Discretionary",
    "communication": "Communication Services",
    "energy": "Energy", "oil": "Energy",
    "gold": "Commodities", "commodit": "Commodities",
    "digital asset": "Digital Assets",
}

_CLASS_SYNONYMS = {
    "equit": "Equity", "stock": "Equity",
    "etf": "ETF",
    "fixed income": "Fixed Income", "bond": "Fixed Income", "treasury": "Fixed Income",
    "duration": "Fixed Income", "credit": "Fixed Income",
    "commodit": "Commodity",
    "crypto": "Crypto", "digital asset": "Crypto", "bitcoin": "Crypto",
    "cash": "Cash",
}

_RULE_SYNONYMS = {
    "single name": "max_single_position", "single position": "max_single_position",
    "position limit": "max_single_position", "concentrat": "max_single_position",
    "overweight": "max_single_position", "overexpos": "max_single_position",
    "top 3": "max_top3_concentration", "top-3": "max_top3_concentration",
    "top three": "max_top3_concentration",
    "crypto": "max_crypto_exposure", "digital asset": "max_crypto_exposure",
    "notional": "large_notional_trade", "large trade": "large_notional_trade",
    "block trade": "large_notional_trade", "50k": "large_notional_trade",
    "risk score": "high_risk_score", "flag": "high_risk_score",
    "suspicious": "high_risk_score",
    "turnover": "max_daily_turnover",
}

_TICKER_ALIASES = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "alphabet": "GOOGL",
    "google": "GOOGL", "jpmorgan": "JPM", "berkshire": "BRK-B", "exxon": "XOM",
    "bitcoin": "BTC-USD", "gold": "GLD",
}


def _seed_nodes(g: Graph, question: str) -> list[str]:
    q = f" {question.lower()} "
    seeds: set[str] = set()

    # Tickers: alias hits and direct symbol matches against graph nodes.
    for alias, ticker in _TICKER_ALIASES.items():
        if alias in q and f"ticker:{ticker}" in g.nodes:
            seeds.add(f"ticker:{ticker}")
    for nid, node in g.nodes.items():
        if node["type"] == "ticker":
            sym = node["label"].lower()
            if re.search(rf"\b{re.escape(sym)}\b", q):
                seeds.add(nid)

    for kw, sector in _SECTOR_SYNONYMS.items():
        if kw in q and f"sector:{sector}" in g.nodes:
            seeds.add(f"sector:{sector}")
    for kw, ac in _CLASS_SYNONYMS.items():
        if kw in q and f"asset_class:{ac}" in g.nodes:
            seeds.add(f"asset_class:{ac}")
    for kw, rule in _RULE_SYNONYMS.items():
        if kw in q and f"rule:{rule}" in g.nodes:
            seeds.add(f"rule:{rule}")

    if any(w in q for w in ("sign-off", "sign off", "approve", "approval",
                            "regulat", "dfsa", "governance", "compliance")):
        seeds.update(nid for nid, n in g.nodes.items()
                     if n["type"] in ("regulation", "role"))
    return list(seeds)


def _expand(g: Graph, seeds: list[str], hops: int) -> set[str]:
    reached = set(seeds)
    frontier = set(seeds)
    for _ in range(max(hops, 0)):
        nxt: set[str] = set()
        for nid in frontier:
            for nb in g.neighbors(nid):
                if nb not in reached:
                    nxt.add(nb)
        reached |= nxt
        frontier = nxt
        if not frontier:
            break
    return reached


def _score_edge(edge: dict, seeds: set[str], reachable: set[str]) -> int:
    score = 0
    if edge["src"] in seeds or edge["dst"] in seeds:
        score += 3
    if edge["rel"] == BREACHES:
        score += 3
    elif edge["rel"] in (FLAGGED_BY, REQUIRES_SIGNOFF):
        score += 2
    if edge["src"] in reachable and edge["dst"] in reachable:
        score += 1
    return score


def _edge_to_fact(g: Graph, edge: dict) -> dict:
    return {
        "subject":  g.nodes[edge["src"]]["label"],
        "relation": edge["rel"],
        "object":   g.nodes[edge["dst"]]["label"],
        "detail":   edge["detail"],
        "source":   edge["source"],
    }


def _format_block(facts: list[dict]) -> str:
    if not facts:
        return ""
    lines = ["--- KNOWLEDGE GRAPH (entities and relationships; live data + policy) ---"]
    for f in facts:
        tail = ""
        bits = [b for b in (f["detail"], f"source: {f['source']}" if f["source"] else "") if b]
        if bits:
            tail = f" ({'; '.join(bits)})"
        lines.append(f"{f['subject']} -[{f['relation']}]-> {f['object']}{tail}")
    lines.append("--- END KNOWLEDGE GRAPH ---")
    return "\n".join(lines)


def build_context_block(
    role: str,
    question: str,
    db_path: Optional[str] = None,
    max_facts: Optional[int] = None,
) -> tuple[str, list[dict]]:
    """
    Return (graph_context_string, graph_facts) relevant to the question.
    Mirrors rag.build_context: a text block for the LLM plus a structured
    fact list for dashboard transparency.
    """
    if not graph_enabled() or not question.strip():
        return "", []

    g = build_graph(role, db_path)
    if g.is_empty():
        return "", []

    cap = max_facts or graph_max_facts()
    seeds = _seed_nodes(g, question)
    reachable = _expand(g, seeds, graph_max_hops()) if seeds else set()

    if seeds:
        candidates = [
            e for e in g.edges
            if (e["src"] in seeds or e["dst"] in seeds
                or (e["src"] in reachable and e["dst"] in reachable))
        ]
    else:
        # No entity match — surface the most decision-relevant facts: active
        # breaches and flags first, otherwise the largest exposures.
        candidates = [e for e in g.edges if e["rel"] in (BREACHES, FLAGGED_BY)]
        if not candidates and g.edges:
            top_class = max(
                (n for n in g.nodes.values() if n["type"] == "asset_class"),
                key=lambda n: n["attrs"].get("weight_pct", 0), default=None,
            )
            if top_class:
                candidates = [e for e in g.edges
                              if top_class["id"] in (e["src"], e["dst"])]

    seed_set = set(seeds)
    ranked = sorted(
        candidates,
        key=lambda e: _score_edge(e, seed_set, reachable),
        reverse=True,
    )

    facts: list[dict] = []
    seen: set[tuple] = set()
    for e in ranked:
        key = (e["src"], e["rel"], e["dst"])
        if key in seen:
            continue
        seen.add(key)
        facts.append(_edge_to_fact(g, e))
        if len(facts) >= cap:
            break

    return _format_block(facts), facts


# ── Endpoint helpers ──────────────────────────────────────────────────────────

def neighbors(role: str, entity: str, db_path: Optional[str] = None) -> dict:
    """Return the immediate relationships of an entity for the given role."""
    if not graph_enabled():
        return {"allowed": True, "found": False, "reason": "graph disabled", "neighbors": []}

    g = build_graph(role, db_path)
    if g.is_empty():
        return {"allowed": False, "error": "No graph entities accessible for this role."}

    q = entity.strip().lower()
    match = None
    for nid, node in g.nodes.items():
        if node["label"].lower() == q or nid.lower() == q or nid.lower().endswith(f":{q}"):
            match = nid
            break
    if not match:
        for nid, node in g.nodes.items():
            if q and q in node["label"].lower():
                match = nid
                break
    if not match:
        return {"allowed": True, "found": False, "entity": entity, "neighbors": []}

    out = []
    for e in g.edges:
        if e["src"] == match:
            out.append({"relation": e["rel"], "direction": "out",
                        "node": g.nodes[e["dst"]]["label"],
                        "type": g.nodes[e["dst"]]["type"], "detail": e["detail"]})
        elif e["dst"] == match:
            out.append({"relation": e["rel"], "direction": "in",
                        "node": g.nodes[e["src"]]["label"],
                        "type": g.nodes[e["src"]]["type"], "detail": e["detail"]})
    node = g.nodes[match]
    return {
        "allowed": True, "found": True,
        "entity": node["label"], "node_type": node["type"], "attrs": node["attrs"],
        "neighbors": out, "count": len(out),
    }


def stats(role: str, db_path: Optional[str] = None) -> dict:
    """Node/edge counts for the requesting role's subgraph."""
    g = build_graph(role, db_path)
    if g.is_empty():
        return {"allowed": False,
                "error": "No knowledge-graph entities are accessible for this role."}

    by_type: dict[str, int] = {}
    for n in g.nodes.values():
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    by_rel: dict[str, int] = {}
    for e in g.edges:
        by_rel[e["rel"]] = by_rel.get(e["rel"], 0) + 1
    return {
        "allowed": True,
        "nodes": len(g.nodes),
        "edges": len(g.edges),
        "node_types": dict(sorted(by_type.items())),
        "relationships": dict(sorted(by_rel.items())),
        "max_hops": graph_max_hops(),
        "max_facts": graph_max_facts(),
    }


def status(db_path: Optional[str] = None, *, lite: bool = False) -> dict:
    """Knowledge-graph subsystem status for /health and observability."""
    if not graph_enabled():
        return {"enabled": False}
    if lite:
        return {"enabled": True, "lite": True}
    g = build_graph("admin", db_path)
    by_type: dict[str, int] = {}
    for n in g.nodes.values():
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    return {
        "enabled":    True,
        "nodes":      len(g.nodes),
        "edges":      len(g.edges),
        "node_types": dict(sorted(by_type.items())),
        "max_hops":   graph_max_hops(),
        "max_facts":  graph_max_facts(),
        "source":     "live SQLite + curated policy ontology",
    }
