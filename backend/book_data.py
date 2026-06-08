"""
ARP Platform – book_data.py
Canonical position master and helpers for seeding holdings / market snapshots.

Used by seed/seed.py (production book) and tests/book_fixture.py (deterministic
copy of the same mandate structure).
"""

from __future__ import annotations

from backend.risk_model import TARGET_AUM

CASH_TICKER = "USD.CASH"
CASH_WEIGHT = 5.0

# (ticker, name, asset_class, target_weight_pct, base_price, unrealized_return)
POSITIONS: list[tuple] = [
    ("MSFT",   "Microsoft Corp",            "Equity",        4.5,   415.0,   0.24),
    ("AAPL",   "Apple Inc",                 "Equity",        4.0,   185.0,   0.09),
    ("NVDA",   "NVIDIA Corp",               "Equity",        3.5,   875.0,   0.42),
    ("GOOGL",  "Alphabet Inc",              "Equity",        3.0,   175.0,   0.15),
    ("JPM",    "JPMorgan Chase & Co",       "Equity",        3.0,   205.0,   0.16),
    ("UNH",    "UnitedHealth Group",        "Equity",        3.0,   505.0,  -0.06),
    ("JNJ",    "Johnson & Johnson",         "Equity",        2.5,   155.0,  -0.02),
    ("V",      "Visa Inc",                  "Equity",        2.5,   285.0,   0.13),
    ("PG",     "Procter & Gamble Co",       "Equity",        2.5,   168.0,   0.05),
    ("XOM",    "Exxon Mobil Corp",          "Equity",        2.5,   112.0,   0.10),
    ("HD",     "Home Depot Inc",            "Equity",        2.0,   360.0,   0.03),
    ("BRK-B",  "Berkshire Hathaway B",      "Equity",        2.5,   415.0,   0.15),
    ("SPY",    "SPDR S&P 500 ETF",          "ETF",          10.0,   520.0,   0.13),
    ("VEA",    "Vanguard Dev. Mkts ETF",    "ETF",           6.5,    49.0,   0.07),
    ("VWO",    "Vanguard Emerging Mkts ETF","ETF",           3.5,    44.0,   0.05),
    ("AGG",    "iShares Core US Agg Bond",  "Fixed Income", 10.0,    98.0,  -0.02),
    ("LQD",    "iShares IG Corp Bond",      "Fixed Income",  7.0,   109.0,  -0.01),
    ("TLT",    "iShares 20+Y Treasury",     "Fixed Income",  6.0,    92.0,  -0.06),
    ("TIP",    "iShares TIPS Bond ETF",     "Fixed Income",  4.0,   108.0,  -0.015),
    ("HYG",    "iShares High Yield Corp",   "Fixed Income",  3.0,    79.0,   0.025),
    ("GLD",    "SPDR Gold Shares",          "Commodity",     7.0,   215.0,   0.17),
    ("BTC-USD","Bitcoin",                   "Crypto",        2.5, 67000.0,   0.40),
]

MARKET_TICKERS = [p[0] for p in POSITIONS]

# Deterministic daily moves for test market_prices (no random jitter).
TEST_MARKET_MOVES: dict[str, float] = {
    "NVDA": 4.2, "BTC-USD": 3.1, "AAPL": 1.2, "MSFT": 0.9, "SPY": 0.8,
    "GOOGL": -1.5, "AGG": -0.3, "TLT": -0.6, "GLD": 0.7, "VEA": 0.4,
}


def holdings_rows(
    prices: dict[str, float],
    target_aum: float = TARGET_AUM,
) -> list[tuple]:
    """Build portfolio_holdings INSERT rows from POSITIONS and a price lookup."""
    rows: list[tuple] = []
    for ticker, name, asset_class, weight, base_price, unreal_ret in POSITIONS:
        price = prices.get(ticker, base_price)
        mv = round(target_aum * weight / 100, 2)
        qty = round(mv / price, 4)
        avg_cost = round(price / (1 + unreal_ret), 4)
        rows.append((ticker, name, asset_class, qty, avg_cost, price, mv, weight))

    cash_mv = round(target_aum * CASH_WEIGHT / 100, 2)
    rows.append((
        CASH_TICKER, "USD Cash & Equivalents", "Cash",
        cash_mv, 1.0, 1.0, cash_mv, CASH_WEIGHT,
    ))
    return rows


def base_prices() -> dict[str, float]:
    return {p[0]: p[4] for p in POSITIONS}


def deterministic_market_rows(
    prices: dict[str, float] | None = None,
    source: str = "mock",
) -> list[tuple]:
    """Fixed market snapshot rows for tests."""
    prices = prices or base_prices()
    rows: list[tuple] = []
    for ticker, _, asset_class, _, base_price, _ in POSITIONS:
        price = prices.get(ticker, base_price)
        change = TEST_MARKET_MOVES.get(ticker, 0.5 if asset_class != "Fixed Income" else -0.2)
        rows.append((ticker, price, change, 1_000_000, source))
    return rows
