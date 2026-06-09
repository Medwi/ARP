# ARP Global Capital — Investment Intelligence Platform

Local AI-powered investment operations for a regulated asset-manager assessment. **FastAPI** backend, **Streamlit** dashboard, **SQLite** data layer (assessment; **PostgreSQL** is the named production target), and **on-device LLM** inference (Ollama) with tool-level RBAC, hash-chained audit logging, and transparency metadata on every agent response.

The seeded book is a **USD 1,000,000** diversified demo mandate (equities, ETFs, fixed income, gold, digital sleeve, cash). Figures are tagged `data_scope: demo_snapshot` — not live fund NAV.

NOTE: For quick testing or review, I recommend using the mock data (fixed, reliable, invariant) and admin role (most comprehensive, all features enabled). This configuration allows for a reliable and fast full overview of the platform.

---

## Tester access (start here)

Use these credentials after `./EXEC` or `docker compose up` — seed always applies this fixed token set. Paste the **Bearer token** into the Streamlit sidebar, or send it as `Authorization: Bearer <token>` on API calls.

| Role | Email | Bearer token |
|------|-------|----------------|
| **Admin** | `admin@local` | `c8e9c750de3d240b4d2e659a48b00f0c66ca534b4e544229` |
| **Analyst** | `analyst@local` | `3edf214346ede6b6593e6c66f54c008bc5cc275793c6a650` |
| **Risk** | `risk@local` | `69535fd68dcf0a91c043d5cc9e776cd1ce6a4437c26870b4` |
| **Manager** | `manager@local` | `e80cde4bb031633a8bb086c33900869ae66b763e4df4d239` |
| **Intern** | `intern@local` | `969bc0eac4285f06a8c6cd63cdd30b218aaab5447dbd6aac` |

**Quick test path**

