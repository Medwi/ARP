# Run the fundamentals from your terminal

Step-by-step guide to run the **basic, core** ARP Investment Intelligence Platform locally. All commands assume the **repository root** — the folder that contains `backend/`, `frontend/`, and `docker-compose.yml`.

```bash
cd path/to/arp   # repository root (contains backend/, frontend/, docker-compose.yml)
```

---

## Recommended order

1. **Option 1** — `make test` + `make smoke` (confirm core code)
2. **Option 2** — `docker compose up --build` (full mockup)
3. Paste a token from **README.md** → open http://localhost:8501

---

## Option 1 — Fastest check (no Docker, ~1 minute)

Verifies core code only: RBAC, tools, REST APIs. **No dashboard, no Ollama required.**

### One-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
```

### Run fundamental tests (116 tests)

```bash
pytest tests/ -m fundamental -v
```

### End-to-end smoke (seed DB + hit core APIs)

```bash
python scripts/smoke.py
```

### Or use Make

```bash
make test
make smoke
```

---

## Option 2 — Full demo (Docker: API + UI + sample data)

Use this for a **mockup** with charts, sample fund data, and login.

> **Warning — first Docker start:** `docker compose up` waits for Ollama to become healthy before seed/backend start (**often 1–3 minutes**). The UI will not load until then. Ollama “healthy” does **not** mean `phi3:mini` is pulled — without `ollama pull`, **AI Agents show `[LLM UNAVAILABLE]`** but Portfolio/Trades/Risk/Market still work. See `docs/OLLAMA.md`.

```bash
cp .env.example .env
docker compose up --build
```

In a **second terminal** (after `arp_ollama` is running):

```bash
# Required for AI Agents — Ollama healthy ≠ model downloaded
docker exec arp_ollama ollama pull phi3:mini
make warm-model

# Optional — RAG embeddings when ENABLE_RAG=1 (default)
docker exec arp_ollama ollama pull nomic-embed-text

# Login: paste a token from the README.md tester table (risk@local is a good default)
```

### Open in browser

| URL | Purpose |
|-----|---------|
| http://localhost:8501 | Streamlit dashboard — paste token in sidebar |
| http://localhost:8000/docs | FastAPI Swagger UI |
| http://localhost:8000/health | Health check (no auth) |

Quick health check from terminal:

```bash
curl http://localhost:8000/health
```

### Stop the stack

```bash
docker compose down
```

---

## Option 3 — Terminal-only API (no Streamlit)

Backend + sample data only; good for API debugging.

### Terminal A — start API

```bash
source .venv/bin/activate
pip install -r requirements.txt

export DB_PATH=/tmp/arp.db
python seed/seed.py

# Optional: only needed for natural-language AI answers
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODEL=phi3:mini

uvicorn backend.main:app --reload --port 8000
```

Bearer tokens are the fixed demo values in **README.md** (tester table).

### Terminal B — call the API

```bash
export TOKEN="69535fd68dcf0a91c043d5cc9e776cd1ce6a4437c26870b4"   # risk@local — see README

curl -s http://localhost:8000/health | python3 -m json.tool

curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/portfolio/summary | python3 -m json.tool

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"What are our top holdings?","agent":"portfolio"}' \
  http://localhost:8000/ask | python3 -m json.tool
```

### Executive (COO) view via API

The **manager** role is the COO persona — one Executive Summary screen in the UI, not the full dashboard. For Intelligence & operations (briefing, compliance, etc.), use **risk@local** or **admin@local**. See `docs/ROLES.md`.

Use the **`manager@local`** token from the README tester table:

```bash
export MANAGER_TOKEN="e80cde4bb031633a8bb086c33900869ae66b763e4df4d239"

curl -s -H "Authorization: Bearer $MANAGER_TOKEN" \
  http://localhost:8000/portfolio/summary | python3 -m json.tool

curl -s -H "Authorization: Bearer $MANAGER_TOKEN" \
  http://localhost:8000/risk/alerts | python3 -m json.tool
```

The risk response returns **counts only** (no rule or trade detail) — executive summary mode.

---

## Option 4 — UI locally (API + Streamlit in two terminals)

### Terminal A

Same as Option 3: `uvicorn backend.main:app --reload --port 8000` after seeding.

### Terminal B — Streamlit

```bash
source .venv/bin/activate
pip install -r requirements.txt

