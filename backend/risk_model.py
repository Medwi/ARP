"""
ARP Platform – risk_model.py
Shared parametric risk assumptions for seeding and ex-ante VaR estimates.

Used by seed/seed.py (mock price jitter), backend/tools/portfolio_tools.py
(get_var_metrics), and backend/book_data.py (TARGET_AUM for holdings).
"""

from __future__ import annotations

# Demo mandate size — matches seed.py book narrative (USD 1m SMA).
TARGET_AUM = 1_000_000

TRADING_DAYS = 252
RISK_FREE_RATE = 0.045  # 4.5% annualised

VAR_Z_95 = 1.645
VAR_Z_99 = 2.326

# Simplified variance-covariance: flat cross-asset correlation (see get_var_metrics note).
CROSS_CORRELATION = 0.35

# Annualised volatility assumptions by asset class.
ASSET_CLASS_VOL: dict[str, float] = {
    "Equity":       0.20,
    "ETF":          0.18,
    "Fixed Income": 0.08,
    "Commodity":    0.16,
    "Crypto":       0.75,
    "Cash":         0.00,
}

# Equity beta proxies by asset class (vs broad equity benchmark).
ASSET_CLASS_BETA: dict[str, float] = {
    "Equity":       1.00,
    "ETF":          0.95,
    "Fixed Income": 0.15,
    "Commodity":    0.25,
    "Crypto":       1.80,
    "Cash":         0.00,
}


def asset_class_vol(asset_class: str) -> float:
    """Annualised vol for an asset class; default 15% if unknown."""
    return ASSET_CLASS_VOL.get(asset_class, 0.15)
