# Roles & demo personas

Five fixed roles with tool-level RBAC. Permissions are enforced in `backend/rbac.py` before any database access.

## Manager / COO view (`manager@local`)

**This is the executive (COO) persona** — not a broken login.

| Manager sees | Manager does **not** see |
|--------------|---------------------------|
| **Executive Summary** screen only | Book & markets nav (Portfolio, Trades, Risk, Market, AI Agents, Audit) |
| Total AUM, P&L, position count | Full holdings book, trade blotter, flagged trade detail |
| Asset-class pie chart | Market movers, AI agents |
| Top-3 concentration (ticker + weight %) | Position-level P&L or market values |
| Risk alert **counts** by severity | Risk rule text, remediation, audit log |

Managers land directly on **Executive Summary** — there is no workspace switcher. That is **segregation of duties**, not missing functionality.

### “Where’s the COO briefing / reporting / compliance?”

The **Intelligence & operations** workspace (Briefing, Email, Research, Reporting, Operations, **Compliance**) is restricted to operational roles:

| Workspace | Typical login |
|-----------|----------------|
| Executive Summary (COO KPIs) | `manager@local` |
| Book & markets + Intelligence & operations | `risk@local` or `admin@local` |

For a walkthrough that includes CIO briefing, manager reporting packs, or the Compliance checklist, **sign in as risk or admin** after showing the COO screen with the manager token.

### API (manager token)

```bash
export MANAGER_TOKEN="e80cde4bb031633a8bb086c33900869ae66b763e4df4d239"

curl -s -H "Authorization: Bearer $MANAGER_TOKEN" \
  http://localhost:8000/portfolio/summary | python3 -m json.tool

curl -s -H "Authorization: Bearer $MANAGER_TOKEN" \
  http://localhost:8000/risk/alerts | python3 -m json.tool
```

`/risk/alerts` returns **executive summary mode** — counts and severity buckets only.

---

## Other roles (quick reference)

| Role | UI | Purpose in demo |
|------|-----|-----------------|
| **admin@local** | Full platform | Superset of risk; audit + metrics |
| **risk@local** | Book & markets + Intelligence & operations | Primary operator / compliance walkthrough |
| **analyst@local** | Portfolio, Market, AI Agents | Research-facing; no trades/audit |
| **intern@local** | Onboarding screen | RBAC deny demo (403 on data APIs) |

Bearer tokens: README **Tester access** table.

## One-liner for reviewers

> The manager token is the COO view — executive KPIs and risk counts only by design. Briefing, reporting, and Compliance live under Intelligence & operations and require a risk or admin login.