export API_URL=http://localhost:8000
streamlit run frontend/app.py --server.port 8501
```

Open http://localhost:8501 and paste a token from the **README.md** tester table.

Expansion workspace defaults **on** (`.env`, `./EXEC`, Docker via `ENABLE_ENHANCEMENTS=1`). Set `ENABLE_ENHANCEMENTS=0` to hide **Intelligence & operations** (fundamentals-only deploys).

Under the **ARP GLOBAL CAPITAL** header, choose a workspace mode — **Book & markets** or **Intelligence & operations** — then pick a section. Each mode shows only its own right-hand panel (not both at once).

- **Book & markets:** Portfolio, Trades, Risk, Market, AI Agents, Audit Log (core fundamentals)
- **Intelligence & operations:** Briefing, Email, Research, Factors, Shadow Book, Reporting, Operations, Compliance (expansion panels in `frontend/expansion_views.py`)

Expansion panels are implemented in `frontend/expansion_views.py` and call routes in `backend/expansion_routes.py`.

---

## Which login to use

Seed always applies the **fixed demo tokens** in `README.md` (same values every run — no random generation).

| User | Bearer token (demo) | What you see |
|------|---------------------|----------------|
| `admin@local` | `c8e9c750de3d240b4d2e659a48b00f0c66ca534b4e544229` | **Full access** — all tabs, audit log, metrics, both agents, all API resources |
| `risk@local` | `69535fd68dcf0a91c043d5cc9e776cd1ce6a4437c26870b4` | Full operational dashboard: portfolio, trades, risk, market, AI agents, audit |
| `manager@local` | `e80cde4bb031633a8bb086c33900869ae66b763e4df4d239` | **COO view** — Executive Summary only (no workspace nav). Briefing/Reporting/Compliance → use **risk** or **admin** (`docs/ROLES.md`) |
| `analyst@local` | `3edf214346ede6b6593e6c66f54c008bc5cc275793c6a650` | Portfolio, market, AI agents; **no** trades, risk, or audit |
| `intern@local` | `969bc0eac4285f06a8c6cd63cdd30b218aaab5447dbd6aac` | Data access denied (RBAC demo) |

The seeded book is a **USD 1,000,000** diversified multi-asset managed account
(equities, ETFs, fixed income, gold, a small digital-asset sleeve, and cash).

---

## Market data source

Prices are chosen at seed time via `MARKET_DATA_SOURCE`:

| Value | Source | Notes |
|-------|--------|-------|
| `mock` (default) | Modelled, offline | No external calls; deterministic |
| `yahoo` | Yahoo Finance (`yfinance`) | Live delayed quotes |
| `alphavantage` | Alpha Vantage | Live; needs `ALPHA_VANTAGE_API_KEY` |

```bash
# Pick a source explicitly (or let ./EXEC prompt you)
MARKET_DATA_SOURCE=yahoo ./EXEC
MARKET_DATA_SOURCE=alphavantage ALPHA_VANTAGE_API_KEY=your_key ./EXEC
```

**Security:** API keys are read from the environment only. Put real keys in
`.env` (gitignored) — never commit them. With a live source, only **public
price quotes** are fetched; no client, position, or trade data leaves the
machine, and LLM inference is always on-device. The active source is shown in
the sidebar and on the Compliance tab.

**Demo tip:** keep **`MARKET_DATA_SOURCE=mock`** unless you want to discuss egress.
`yahoo` / `alphavantage` set **`external_calls: true`** on `/health`, `/metrics`, and
the Compliance pack export — the UI explains public quotes only, but reviewers will
still see the flag. Details: `docs/MARKET_DATA.md`.

---

## What “fundamentals” includes

| Layer | Included |
|-------|----------|
| Data | SQLite schema + `seed/seed.py` (holdings, trades, risk rules, users) |
| Auth | Bearer tokens, `GET /me` |
| RBAC | Tool-level allow/deny + audit logging |
| API | `/health`, `/portfolio/summary`, `/portfolio/exposure`, `/trades/recent`, `/risk/alerts`, `/risk/flagged`, `/market/movers`, `POST /ask`, `/audit/logs` |
| UI | Six tabs for analyst/risk; executive-only screen for manager |
| Tests | `pytest -m fundamental` (116 tests) |

Extended UI features stay behind `ENABLE_ENHANCEMENTS=1`. Intelligence & operations modules live in `backend/expansion_routes.py` and `frontend/expansion_views.py`, tested with `pytest -m expansion` (206 tests; **322** total with `make test-all`).

Rate limits use **in-memory** storage by default (single API worker). Multi-replica deploys: `make up-prod` enables **Redis**-backed limits — see README § Rate limiting.

---

## Make targets

| Command | Action |
|---------|--------|
| `make test` | Fundamental pytest suite (116 tests) |
| `make test-expansion` | Expansion module tests (206) |
| `make test-all` | Full pytest suite (322 total) |
| `make smoke` | `scripts/smoke.py` end-to-end check |
| `make security` | bandit + Safety (uses `.safety-policy.yml` — do not run bare `safety check`) |
| `make healthcheck` | Pre-demo stack check (API, DB, Ollama, RAG/graph/memory) |
| `make healthcheck-quick` | Same, skip Ollama inference probe |
| `make warm-model` | Ollama cold-start warm-up before demo |
| `make backup-db` | Manual SQLite hot backup (assessment RPO only — production uses PostgreSQL PITR) |
| `make rotate-tokens` | Rotate bearer tokens without full reseed |
| `make seed-local` | Seed DB at `$DB_PATH` or `/tmp/arp.db` |
| `make up` | `docker compose up --build` |
| `make down` | `docker compose down` |
| `make bootstrap-tokens` | Optional — dump `data/.bootstrap_tokens` (same values as README tester table) |
| `make logs-tokens` | Alias for `bootstrap-tokens` |

---

## Demo rate limits (LLM routes)

Briefing, digest, `/ask`, email triage, research Q&A, and investor letters share the
**ask** rate-limit bucket (`RATE_LIMIT_ASK_MAX` per `RATE_LIMIT_ASK_WINDOW` seconds,
default **120/min** in `.env.example` for walkthroughs).

| Symptom | Fix |
|---------|-----|
| HTTP **429** on AI Agents or Briefing | Wait for `retry_after` seconds, or set `RATE_LIMIT_ASK_MAX=120` in `.env` and restart the API |
| Heavy local testing | `ENABLE_RATE_LIMITER=0` in `.env` (re-enable before any external demo) |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Invalid token` | Use the **README.md** tester table; re-run seed (`./EXEC` or `docker compose up`) if the DB predates fixed demo tokens |
| `docker compose up` takes several minutes | Normal — waiting on Ollama `service_healthy`; see `docs/OLLAMA.md` |
| Ollama offline / `[LLM UNAVAILABLE]` | `docker exec arp_ollama ollama pull phi3:mini`; `make warm-model`; non-AI tabs unaffected |
| Ollama offline (local uvicorn) | `OLLAMA_HOST=http://localhost:11434`, `ollama pull phi3:mini`, restart API |
| Sidebar flashes “Cannot reach API health endpoint” during LLM work | Usually transient — the UI now uses `/health?lite=1` and keeps the last good status for up to 5 min. If it persists, confirm uvicorn is running on `API_URL` and increase `HEALTH_TIMEOUT_SECONDS` |
| Frontend cannot reach API | Docker: `API_URL=http://backend:8000`. Local UI: `API_URL=http://localhost:8000` |
| Analyst denied on trades | Expected RBAC — use `risk@local` |
| Manager sees “access denied” on summary | Pull latest code; manager uses `summary` permission on `/portfolio/summary` |
| `pip install` fails (disk space) | Use Option 1 with `requirements-test.txt` only, or Docker Option 2 |
| **429 Too Many Requests** on LLM tabs | See [Demo rate limits](#demo-rate-limits-llm-routes) above |

---

## Related docs

| File | Contents |
|------|----------|
| `PROJECT.md` | Repository layout |
| `README.md` | Architecture and RBAC matrix |
| `docs/PERSISTENCE.md` | SQLite assessment limits vs PostgreSQL production target |
| `docs/DEPENDENCY_SCANNING.md` | Safety policy file + Trivy image scanning |
| `docs/MARKET_DATA.md` | Mock vs live quotes; `external_calls` disclosure |
| `docs/OLLAMA.md` | Docker first-start wait; model pull; AI vs non-AI tabs |
| `docs/ROLES.md` | Manager/COO vs risk/admin; Intelligence workspace |
| `docs/RISK_MODELS.md` | Research RISK_REPORT, factors, parametric VaR — demo vs production |
| `CONTEXT.md` | ARP Global Capital engagement context |
| `NEXT_STEPS.md` | Submission and interview checklist |
