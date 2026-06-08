# ARP Global Capital — Risk Limits & Monitoring Policy

**Document ID:** POL-RISK-002  
**Effective:** 2026-01-01  
**Audience:** Analyst, Risk, Admin

## Purpose

Define automated and desk-level risk limits for the USD 1,000,000 managed account.
Breaches surface as WARNING or BREACH badges in the dashboard and in agent responses.

## Hard limits (system-enforced)

| Rule ID                  | Description                              | Threshold        |
|--------------------------|------------------------------------------|------------------|
| max_single_position      | Single position exceeds 8% of AUM        | 8.0% weight      |
| max_crypto_exposure      | Digital-asset allocation exceeds mandate   | 5.0% of AUM      |
| max_top3_concentration   | Top-3 holdings exceed concentration cap  | 30.0% of AUM     |
| large_notional_trade     | Single trade notional                    | USD 50,000       |
| high_risk_score          | Model risk score on pending trade        | 0.80             |
| max_daily_turnover       | Daily turnover (monitored, soft)         | 10.0% of AUM     |

## Escalation

- **CRITICAL** (high risk score): Block pending execution until Risk + Compliance review.
- **HIGH** (concentration, crypto breach): Same-day desk review; no new risk until cleared.
- **MEDIUM** (large notional): Dual sign-off required before release.
- **LOW** (turnover monitor): Logged for weekly risk committee.

## Analyst guidance

Analysts may view portfolio and market data but cannot access trade blotter or audit logs.
When answering allocation questions, cite live holdings data — not policy thresholds alone.

## Model risk disclaimer

VaR, Sharpe, and beta metrics in the dashboard are **parametric proxies** derived from
snapshot holdings. They support monitoring; they are not regulatory capital figures.
