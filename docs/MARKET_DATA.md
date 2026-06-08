# Market data & egress disclosure

## Demo default: stay on `mock`

For assessor walkthroughs and live demos, keep:

```bash
MARKET_DATA_SOURCE=mock
```

This is the default in `.env.example`. Prices are modelled offline — **no outbound HTTP** for quotes — and platform attestation reports `external_calls: false`.

Only switch to `yahoo` or `alphavantage` if you **intentionally** want to discuss live quote egress with the reviewer.

## Live sources (`yahoo`, `alphavantage`)

| Source | Egress | What leaves the machine |
|--------|--------|-------------------------|
| `yahoo` | Yes | Public ticker symbols → Yahoo Finance (`yfinance`) for delayed quotes |
| `alphavantage` | Yes | Public symbols → Alpha Vantage API (`ALPHA_VANTAGE_API_KEY` required) |

**What never leaves the machine:** client identities, positions, trades, portfolio holdings, or LLM prompts/responses (inference stays on-device via Ollama).

Re-seed after changing source (`./EXEC` or `python seed/seed.py`) so `market_prices` reflects the new provider.

## Where `external_calls` appears

When `MARKET_DATA_SOURCE` is not `mock`, runtime attestation sets **`external_calls: true`**:

| Surface | Field |
|---------|--------|
| `GET /health` (authenticated detail) | `external_calls` |
| `GET /metrics` | via platform attestation |
| Compliance pack export (`/report/compliance`) | External API calls row |
| Streamlit **Compliance** tab | Local inference check + market-data caption |

The Compliance tab explains that live sources fetch **public price quotes only**. That is accurate — but reviewers will still see `external_calls: true` and may ask about data residency. Use **`mock`** unless you are prepared for that conversation.

## Switching sources

```bash
# Offline demo (recommended)
MARKET_DATA_SOURCE=mock ./EXEC

# Live quotes — optional; triggers external_calls: true
MARKET_DATA_SOURCE=yahoo ./EXEC
MARKET_DATA_SOURCE=alphavantage ALPHA_VANTAGE_API_KEY=your_key ./EXEC
```

API keys belong in `.env` (gitignored), never in source control.

## One-liner for reviewers

> Default demo uses offline mock prices (`external_calls: false`). Yahoo and Alpha Vantage are optional integrations for public quotes only — no position or client data is transmitted; the Compliance tab and `/health` disclose when egress is active.
