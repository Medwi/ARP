"""
ARP Platform - market_data.py
Pluggable market-price providers for seeding and refresh.

Three sources, selected via the MARKET_DATA_SOURCE environment variable:

    mock          modelled prices (deterministic, offline; default)
    yahoo         Yahoo Finance via yfinance
    alphavantage  Alpha Vantage GLOBAL_QUOTE / CURRENCY_EXCHANGE_RATE

Security:
    All credentials are read from the environment only. The Alpha Vantage key
    must be supplied via ALPHA_VANTAGE_API_KEY. No secret is committed to source
    or written to the database. If a real source fails or is rate-limited, the
    provider degrades gracefully to modelled prices so a demo never blocks.
"""

from __future__ import annotations

import os
import json
import time
import random
import urllib.parse
import urllib.request

# ── Configuration (environment-driven) ────────────────────────────────────────

def _norm_source(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("yahoo", "yfinance", "yf"):
        return "yahoo"
    if v in ("alphavantage", "alpha_vantage", "alpha-vantage", "av"):
        return "alphavantage"
    return "mock"


def configured_source() -> str:
    return _norm_source(os.getenv("MARKET_DATA_SOURCE", "mock"))


# Alpha Vantage settings. Set ALPHA_VANTAGE_API_KEY in .env (gitignored).
# If alphavantage is selected but the key is missing, seeding falls back to mock.
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
AV_BASE_URL           = os.getenv("AV_BASE_URL", "https://www.alphavantage.co/query")
AV_ENTITLEMENT        = os.getenv("ALPHA_VANTAGE_ENTITLEMENT", "delayed")
AV_REQUEST_SLEEP      = float(os.getenv("AV_REQUEST_SLEEP", "0.9"))  # be polite to the rate limit
AV_TIMEOUT            = float(os.getenv("AV_TIMEOUT", "8"))

# ── Modelled (mock) prices ─────────────────────────────────────────────────────

def _daily_move(asset_class: str, vol_map: dict) -> float:
    ann_vol = vol_map.get(asset_class, 0.15)
    daily_sigma = ann_vol / (252 ** 0.5) * 100.0
    return round(random.gauss(0.0, daily_sigma), 2)


def _jitter(base: float, spread: float = 0.015) -> float:
    return round(base * random.uniform(1 - spread, 1 + spread), 2)


def _mock_rows(tickers, base_prices, class_map, vol_map):
    rows = []
    for ticker in tickers:
        base = base_prices[ticker]
        price = _jitter(base)
        chg = _daily_move(class_map.get(ticker, "Equity"), vol_map)
        rows.append((ticker, price, chg, random.randint(500_000, 40_000_000), "mock"))
    return rows


# ── Yahoo Finance ──────────────────────────────────────────────────────────────

def _yahoo_rows(tickers, base_prices, class_map, vol_map):
    try:
        import yfinance as yf
    except ImportError:
        print("  - yfinance not installed; falling back to modelled prices")
        return _mock_rows(tickers, base_prices, class_map, vol_map)

    rows = []
    try:
        tickers_obj = yf.Tickers(" ".join(tickers))
        for ticker in tickers:
            try:
                info = tickers_obj.tickers[ticker].fast_info
                price = round(float(info.last_price or base_prices[ticker]), 2)
                prev = round(float(info.previous_close or price), 2)
                chg = round((price - prev) / prev * 100, 2) if prev else 0.0
                vol = int(info.three_month_average_volume or 1_000_000)
                rows.append((ticker, price, chg, vol, "yahoo"))
            except Exception:
                base = base_prices[ticker]
                rows.append((ticker, _jitter(base),
                             _daily_move(class_map.get(ticker, "Equity"), vol_map),
                             1_000_000, "yahoo:fallback"))
        print(f"  - Yahoo Finance prices fetched for {len(rows)} instruments")
        return rows
    except Exception as e:
        print(f"  - Yahoo Finance unavailable ({e}); falling back to modelled prices")
        return _mock_rows(tickers, base_prices, class_map, vol_map)


# ── Alpha Vantage ────────────────────────────────────────────────────────────

def _av_get(params: dict) -> dict:
    if AV_ENTITLEMENT:
        params = {**params, "entitlement": AV_ENTITLEMENT}
    params = {**params, "apikey": ALPHA_VANTAGE_API_KEY}
    url = f"{AV_BASE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ARP-Platform/1.0"})
    with urllib.request.urlopen(req, timeout=AV_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _av_equity_quote(ticker: str):
    """Return (price, change_pct, volume) for an equity/ETF, or None on failure."""
    data = _av_get({"function": "GLOBAL_QUOTE", "symbol": ticker})
    quote = data.get("Global Quote") or data.get("Global Quote - DATA DELAYED BY 15 MINUTES") or {}
    price = quote.get("05. price")
    if not price:
        # Surface rate-limit / error notes without leaking the key.
        note = data.get("Note") or data.get("Information") or data.get("Error Message")
        if note:
            print(f"  - Alpha Vantage: {ticker}: {note[:120]}")
        return None
    try:
        price = float(price)
        chg_raw = quote.get("10. change percent", "0%").replace("%", "").strip()
        chg = float(chg_raw) if chg_raw else 0.0
        vol = int(float(quote.get("06. volume", 0) or 0))
        return price, chg, vol
    except (TypeError, ValueError):
        return None


def _av_crypto_quote(ticker: str):
    """Return (price, change_pct, volume) for a crypto pair like BTC-USD."""
    base = ticker.split("-")[0]
    data = _av_get({
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": base,
        "to_currency": "USD",
    })
    rate = (data.get("Realtime Currency Exchange Rate", {}) or {}).get("5. Exchange Rate")
    if not rate:
        return None
    try:
        return float(rate), None, 0  # AV exchange-rate has no intraday change/volume
    except (TypeError, ValueError):
        return None


def _alphavantage_rows(tickers, base_prices, class_map, vol_map):
    if not ALPHA_VANTAGE_API_KEY:
        print("  - ALPHA_VANTAGE_API_KEY not set; falling back to modelled prices")
        return _mock_rows(tickers, base_prices, class_map, vol_map)

    rows = []
    ok = 0
    for ticker in tickers:
        asset_class = class_map.get(ticker, "Equity")
        result = None
        try:
            if asset_class == "Crypto":
                result = _av_crypto_quote(ticker)
            else:
                result = _av_equity_quote(ticker)
        except Exception as e:
            print(f"  - Alpha Vantage error for {ticker}: {str(e)[:80]}")

        if result:
            price, chg, vol = result
            # Crypto has no change/volume from AV; model the daily move only.
            if chg is None:
                chg = _daily_move(asset_class, vol_map)
            if not vol:
                vol = random.randint(500_000, 40_000_000)
            rows.append((ticker, round(price, 2), round(chg, 2), int(vol), "alphavantage"))
            ok += 1
        else:
            base = base_prices[ticker]
            rows.append((ticker, _jitter(base),
                         _daily_move(asset_class, vol_map),
                         random.randint(500_000, 40_000_000), "alphavantage:fallback"))

        if AV_REQUEST_SLEEP > 0:
            time.sleep(AV_REQUEST_SLEEP)

    print(f"  - Alpha Vantage prices fetched for {ok}/{len(tickers)} instruments "
          f"(entitlement={AV_ENTITLEMENT})")
    return rows


# ── Public entry point ─────────────────────────────────────────────────────────

def fetch_prices(tickers, base_prices, class_map, vol_map, source: str | None = None):
    """
    Return a list of rows (ticker, price, change_pct, volume, source) for the
    requested tickers using the selected provider. Always returns a full set;
    individual failures fall back to modelled prices.
    """
    src = _norm_source(source) if source else configured_source()
    if src == "yahoo":
        return _yahoo_rows(tickers, base_prices, class_map, vol_map)
    if src == "alphavantage":
        return _alphavantage_rows(tickers, base_prices, class_map, vol_map)
    return _mock_rows(tickers, base_prices, class_map, vol_map)
