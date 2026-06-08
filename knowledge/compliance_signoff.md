# ARP Global Capital — Compliance & Trade Sign-Off Framework

**Document ID:** POL-COMP-003  
**Effective:** 2026-01-01  
**Audience:** Risk, Admin

## Regulatory context

ARP Global Capital operates in the **DIFC** under **DFSA** oversight. All autonomous or
semi-autonomous systems (including AI agents) must maintain auditability, explainability,
and human accountability per **DFSA Regulation 10 on Autonomous Systems**.

## Sign-off matrix

| Action                         | Analyst | Risk | Compliance | CIO  |
|--------------------------------|---------|------|------------|------|
| View portfolio                 | Yes     | Yes  | Yes        | Yes  |
| View trades / flagged items    | No      | Yes  | Yes        | Yes  |
| Approve trade < USD 50k        | Propose | Review | —        | —    |
| Approve trade >= USD 50k       | Propose | Review | Required | —    |
| Mandate exception              | —       | Review | Required | Yes  |
| Export audit pack              | No      | Yes  | Yes        | Yes  |

## Pending compliance queue

Trades in **PENDING** status await IC note or dual sign-off. Agents must never approve
trades — only surface status, risk score, and applicable policy references.

## AI agent governance

- Every agent query is logged with user, role, tool, and allow/deny outcome.
- Structured database context is passed to the local LLM; raw credentials never are.
- Denied RBAC attempts are written to the immutable audit trail.
- Human review is mandatory before any execution or client communication.

## Data protection

Client and position data remain on-device. External calls are limited to public market
quotes when a live price source is selected. No portfolio data is sent to cloud LLMs.
