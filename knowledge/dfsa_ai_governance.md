# ARP Global Capital — DFSA AI Governance Summary

**Document ID:** POL-GOV-005  
**Effective:** 2026-01-01  
**Audience:** Risk, Manager, Admin

## Regulation 10 alignment (autonomous systems)

ARP's AI agents are **advisory only**. They:

- Retrieve live portfolio and risk data through RBAC-gated tools.
- Supplement answers with retrieved internal policy documents (local RAG).
- Run inference on-device via Ollama — no client data sent to external LLMs.
- Log every interaction to an append-only audit trail.

## Human-in-the-loop (HITL)

No agent may execute trades, modify holdings, or send investor communications.
All outputs require human judgment before action.

## Transparency requirements

The platform exposes:

- Which tool was called for each question.
- Structured data context passed to the model.
- Retrieved policy excerpts (RAG sources) where applicable.
- Role-based UI adaptation so users only see permitted data.

## Manager-facing summary

Executives receive headline KPIs: AUM, P&L direction, alert counts. Detailed positions
and trade surveillance are restricted to Risk and Compliance roles.

## Record of processing

Market data providers (mock, Yahoo, Alpha Vantage) fetch **public quotes only**.
Position and client data never leave the local environment during inference.
