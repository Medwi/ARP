"""
ARP Platform – tools/manager_reporting.py
Manager Financial Reporting — DFSA Monthly Accounts.

Maps to ARP's SOON (0–3 month) roadmap priority:
  "Manager Financial Reporting: compiling monthly accounts, projections,
   and compliance tests mandated by the DFSA."

DFSA requirements addressed:
  - GEN Rule 6.2: Annual financial statements within 4 months of year-end
  - GEN Rule 6.3: Quarterly management accounts within 30 days
  - PIB Rule 3.7: Expenditure-based capital requirement (EBCR) calculation
  - PIB Rule 3.8: Liquid assets requirement (≥ 3 months' expenditure)
  - CIR Rule 13: Investor reporting timelines
  - COBS Rule 17: Performance fee and redemption disclosure

This module:
  - Computes the DFSA Expenditure-Based Capital Requirement (EBCR)
  - Checks liquid assets vs regulatory minimum
  - Flags upcoming regulatory filing deadlines
  - Generates a monthly accounts summary for CFO/COO review
  - All outputs require CFO (Innes Harding) sign-off before submission
"""

from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.data_scope import attach_data_scope
from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect

# ARP firm-level financial assumptions (mock — production pulls from accounting system)
ANNUAL_OPERATING_EXPENDITURE = float(os.getenv("ANNUAL_OPEX_USD", "2400000"))   # $2.4M/yr
MANAGEMENT_FEE_RATE          = float(os.getenv("MGMT_FEE_RATE", "0.015"))      # 1.5% AUM
PERFORMANCE_FEE_RATE         = float(os.getenv("PERF_FEE_RATE", "0.20"))       # 20% above HWM
LIQUID_ASSETS_USD            = float(os.getenv("LIQUID_ASSETS_USD", "850000")) # current liquid

DFSA_EBCR_MULTIPLIER         = 0.25   # PIB Rule 3.7: 25% of annual expenditure
DFSA_LIQUID_MONTHS           = 3      # PIB Rule 3.8: 3 months' expenditure minimum


