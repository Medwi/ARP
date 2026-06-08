"""
ARP Platform – risk_constants.py
Canonical risk rule definitions aligned with knowledge/POL-RISK-002 and seed data.

Single source of truth for mandate limits. Used by:
  - seed/seed.py (DB seeding)
  - tests/conftest.py (test fixture)
  - backend/tools/risk_tools.py (runtime fallbacks when rules table is empty)
  - backend/graph.py policy ontology thresholds (via live risk_rules table)
"""

from __future__ import annotations

from typing import NamedTuple, Optional

# Asset classes subject to the single-name concentration limit (not pooled funds).
SINGLE_ISSUER_ASSET_CLASSES: tuple[str, ...] = ("Equity", "Crypto")

# Badge / pre-trade warning band: flag at 80% of the hard limit.
WARNING_BAND_RATIO: float = 0.8

# Below CRITICAL (0.80) but worth surfacing in flagged-trade review.
ELEVATED_RISK_SCORE_THRESHOLD: float = 0.70


class RiskRuleDef(NamedTuple):
    rule_name: str
    description: str
    threshold: float
    metric: str
    severity: str


# Mandate-aligned limits — must match knowledge/risk_limits_policy.md.
RISK_RULES: tuple[RiskRuleDef, ...] = (
    RiskRuleDef(
        "max_single_position",
        "Single position exceeds 8% of AUM",
        8.0,
        "weight_pct",
        "HIGH",
    ),
    RiskRuleDef(
        "max_crypto_exposure",
        "Digital-asset allocation exceeds 5% of AUM",
        5.0,
        "asset_class_pct",
        "HIGH",
    ),
    RiskRuleDef(
        "large_notional_trade",
        "Single trade notional exceeds USD 50,000",
        50_000.0,
        "notional",
        "MEDIUM",
    ),
    RiskRuleDef(
        "high_risk_score",
        "Trade model risk score exceeds 0.80",
        0.80,
        "risk_score",
        "CRITICAL",
    ),
    RiskRuleDef(
        "max_top3_concentration",
        "Top-3 holdings exceed 30% of AUM",
        30.0,
        "top3_pct",
        "MEDIUM",
    ),
    RiskRuleDef(
        "max_daily_turnover",
        "Daily turnover exceeds 10% of AUM (monitored)",
        10.0,
        "daily_turnover",
        "LOW",
    ),
)

_RULE_BY_NAME: dict[str, RiskRuleDef] = {r.rule_name: r for r in RISK_RULES}
_RULE_BY_METRIC: dict[str, RiskRuleDef] = {r.metric: r for r in RISK_RULES}


def rules_as_seed_rows() -> list[tuple]:
    """Rows for INSERT INTO risk_rules (rule_name, description, threshold, metric, severity)."""
    return [
        (r.rule_name, r.description, r.threshold, r.metric, r.severity)
        for r in RISK_RULES
    ]


def default_threshold(metric: str, rule_name: Optional[str] = None) -> float:
    """Fallback threshold when the DB rules table has no matching row."""
    if rule_name and rule_name in _RULE_BY_NAME:
        return _RULE_BY_NAME[rule_name].threshold
    if metric in _RULE_BY_METRIC:
        return _RULE_BY_METRIC[metric].threshold
    raise KeyError(f"No default threshold for metric={metric!r} rule_name={rule_name!r}")


def default_rule(rule_name: str) -> RiskRuleDef:
    return _RULE_BY_NAME[rule_name]
