# Ollama & local LLM — first-start expectations

The platform **works without Ollama** for portfolio, trades, risk, market data, audit, and most expansion REST routes. **AI Agents**, briefing, and other LLM tabs need a running Ollama with the configured model **pulled**.

## Docker Compose — first start is slow (normal)

`docker-compose.yml` gates **seed** and **backend** on `ollama: service_healthy`:

- Ollama’s healthcheck has a **60s `start_period`** before failures count.
- First boot on a cold machine can take **1–3 minutes** before the API container starts.
- This is **not** a hung build — Compose is waiting for the Ollama API to respond.

While you wait, there is **no dashboard yet** — that is expected.

## Model not pulled → AI looks broken

Ollama can be **healthy** while **`phi3:mini` is not downloaded**. Symptom:

- Sidebar may show “Local model offline” or missing models.
- **AI Agents** returns `[LLM UNAVAILABLE] Could not reach Ollama…` or fails after a long wait.
- **Portfolio / Risk / Market tabs still work** — only NL synthesis is affected.

**After first `docker compose up`:**

```bash
docker exec arp_ollama ollama pull phi3:mini
docker exec arp_ollama ollama pull nomic-embed-text   # optional — RAG embeddings
make warm-model   # cold-start inference before a live demo
make healthcheck  # confirms Ollama + model
```

Local (non-Docker) equivalent:

```bash
ollama pull phi3:mini
make warm-model
```

## Pre-demo checklist

```bash
make healthcheck      # includes Ollama reachability
make warm-model       # loads model into memory (avoids 30–120s first question)
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| `docker compose up` stuck a long time | Waiting on Ollama healthy | Wait; check `docker logs arp_ollama` |
| Dashboard up, AI Agents broken | Model not pulled | `docker exec arp_ollama ollama pull phi3:mini` |
| `[LLM UNAVAILABLE]` in agent reply | Ollama down or wrong `OLLAMA_HOST` | Docker: `OLLAMA_HOST=http://ollama:11434`; local: `http://localhost:11434` |
| First question very slow | Cold model load | `make warm-model` before demo |
| Tools work, only AI fails | Expected without LLM | Pull model or demo non-AI tabs first |

## One-liner for reviewers

> Compose intentionally waits for Ollama; tools and RBAC work without it. Pull `phi3:mini` once, run `make warm-model`, and the AI Agents tab uses on-device inference only.