def get_manager_accounts(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Monthly management accounts summary with DFSA compliance tests.
    Required role: risk (senior management reporting access).
    """
    perm = check_permission(user, "risk_alerts")
    log(user.email, user.role, "get_manager_accounts", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    con = connect(db_path)
    try:
        # ── AUM and fee revenue ───────────────────────────────────────────────
        total_mv = con.execute(
            "SELECT SUM(market_value) FROM portfolio_holdings"
        ).fetchone()[0] or 0

        mgmt_fee_annual    = round(total_mv * MANAGEMENT_FEE_RATE, 0)
        mgmt_fee_monthly   = round(mgmt_fee_annual / 12, 0)

        # Performance fee estimate (simple — full production uses HWM tracking)
        total_pnl = con.execute(
            "SELECT SUM((current_price - avg_cost) * quantity) FROM portfolio_holdings"
        ).fetchone()[0] or 0
        perf_fee_estimate = round(max(total_pnl * PERFORMANCE_FEE_RATE, 0), 0)

        total_revenue_monthly = mgmt_fee_monthly + round(perf_fee_estimate / 12, 0)
        monthly_opex          = round(ANNUAL_OPERATING_EXPENDITURE / 12, 0)
        operating_profit      = total_revenue_monthly - monthly_opex

        # ── DFSA PIB Capital Requirements ─────────────────────────────────────
        # PIB Rule 3.7: EBCR = 25% of annual operating expenditure
        ebcr_required = round(ANNUAL_OPERATING_EXPENDITURE * DFSA_EBCR_MULTIPLIER, 0)

        # PIB Rule 3.8: Liquid assets ≥ 3 months' operating expenditure
        liquid_assets_required = round(monthly_opex * DFSA_LIQUID_MONTHS, 0)
        liquid_assets_surplus  = round(LIQUID_ASSETS_USD - liquid_assets_required, 0)
        liquid_test_passed     = LIQUID_ASSETS_USD >= liquid_assets_required

        # EBCR test: requires actual capital figure (mock here)
        firm_capital_mock = round(LIQUID_ASSETS_USD * 1.8, 0)  # placeholder
        ebcr_test_passed  = firm_capital_mock >= ebcr_required
        ebcr_surplus      = round(firm_capital_mock - ebcr_required, 0)

        # ── Regulatory filing deadlines ────────────────────────────────────────
        now  = datetime.now(timezone.utc)
        year = now.year
        filings = [
            {
                "filing":    "DFSA Annual Financial Statements",
                "rule":      "GEN Rule 6.2",
                "deadline":  f"{year}-04-30",
                "frequency": "Annual",
                "status":    "PENDING" if now.month <= 4 else "SUBMITTED",
            },
            {
                "filing":    "DFSA Q1 Management Accounts",
                "rule":      "GEN Rule 6.3",
                "deadline":  f"{year}-04-30",
                "frequency": "Quarterly",
                "status":    "PENDING" if now.month <= 4 else "SUBMITTED",
            },
            {
                "filing":    "DFSA Q2 Management Accounts",
                "rule":      "GEN Rule 6.3",
                "deadline":  f"{year}-07-31",
                "frequency": "Quarterly",
                "status":    "PENDING" if now.month <= 7 else "SUBMITTED",
            },
            {
                "filing":    "DFSA Q3 Management Accounts",
                "rule":      "GEN Rule 6.3",
                "deadline":  f"{year}-10-31",
                "frequency": "Quarterly",
                "status":    "PENDING" if now.month <= 10 else "SUBMITTED",
            },
            {
                "filing":    "PIB Liquid Assets Return",
                "rule":      "PIB Rule 3.8",
                "deadline":  (now + timedelta(days=30 - now.day + 1)).strftime("%Y-%m-%d"),
                "frequency": "Monthly",
                "status":    "PENDING",
            },
        ]

        # Flag filings due within 30 days
        for f in filings:
            due = datetime.strptime(f["deadline"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_remaining = (due - now).days
            f["days_remaining"] = days_remaining
            f["urgent"] = 0 <= days_remaining <= 14
            f["synthetic"] = True

        upcoming = sorted(
            [f for f in filings if f["status"] == "PENDING"],
            key=lambda x: x["deadline"]
        )

        # ── Compliance tests summary ──────────────────────────────────────────
        all_tests_passed = liquid_test_passed and ebcr_test_passed

        return attach_data_scope({
            "allowed":              True,
            "synthetic":            True,
            "reporting_date":       now.strftime("%Y-%m-%d"),
            "aum":                  round(total_mv, 0),

            # P&L
            "mgmt_fee_monthly":     mgmt_fee_monthly,
            "perf_fee_estimate_ytd": perf_fee_estimate,
            "total_revenue_monthly": total_revenue_monthly,
            "monthly_opex":         monthly_opex,
            "operating_profit":     operating_profit,

            # DFSA tests
            "dfsa_tests": {
                "ebcr": {
                    "rule":           "PIB Rule 3.7",
                    "required":       ebcr_required,
                    "actual":         firm_capital_mock,
                    "surplus":        ebcr_surplus,
                    "passed":         ebcr_test_passed,
                    "synthetic":      True,
                    "description":    "Expenditure-Based Capital Requirement (25% annual opex)",
                },
                "liquid_assets": {
                    "rule":           "PIB Rule 3.8",
                    "required":       liquid_assets_required,
                    "actual":         LIQUID_ASSETS_USD,
                    "surplus":        liquid_assets_surplus,
                    "passed":         liquid_test_passed,
                    "synthetic":      True,
                    "description":    "Liquid assets ≥ 3 months' operating expenditure",
                },
            },
            "all_tests_passed":     all_tests_passed,

            # Filings
            "upcoming_filings":     upcoming,
            "urgent_filings":       [f for f in upcoming if f.get("urgent")],

            "sign_off_note": (
                "Monthly accounts require CFO (Innes Harding) sign-off "
                "before submission to the DFSA. Board-level sign-off required "
                "for formal product changes or prospectus disclosures."
            ),
            "data_note": (
                "Synthetic demo metrics — fee revenue, capital, and filing calendars "
                "are illustrative. Production pulls from the accounting system and "
                "Broadridge trade data for accurate P&L."
            ),
        })
    finally:
        con.close()
