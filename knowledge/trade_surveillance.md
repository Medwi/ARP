# ARP Global Capital — Trade Surveillance & Flagging Procedures

**Document ID:** POL-SURV-004  
**Effective:** 2026-01-01  
**Audience:** Risk, Admin

## Objectives

Detect trades that breach mandate limits, exhibit elevated model risk scores, or require
enhanced review before settlement.

## Automatic flags

A trade is flagged when any of the following apply:

1. **Risk score** at or above 0.80 (CRITICAL) — execution blocked pending review.
2. **Notional** at or above USD 50,000 — dual sign-off (PM + Compliance).
3. **Post-trade simulation** shows crypto sleeve would exceed 5% of AUM.
4. **Concentration** — single-name weight would exceed 8% after execution.

## Review workflow

1. Risk desk receives alert in dashboard and audit log.
2. Analyst or PM supplies written rationale (IC note for strategic sleeve changes).
3. Compliance confirms sign-off matrix requirements are met.
4. Status moves from PENDING to APPROVED or REJECTED — never by AI agent.

## Explaining flagged trades to stakeholders

When asked "why was this trade flagged", cite:

- The triggering rule (e.g. large_notional_trade, high_risk_score).
- The trade's risk score and notional versus threshold.
- Current pending status and required approvers.

## Escalation timing

CRITICAL flags: same business day.  
HIGH/MEDIUM flags: within 24 hours.  
LOW monitors: weekly risk committee agenda item.
