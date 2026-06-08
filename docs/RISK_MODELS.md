# Research, factors & risk metrics — demo vs production

Several Intelligence & operations panels **look like live risk infrastructure** (VaR figures, stress P&L, risk reports). They are **illustrative demos** on the seeded book unless captions are read.

## Quick rule for assessors

| Surface | Looks like | Actually is |
|---------|------------|-------------|
| **Research → `RISK_REPORT` samples** | Monthly risk review with VaR, Sharpe, drawdown | **Seeded narrative text** in the research lake — not output from a risk engine |
| **Factors → stress scenarios** | Scenario P&L bars | **Heuristic shocks** on snapshot weights — not historical simulation |
| **Factors → factor scores** | Fama-French-style tilts | **Hand-coded proxy map** per ticker — not CRSP/Compustat factors |
| **Operations → VaR 95% (vol sizing)** | Production parametric VaR | **Gaussian 1-day formula** on user-entered vol — advisory sizing only |
| **Operations → risk budget** | Live vol utilisation | **Vol proxy from factor scores** — not 252-day realised vol |
| **`GET /portfolio/var`** | Fund risk dashboard API | **Ex-ante parametric** model (asset-class vols + flat correlation) on one price snapshot |
| **Reporting → manager accounts** | DFSA regulatory metrics | **`synthetic: true`** — illustrative tests and filing calendar |

All of the above sit on `data_scope: demo_snapshot` unless `ARP_DATA_SCOPE=live` is set with real ingest (not wired in this assessment).

---

## Research lake — `RISK_REPORT` entries

Clicking **Load sample research** seeds entries including:

> *"Monthly Risk Review — May 2026 … Portfolio VaR (95%, 1-day): $420,000 … Stress test: -20% equity shock …"*

That text is **static demo content** authored for the assessment (`backend/tools/research_lake.py`). It is **not**:

- Fed by Broadridge, MSCI, or an internal risk warehouse  
- Linked to `/portfolio/var` or live P&L history  
- A substitute for the mandate `risk_rules` engine on the Risk tab  

Use it to demo **research search and Q&A** — cite it as seeded narrative when asked about VaR.

---

## Factor analysis & stress tests

**Factors** tab (`backend/tools/factor_analysis.py`):

- Sector weights and HHI from the **current holdings snapshot**  
- Factor exposures from a **simplified `FACTOR_MAP`** per ticker  
- Stress tests apply **fixed scenario shocks** (equity -20%, crypto -30%, etc.) to snapshot weights  

Production would use a **252-day returns window** from Broadridge (stated in module docstring and UI caption).

---

## Parametric VaR (API & Operations)

**`GET /portfolio/var`** (`get_var_metrics`):

- Variance-covariance with **asset-class volatilities** and a **flat 0.35 cross-correlation**  
- Single price snapshot — response includes a `note` field explaining limitations  

**Operations → Position sizing → Volatility**:

- Computes **1-day VaR (95%)** from user-supplied annual vol (`1.645 × daily σ × weight`)  
- Displayed as a metric card — **advisory pre-trade maths**, not fund regulatory VaR  

Read the **`note`** / **`interpretation`** / **`hitl_note`** fields on API responses; the UI captions are easy to skip when clicking quickly.

---

## What *is* live-ish on the Risk tab

**Book & markets → Risk** evaluates seeded **mandate rules** (`risk_rules`) against the demo book (concentration, crypto sleeve, notional limits). That is rule-based surveillance on snapshot data — still `demo_snapshot`, but it is the primary “risk engine” in the core UI.

---

## One-liner for reviewers

> VaR, stress tests, and `RISK_REPORT` research entries are illustrative demo artefacts on a seeded book — read the captions and `data_scope`; production would use Broadridge returns, a real factor model, and a certified risk stack.