1. Start the stack (see [Quick start](#quick-start) below).
2. Open [http://localhost:8501](http://localhost:8501).
3. Paste a token from the table → pick a role-appropriate tab (e.g. **Risk** → Trades, AI Agents, Audit Log).
4. Optional API check:

```bash
export TOKEN="69535fd68dcf0a91c043d5cc9e776cd1ce6a4437c26870b4"   # risk@local
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/me | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/portfolio/summary | python3 -m json.tool
```

**If a token is rejected:** confirm you are using a value from the table above, then re-run seed (`./EXEC`, `make seed-local`, or `docker compose up`) to reset the DB to the fixed demo set.

| Role | Typical access |
|------|----------------|
| Admin | Full platform — all data, agents, audit, metrics, seed actions |
| Risk | Portfolio, trades, risk, market, AI agents, audit, expansion ops |
| Analyst | Portfolio, market, AI agents only (no Trades / Risk / Audit nav) |
| Manager | **COO / Executive Summary only** — AUM, P&L, mix, top-3 weights, risk **counts**; no nav, blotter, audit, or AI. Intelligence workspace → use **risk** or **admin** (`docs/ROLES.md`) |
| Intern | No data — RBAC deny demo (API returns **403**) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         ARP PLATFORM — LAYERED STACK                          │
└──────────────────────────────────────────────────────────────────────────────┘

  PRESENTATION                          frontend/app.py  ·  expansion_views.py  :8501
  ─────────────────────────────────────────────────────────────────────────────
  Bearer auth · role-adaptive nav · Plotly · grounding / model metadata on agents

                                    │  REST + Bearer token
                                    ▼

  API GATEWAY                           backend/main.py  ·  expansion_routes.py  :8000
  ─────────────────────────────────────────────────────────────────────────────
  Lifespan checks · CORS · rate limit (memory or Redis) · request logging
  RBAC deny → HTTP 403 on REST · /ask keeps agent-style body for orchestration

                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            Portfolio agent                   Risk agent
            (keyword tool stage)              (keyword tool stage)
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
              run_agent(): route_tools (keyword score) → tool(s) → context → llm.chat_with_meta()
              Note: LLM does not select tools — production would add planner / MCP
              Context order: memory → knowledge graph → RAG → live JSON
              Transparency: tools_called, grounding, rag_sources, llm_model, memory_used

  CROSS-CUTTING
  ─────────────────────────────────────────────────────────────────────────────
  rbac.py · audit.py (v2 hash chain) · llm_audit_logs · prompt_safety.py · data_scope.py

  DATA & INFERENCE
  ─────────────────────────────────────────────────────────────────────────────
  SQLite (arp.db)          Ollama (phi3:mini + fallback)     market_data (mock/Yahoo/AV)
  knowledge/ + rag.py      on-device only                      optional external quotes

  BOOTSTRAP: seed/seed.py · scripts/ (healthcheck, backup, smoke, warm-model)
```

| Layer | Location | Responsibility |
|-------|----------|----------------|
| UI | `frontend/` | Core dashboard + expansion tabs (`ENABLE_ENHANCEMENTS=1`) |
| API | `backend/main.py`, `expansion_routes.py` | Auth, core + ~45 expansion routes |
| Orchestration | `agents/base.py`, `context.py` | Keyword-scored tool stage (not LLM tool-calling), grounding, LLM metadata |
| Agents | `portfolio`, `risk`, `briefing`, `cio_digest`, … | NL workflows and scheduled-style digests |
| Tools | `backend/tools/*` | RBAC-gated DB/domain functions |
| Security | `rbac.py`, `audit.py`, `prompt_safety.py` | Permissions, tamper-evident logs, prompt hardening |
| Ops | `middleware/`, `scripts/`, `.github/workflows/ci.yml` | Rate limits, backups, CI (fundamental + expansion + Trivy) |

All application code lives under **`backend/`** and **`frontend/`** (including Intelligence & operations routes in `backend/expansion_routes.py`).

---

## How it works

### REST API

1. Client sends `Authorization: Bearer <token>`.
2. `resolve_token()` hashes the token and matches `users.token` (SHA-256 digest).
3. Tool functions call `check_permission(user, resource)`.
4. On deny, REST routes raise **HTTP 403** (not `200` with `allowed: false`).
5. Successful portfolio/reporting payloads include `data_scope`, `data_scope_note`, and `synthetic` where applicable.

### AI agents (`POST /ask`)

1. User picks **Portfolio** or **Risk** agent and submits a question.
2. `route_tools()` **keyword-scores** predefined rules and may invoke **up to two RBAC-gated tools** (e.g. summary + market movers). This is a deterministic pre-stage — **not** LLM tool-calling or a planner loop.
3. `build_agent_context()` merges memory → graph → RAG → live tool JSON.
4. `llm.chat_with_meta()` synthesizes an answer from that context; fallback model if primary fails. The model does **not** choose which tools run.
5. Response includes `tool_called`, `tools_called`, `grounding`, `llm_model`, `llm_fallback_used`, `rag_sources`, `graph_facts`, `memory_used`.
6. One audit row per tool + LLM call logged to `llm_audit_logs` when configured.

**Demo vs production orchestration:** the tool registry, RBAC checks, audit hooks, and transparency fields are production-shaped. Only the router is simplified for assessment reliability. A production deployment would replace keyword scoring with an **LLM planner** or **MCP tool layer** while keeping the same `backend/tools/*` functions.

### Data scope & pipeline (investment ops honesty)

| Concept | Meaning |
|---------|---------|
| `ARP_DATA_SCOPE=demo_snapshot` | Book is seeded demo data — default for this assessment |
| `pipeline health` | Broadridge-style **sync heartbeat** (`pipeline_runs`) |
| `book_freshness` | Separate from pipeline — demo book stays `DEMO · pipeline …` even after simulate-sync |
| Simulate sync | Records metrics only; `book_mutated: false` |

See `docs/PERSISTENCE.md` for the full persistence story.

**Assessment vs production persistence:** this deliverable uses **SQLite** on a seeded demo book (`ARP_PERSISTENCE=sqlite_demo`). It is not HA-ready, does not support safe multi-writer API replicas, and relies on manual backup (`make backup-db`) without a defined production RPO. **PostgreSQL** is the named production target (`ARP_PERSISTENCE=postgresql`) for failover, concurrent writers, and managed backup/PITR — the tool/RBAC/audit layer stays the same.

### Rate limiting

Default **`RATE_LIMIT_BACKEND=memory`** — appropriate for a **single API worker** (local `./EXEC`, default `docker compose up`). Counters live in process memory and are lost on restart. For **multiple API replicas**, use **`docker-compose.prod.yml`** (`make up-prod`), which sets **`RATE_LIMIT_BACKEND=redis`** and a shared Redis instance so all workers enforce the same per-token windows.

---

## Quick start

### Prerequisites

- **Python 3.11+**
- **[Ollama](https://ollama.com/)** (for AI answers only — portfolio/trades/risk work without it; see `docs/OLLAMA.md`)
- **Docker + Compose** (optional)

### Option A — Local launcher (recommended)

```bash
git clone <repository-url>
cd ARP
./SETUP          # once: venv, deps, .env, data/
./EXEC           # seed + API :8000 + dashboard :8501
```

Pull models for AI (default `phi3:mini`):

```bash
ollama pull phi3:mini
ollama pull nomic-embed-text    # optional — RAG embeddings; hash fallback works offline
```

Open **[http://localhost:8501](http://localhost:8501)** and paste a [tester token](#tester-access-start-here).

### Option B — Docker Compose

> **First start:** Compose waits on Ollama `service_healthy` (often **1–3 minutes** on a cold machine) before seed/backend start — this is normal, not a failed build. After the stack is up, **pull the model** or AI Agents will show `[LLM UNAVAILABLE]` while other tabs still work. See `docs/OLLAMA.md`.

```bash
cp .env.example .env
docker compose up --build
# In another terminal once arp_ollama is running:
docker exec arp_ollama ollama pull phi3:mini
make warm-model
# Login tokens: see [tester table](#tester-access-start-here) above
```

| URL | Purpose |
|-----|---------|
| [http://localhost:8501](http://localhost:8501) | Streamlit dashboard |
| [http://localhost:8000/docs](http://localhost:8000/docs) | Swagger (disable in prod: `ARP_DOCS_PUBLIC=0`) |
| [http://localhost:8000/health](http://localhost:8000/health) | Liveness; detail may require Bearer |

**Production-shaped overlay** (Redis rate limits + TLS proxy on `:8443`):

```bash
make tls-certs
make up-prod    # docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

### Option C — Manual (two terminals)

**Terminal A — API**

```bash
source .venv/bin/activate
export DB_PATH=./data/arp.db OLLAMA_HOST=http://localhost:11434 OLLAMA_MODEL=phi3:mini
python seed/seed.py
uvicorn backend.main:app --reload --port 8000
```

**Terminal B — Dashboard**

```bash
source .venv/bin/activate
export API_URL=http://localhost:8000 ENABLE_ENHANCEMENTS=1
streamlit run frontend/app.py --server.port 8501
```

### Pre-demo checklist

```bash
make healthcheck     # API, DB, Ollama, RAG/graph/memory, audit chain
make smoke           # authenticated API smoke (no UI)
make warm-model      # Ollama cold-start (do before AI demo — see docs/OLLAMA.md)
make backup-db       # SQLite hot backup
```

Keep **`MARKET_DATA_SOURCE=mock`** (default) for walkthroughs — live Yahoo/Alpha Vantage sets `external_calls: true` on `/health` and the Compliance export. See `docs/MARKET_DATA.md`.

---

## Environment variables

Copy `.env.example` → `.env`. **Never commit `.env`.**

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `./data/arp.db` | SQLite database path |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API |
| `OLLAMA_MODEL` | `phi3:mini` | Primary LLM |
| `OLLAMA_FALLBACK_MODEL` | `llama3.2:latest` | Used if primary fails |
| `API_URL` | `http://localhost:8000` | Backend URL for Streamlit |
| `ENABLE_ENHANCEMENTS` | `1` | Briefing, Research, Reporting, Operations, … |
| `ARP_DATA_SCOPE` | `demo_snapshot` | `demo_snapshot` or `live` |
| `ARP_PERSISTENCE` | `sqlite_demo` | `sqlite_demo` or `postgresql` (target) |
| `MARKET_DATA_SOURCE` | `mock` | `mock` (offline, recommended demo), `yahoo`, `alphavantage` — live sources set `external_calls: true`; see `docs/MARKET_DATA.md` |
| `ENABLE_RAG` / `VECTOR_DB` | `1` / `local` | Policy RAG; `sqlite` for DB-backed vectors |
| `ENABLE_GRAPH` / `MEMORY_ENABLED` | `1` / `1` | Knowledge graph + agent memory |
| `ENABLE_RATE_LIMITER` | `1` | Per-token limits (`RATE_LIMIT_BACKEND=memory` default; Redis in prod overlay) |
| `ARP_HEALTH_PUBLIC_DETAIL` | `0` | `1` = full `/health` without auth |
| `ARP_DOCS_PUBLIC` | `1` | `0` = hide `/docs` in production |

Full list: `.env.example`.

---

## RAG, graph, and memory

- **RAG** (`knowledge/*.md`, `backend/rag.py`) — on-device embeddings + keyword fallback; role-filtered; `grounding` quality on agent responses.
- **Knowledge graph** (`backend/graph.py`) — live holdings/trades/rules linked to policy ontology; `graph_facts` on `/ask`.
- **Memory** (`backend/memory.py`) — per-user recall; sanitised on store/recall; `GET/DELETE /memory`.

---

## API overview

All protected routes: `Authorization: Bearer <token>`. Interactive docs: `/docs`.

**Core:** `/health`, `/me`, `/ask`, `/memory`, `/graph/*`, `/portfolio/*`, `/trades/*`, `/risk/*`, `/market/*`, `/audit/*`, `/metrics`

**Expansion (selection):** `/briefing`, `/digest`, `/research/*`, `/pipeline/*`, `/portfolio/attribution`, `/reporting/manager-accounts`, `/report/compliance`, `/shadow-book/*`, `/crm/*`, `/trade/*`, `/analysts/*`

Example:

```bash
export TOKEN="69535fd68dcf0a91c043d5cc9e776cd1ce6a4437c26870b4"

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/portfolio/summary
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"What are our top holdings?","agent":"portfolio"}' \
  http://localhost:8000/ask
```

---

## AI agents & tools

| Agent | Tools (keyword-scored pre-stage; up to 2 per question) |
|-------|--------------------------------------------------------|
| Portfolio | `get_portfolio_summary`, `get_asset_exposure`, `get_market_movers` |
| Risk | `get_recent_trades`, `get_risk_alerts`, `get_flagged_trades`, `check_overexposure` |

**Not in scope for this demo:** native LLM tool-calling (function calling), ReAct loops, or dynamic multi-step planners. Those would sit in front of the same tool functions in production.

Expansion modules (REST only, not `/ask` routing) include briefing, CIO digest, research lake, attribution, pipeline monitor, manager reporting, shadow book, CRM stub, trade optimizer, position sizing, and compliance export — see Swagger for the full list.

---

## Mandate limits

Defined in `backend/risk_constants.py`, seeded into `risk_rules`:

| Rule | Threshold |
|------|-----------|
| `max_single_position` | 8% AUM |
| `max_crypto_exposure` | 5% AUM |
| `large_notional_trade` | USD 50,000 |
| `high_risk_score` | 0.80 |
| `max_top3_concentration` | 30% AUM |

---

## Project structure

```
ARP/
├── backend/           # Canonical API, agents, tools, middleware
├── frontend/          # Streamlit UI
├── seed/              # Database + RAG index bootstrap
├── db/schema.sql      # Core schema
├── knowledge/         # Policy corpus
├── tests/             # pytest (fundamental + expansion markers)
├── scripts/           # smoke, healthcheck, backup, warm-model
├── deploy/tls/        # Optional nginx TLS for docker-compose.prod.yml
├── docs/PERSISTENCE.md · ROLES.md · RISK_MODELS.md · MARKET_DATA.md · OLLAMA.md · DEPENDENCY_SCANNING.md
├── docker-compose.yml
├── docker-compose.prod.yml
├── SETUP · EXEC · Makefile
└── .env.example
```

---

## Testing

```bash
source .venv/bin/activate && pip install -r requirements.txt

make test              # 116 fundamental tests
make test-expansion    # 206 expansion tests
make test-all          # 322 total

make smoke             # API smoke (authenticated routes)
```

CI (GitHub Actions): `make security` (bandit + Safety with `.safety-policy.yml`), fundamental tests + coverage, expansion tests, Docker build + backup-in-image check, Trivy HIGH/CRITICAL gate. See `docs/DEPENDENCY_SCANNING.md` — always use the policy file for Safety (bare `safety check` will flag accepted Streamlit CVEs).

---

## Security controls

| Control | Implementation |
|---------|----------------|
| Auth | Fixed demo tokens in README; stored as SHA-256 digests; mirrored in `data/.bootstrap_tokens` |
| RBAC | `check_permission()` in every tool; REST deny → 403 |
| Audit | Append-only `audit_logs` (v2 chain) + `llm_audit_logs` |
| Prompt safety | Delimiter blocks; sanitise memory + research ingest |
| Data residency | LLM on-device; `external_calls: false` on `mock` (demo default) — see `docs/MARKET_DATA.md` |
| Rate limiting | In-memory default; Redis via `make up-prod` — see [Rate limiting](#rate-limiting) |
| Persistence | SQLite demo file; PostgreSQL named production target — see `docs/PERSISTENCE.md` |
| Dependencies | Safety + `.safety-policy.yml` (`make security`); Trivy on Docker image — see `docs/DEPENDENCY_SCANNING.md` |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Invalid token` | Re-run seed; use [tester table](#tester-access-start-here) |
| `403` on portfolio/trades | Expected for `intern@local` / wrong role — switch token |
| “Where’s the COO briefing?” | Manager = Executive Summary only; use **risk** or **admin** for Intelligence workspace — `docs/ROLES.md` |
| `docker compose up` slow / stuck | Waiting on Ollama healthy (~60s+); see `docs/OLLAMA.md` |
| Ollama offline / `[LLM UNAVAILABLE]` | `docker exec arp_ollama ollama pull phi3:mini` then `make warm-model`; non-AI tabs still work |
| Dashboard can't reach API | `API_URL=http://localhost:8000` (local) or `http://backend:8000` (Docker) |
| Inflated AUM after re-seed | `rm ./data/arp.db && ./EXEC` |
| Pipeline shows HEALTHY but book is demo | By design — see `book_freshness` on `/pipeline/status` |
| VaR / stress / `RISK_REPORT` look “live” | Illustrative demo on seeded book — read captions; see `docs/RISK_MODELS.md` |

---

## Licence

Proprietary — ARP Global Capital technical assessment deliverable.
