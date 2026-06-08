"""
ARP Platform – frontend/app.py
Streamlit dashboard: portfolio, trades, risk, market data, AI agents, audit log.
Each role sees a different experience — RBAC enforced at the API level,
reflected visually here.
"""

import os, sys, time
from pathlib import Path

import requests
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Ensure sibling modules (expansion_views) resolve when launched as frontend/app.py
_FRONTEND_DIR = Path(__file__).resolve().parent
if str(_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(_FRONTEND_DIR))


def _load_dotenv() -> None:
    """Load repo-root .env into os.environ (setdefault — does not override exports)."""
    root = _FRONTEND_DIR.parent
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


_load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")

_NAV_CORE = [
    "Portfolio", "Trades", "Risk", "Market", "AI Agents", "Audit Log",
]
_NAV_CORE_ANALYST = ["Portfolio", "Market", "AI Agents"]
_NAV_EXPANSION = [
    "Briefing", "Email", "Research", "Factors", "Ideas",
    "Reporting", "Operations", "Compliance",
]


def _nav_core_for_role(role: str) -> list[str]:
    """Analysts cannot reach Trades, Risk, or Audit Log (API denies those resources)."""
    if role in ("risk", "admin"):
        return list(_NAV_CORE)
    return list(_NAV_CORE_ANALYST)


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# When false, Intelligence & operations workspace is hidden (fundamentals-only deploys).
_INTEL_WORKSPACE_AVAILABLE = _env_flag("ENABLE_ENHANCEMENTS", "1")

# ── Session timeout configuration ────────────────────────────────────────────
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
HEALTH_CACHE_SECONDS = int(os.getenv("HEALTH_CACHE_SECONDS", "45"))
HEALTH_STALE_SECONDS = int(os.getenv("HEALTH_STALE_SECONDS", "300"))
HEALTH_TIMEOUT_SECONDS = int(os.getenv("HEALTH_TIMEOUT_SECONDS", "12"))
API_CACHE_SECONDS = int(os.getenv("API_CACHE_SECONDS", "30"))


def _fetch_health(api_url: str, headers: dict | None = None) -> dict | None:
    """
    Lightweight /health?lite=1 with stale-while-revalidate.
    Avoids blanking the sidebar when the API is briefly slow (e.g. during Ollama inference).
    """
    store = st.session_state.setdefault("_health_cache", {})
    now = time.time()
    last_at = store.get("at", 0.0)
    if store.get("data") and now - last_at < HEALTH_CACHE_SECONDS:
        return store["data"]

    try:
        r = requests.get(
            f"{api_url}/health",
            params={"lite": "true"},
            headers=headers or {},
            timeout=HEALTH_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        data = r.json()
        store["data"] = data
        store["at"] = now
        store["stale"] = False
        return data
    except Exception:
        stale = store.get("data")
        if stale and now - last_at < HEALTH_STALE_SECONDS:
            store["stale"] = True
            return stale
        store.pop("data", None)
        store["stale"] = False
        return None


st.set_page_config(
    page_title="ARP Global Capital | Investment Intelligence",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS — institutional, restrained ───────────────────────────────────
ACCENT = "#c8a86b"   # muted gold
POS    = "#1f9d6b"   # gain green
NEG    = "#c0392b"   # loss red
WARN   = "#c8902b"   # amber

# Restrained institutional chart palette (slate / steel / gold tones).
PALETTE = ["#3b6ea5", "#4f8a8b", "#c8a86b", "#7a8b99", "#8a6d9e",
           "#b5895b", "#5c7a99", "#9aa3b2", "#6b8e7f", "#a3866b"]
CLASS_COLORS = {
    "Equity":       "#3b6ea5",
    "ETF":          "#4f8a8b",
    "Fixed Income": "#7a8b99",
    "Commodity":    "#c8a86b",
    "Crypto":       "#8a6d9e",
    "Cash":         "#9aa3b2",
}

st.markdown(f"""
<style>
:root {{
    --accent: {ACCENT};
    --pos: {POS};
    --neg: {NEG};
    --warn: {WARN};
}}

html, body, [class*="css"] {{
    font-feature-settings: "tnum" 1, "lnum" 1;
}}

/* Branded header band */
.arp-header {{
    border-bottom: 2px solid var(--accent);
    padding: 4px 0 14px 0;
    margin-bottom: 8px;
}}
.arp-brand {{
    font-size: 1.55rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    color: #f3f4f6;
}}
.arp-sub {{
    font-size: 0.78rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: #8a93a3;
    margin-top: 2px;
}}

/* Metric / KPI cards */
.metric-card {{
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-left: 3px solid var(--accent);
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 8px;
}}
.metric-card .mc-value {{ font-size: 1.15rem; font-weight: 700; }}
.metric-card .mc-label {{ font-size: 0.72rem; text-transform: uppercase;
                          letter-spacing: 0.06em; opacity: 0.65; }}
.metric-card .mc-sub   {{ font-size: 0.66rem; opacity: 0.5; }}

.badge-breach  {{ color: var(--neg);  font-weight: 700; }}
.badge-warning {{ color: var(--warn); font-weight: 700; }}
.badge-ok      {{ color: var(--pos);  font-weight: 700; }}

/* Status / severity pills (used in markdown contexts) */
.pill {{
    display: inline-block; padding: 1px 9px; border-radius: 3px;
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.04em;
    border: 1px solid transparent;
}}
.pill-pos  {{ color: var(--pos);  background: rgba(31,157,107,0.12);  border-color: rgba(31,157,107,0.35); }}
.pill-warn {{ color: var(--warn); background: rgba(200,144,43,0.12);  border-color: rgba(200,144,43,0.35); }}
.pill-neg  {{ color: var(--neg);  background: rgba(192,57,43,0.12);   border-color: rgba(192,57,43,0.35); }}
.pill-mute {{ color: #9aa3b2;     background: rgba(154,163,178,0.10); border-color: rgba(154,163,178,0.30); }}

.role-pill {{
    display: inline-block; padding: 2px 10px; border-radius: 3px;
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em;
    background: rgba(200,168,107,0.14); color: var(--accent);
    border: 1px solid rgba(200,168,107,0.35);
}}
.local-badge {{
    display: inline-block; padding: 2px 9px; border-radius: 3px;
    font-size: 0.68rem; font-weight: 600; letter-spacing: 0.05em;
    background: rgba(31,157,107,0.14); color: var(--pos);
    border: 1px solid rgba(31,157,107,0.35);
}}

/* Tabs: quieter, with an accent underline on the active tab */
button[data-baseweb="tab"] {{
    font-size: 0.86rem; letter-spacing: 0.03em;
}}
button[data-baseweb="tab"][aria-selected="true"] {{
    border-bottom-color: var(--accent) !important;
    color: #f3f4f6 !important;
}}

/* Main-area section navigation (under branded header) */
.arp-nav {{
    margin-top: 4px;
    padding-bottom: 4px;
}}
.arp-nav-label {{
    font-size: 0.62rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #7a8494;
    margin: 10px 0 6px 0;
}}
.arp-nav .stButton > button {{
    font-size: 0.78rem;
    letter-spacing: 0.03em;
    border-radius: 3px;
    padding: 0.35rem 0.5rem;
    min-height: 2rem;
}}
.arp-nav .stButton > button[kind="primary"] {{
    background: rgba(200,168,107,0.18) !important;
    border: 1px solid rgba(200,168,107,0.55) !important;
    color: #f3f4f6 !important;
}}
.arp-nav .stButton > button[kind="secondary"] {{
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    color: #b8c0cc !important;
}}
.arp-nav .stButton > button[kind="secondary"]:hover {{
    border-color: rgba(200,168,107,0.35) !important;
    color: #e8eaed !important;
}}
.arp-mode-switch {{
    margin: 12px 0 8px 0;
}}
.arp-mode-switch .stButton > button {{
    font-size: 0.82rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    min-height: 2.35rem;
}}
</style>
""", unsafe_allow_html=True)

# ── Sidebar: auth ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ARP Global Capital")
    st.caption("Local AI Investment Intelligence")
    st.divider()

    token = st.text_input("Bearer token", type="password",
                           placeholder="Paste your token…", key="token_input")

    if not token:
        st.info("Enter your token to continue.")
        st.stop()

    try:
        if (
            st.session_state.get("_me_token") == token
            and st.session_state.get("_me")
        ):
            me = st.session_state["_me"]
        else:
            me_resp = requests.get(
                f"{API_URL}/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            if me_resp.status_code != 200:
                st.error("Invalid token.")
                st.stop()
            me = me_resp.json()
            st.session_state["_me"] = me
            st.session_state["_me_token"] = token
    except Exception as e:
        st.error(f"Cannot reach API: {e}")
        st.stop()

    # ── Session inactivity check ─────────────────────────────────────────────
    import time as _time
    now = _time.time()
    last_active = st.session_state.get("last_active", now)
    idle_minutes = (now - last_active) / 60

    if idle_minutes > SESSION_TIMEOUT_MINUTES:
        st.error(
            f"Session expired after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. "
            "Please re-enter your token to continue."
        )
        st.session_state.pop("last_active", None)
        st.stop()

    st.session_state["last_active"] = now
    st.success(f"**{me['email']}**")
    st.markdown(f"<span class='role-pill'>{me['role'].upper()}</span>", unsafe_allow_html=True)
    st.caption(me.get("description", ""))
    perms = me.get("permissions", [])
    if perms:
        st.caption(f"Access: {', '.join(perms)}")

    # ── LLM status (cached — avoids hammering Ollama on every widget rerun) ───
    st.divider()
    _SOURCE_LABEL = {
        "mock": "Modelled (offline)",
        "yahoo": "Yahoo Finance (live)",
        "alphavantage": "Alpha Vantage (live)",
    }
    health = _fetch_health(API_URL, headers={"Authorization": f"Bearer {token}"})
    if health is None:
        st.warning("Cannot reach API health endpoint.")
        chosen_model = None
    else:
        if st.session_state.get("_health_cache", {}).get("stale"):
            st.caption("API busy — showing last known model status.")
        if health.get("llm_online"):
            models = health.get("llm_models", [])
            primary = health.get("llm_primary", "phi3:mini")
            if primary in models:
                default_idx = models.index(primary)
            else:
                default_idx = 0
                primary = models[0] if models else primary
            st.markdown(
                "<span class='local-badge'>LOCAL · ON-DEVICE</span>", unsafe_allow_html=True
            )
            st.caption(f"Default model: `{primary}`")
            st.caption("Inference runs locally. No client data leaves this machine.")

            # Model selector (analyst/risk only) — defaults to OLLAMA_MODEL, not first in list
            if me["role"] in ("analyst", "risk", "admin") and len(models) > 1:
                chosen_model = st.selectbox(
                    "Switch model", models, index=default_idx, key="model_select"
                )
            else:
                chosen_model = primary
        else:
            st.warning(
                "Local model offline — AI Agents unavailable; other tabs still work. "
                "Docker: pull `phi3:mini` after compose up (`docs/OLLAMA.md`)."
            )
            chosen_model = None

    if health is not None:
        _src = health.get("market_data_source", "mock")
        st.caption(f"Market data: {_SOURCE_LABEL.get(_src, _src)}")

# ── Helpers ───────────────────────────────────────────────────────────────────
headers = {"Authorization": f"Bearer {token}"}


def _api_fetch(path: str, params: dict | None, auth_headers: dict) -> dict:
    try:
        r = requests.get(
            f"{API_URL}{path}",
            headers=auth_headers,
            params=params,
            timeout=12,
        )
        if r.status_code == 403:
            detail = r.json().get("detail", "Access denied.")
            return {"error": detail, "allowed": False}
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text[:200])
            return {"error": detail, "allowed": False}
        return r.json()
    except Exception as e:
        return {"error": str(e), "allowed": False}


def api(path: str, params: dict = None, *, cache: bool = True, ttl: int | None = None) -> dict:
    """GET helper with short-lived session cache (default 30s)."""
    if not cache:
        return _api_fetch(path, params, headers)
    ttl = ttl if ttl is not None else API_CACHE_SECONDS
    cache_key = (path, tuple(sorted((params or {}).items())), token)
    store = st.session_state.setdefault("_api_cache", {})
    entry = store.get(cache_key)
    now = time.time()
    if entry and now - entry["ts"] < ttl:
        return entry["data"]
    data = _api_fetch(path, params, headers)
    store[cache_key] = {"ts": now, "data": data}
    return data

def denied(data: dict) -> bool:
    """True only when access was denied or the HTTP request failed."""
    if data.get("allowed") is False:
        return True
    if data.get("allowed") is True:
        return False
    return bool(data.get("error"))

# Map status/severity to a CSS pill class (no emojis anywhere).
_STATUS_CLASS = {
    "EXECUTED": "pill-pos", "PENDING": "pill-warn",
    "FLAGGED": "pill-neg", "CANCELLED": "pill-mute",
}
_SEVERITY_CLASS = {
    "CRITICAL": "pill-neg", "HIGH": "pill-neg",
    "MEDIUM": "pill-warn", "LOW": "pill-mute",
}

def pill(text: str, css_class: str) -> str:
    return f"<span class='pill {css_class}'>{text}</span>"

def status_pill(s: str) -> str:
    return pill(s, _STATUS_CLASS.get(s, "pill-mute"))

def severity_pill(s: str) -> str:
    return pill(s, _SEVERITY_CLASS.get(s, "pill-mute"))

def risk_band(score: float) -> str:
    if score > 0.8:
        return "High"
    if score > 0.5:
        return "Elevated"
    return "Low"


def _pnl_style(val: float) -> str:
    """CSS for positive (green) / negative (red) P&L and price changes."""
    color = POS if val >= 0 else NEG
    return f"color: {color}; font-weight: 600"


def _nav_button_row(labels: list[str], cols_per_row: int) -> None:
    """Render a row of section buttons; updates session_state.main_nav on click."""
    for offset in range(0, len(labels), cols_per_row):
        row = labels[offset : offset + cols_per_row]
        cols = st.columns(cols_per_row)
        for idx, col in enumerate(cols):
            with col:
                if idx >= len(row):
                    continue
                label = row[idx]
                is_active = st.session_state.main_nav == label
                if st.button(
                    label,
                    key=f"nav_btn_{label}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    if not is_active:
                        st.session_state.main_nav = label
                        st.rerun()


def _render_workspace_nav(intel_available: bool, role: str) -> tuple[str, str]:
    """
    Two workspace modes under the branded header; sub-nav shows sections for the active mode only.
    Returns (ui_mode, active_view) where ui_mode is 'book' | 'intel'.
    """
    if "ui_mode" not in st.session_state:
        st.session_state.ui_mode = "book"
    if st.session_state.ui_mode == "intel" and not intel_available:
        st.session_state.ui_mode = "book"

    st.markdown("<div class='arp-nav'>", unsafe_allow_html=True)

    st.markdown("<div class='arp-mode-switch'>", unsafe_allow_html=True)
    mode_l, mode_r = st.columns(2)
    with mode_l:
        is_book = st.session_state.ui_mode == "book"
        if st.button(
            "Book & markets",
            key="ws_mode_book",
            use_container_width=True,
            type="primary" if is_book else "secondary",
        ):
            if not is_book:
                st.session_state.ui_mode = "book"
                st.session_state.main_nav = _nav_core_for_role(role)[0]
                st.rerun()
    with mode_r:
        if intel_available:
            is_intel = st.session_state.ui_mode == "intel"
            if st.button(
                "Intelligence & operations",
                key="ws_mode_intel",
                use_container_width=True,
                type="primary" if is_intel else "secondary",
            ):
                if not is_intel:
                    st.session_state.ui_mode = "intel"
                    st.session_state.main_nav = _NAV_EXPANSION[0]
                    st.rerun()
        else:
            st.button(
                "Intelligence & operations",
                key="ws_mode_intel_off",
                use_container_width=True,
                disabled=True,
                help="Set ENABLE_ENHANCEMENTS=1 to enable the expansion workspace.",
            )
    st.markdown("</div>", unsafe_allow_html=True)

    sections = (
        _nav_core_for_role(role) if st.session_state.ui_mode == "book" else _NAV_EXPANSION
    )
    _legacy_nav = {"Shadow Book": "Ideas"}
    if st.session_state.get("main_nav") in _legacy_nav:
        st.session_state.main_nav = _legacy_nav[st.session_state.main_nav]
    if st.session_state.get("main_nav") not in sections:
        st.session_state.main_nav = sections[0]

    st.markdown("<div class='arp-nav-label'>Sections</div>", unsafe_allow_html=True)
    cols_per_row = 6 if st.session_state.ui_mode == "book" else 4
    _nav_button_row(sections, cols_per_row=cols_per_row)

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()
    return st.session_state.ui_mode, st.session_state.main_nav


# ── Branded header + section navigation ───────────────────────────────────────
st.markdown(
    "<div class='arp-header'>"
    "<div class='arp-brand'>ARP GLOBAL CAPITAL</div>"
    "<div class='arp-sub'>Investment Intelligence Platform &nbsp;·&nbsp; DIFC &nbsp;·&nbsp; "
    "Discretionary Multi-Asset Mandate &nbsp;·&nbsp; Technical assessment prototype</div>"
    "</div>",
    unsafe_allow_html=True,
)

ui_mode = "book"
active_view = "Portfolio"
if me["role"] not in ("manager", "intern"):
    ui_mode, active_view = _render_workspace_nav(_INTEL_WORKSPACE_AVAILABLE, me["role"])

BOOK_MODE = ui_mode == "book"
INTEL_MODE = ui_mode == "intel"

# ── Executive view (manager / COO — fundamentals only) ───────────────────────
if me["role"] == "manager":
    st.title("Executive Summary")
    st.caption(
        "COO / manager read-only view · Fund KPIs, asset-class mix, top-3 concentration "
        "weights, and risk alert counts · No full book, blotter, audit, or AI agents"
    )
    st.info(
        "**This is the full manager (COO) UI** — one Executive Summary screen by RBAC design, "
        "not a missing dashboard. Briefing, reporting, and Compliance live under "
        "**Intelligence & operations**; sign in as **`risk@local`** or **`admin@local`** "
        "to demo those (`docs/ROLES.md`)."
    )
    st.caption(
        "**Demo book** — P&L and AUM are from a seeded model portfolio for this assessment, "
        "not live fund NAV."
    )
    st.divider()

    summary = api("/portfolio/summary")
    risk_exec = api("/risk/alerts")

    if denied(summary):
        st.warning(summary.get("error", "Portfolio summary not available."))
    else:
        pnl = summary["total_pnl"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total AUM", f"${summary['total_aum']:,.0f}")
        c2.metric(
            "Total P&L",
            f"${pnl:,.0f}",
            f"{summary['total_pnl_pct']:.2f}%",
            delta_color="normal",
        )
        c3.metric("Positions", summary["num_positions"])
        breakdown = summary.get("asset_class_breakdown", {})
        c4.metric("Asset classes", len(breakdown))

        if not denied(risk_exec) and risk_exec.get("executive_summary"):
            by_sev = risk_exec.get("by_severity", {})
            open_n = risk_exec.get("alert_count", 0)
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Open risk items", open_n)
            r2.metric("Critical", by_sev.get("CRITICAL", 0))
            r3.metric("High", by_sev.get("HIGH", 0))
            r4.metric("Rules checked", risk_exec.get("rules_checked", 0))
            if open_n > 0:
                st.warning(
                    f"{open_n} mandate rule(s) currently triggered. "
                    "Counts only — contact Risk & Compliance for rule detail and remediation."
                )
            else:
                st.success("No active mandate breaches against current rules.")
        elif denied(risk_exec):
            st.info("Risk alert counts require executive summary access.")
        else:
            st.caption("Risk summary unavailable.")

        col_left, col_right = st.columns([1, 1])
        with col_left:
            st.subheader("Asset class allocation")
            if breakdown:
                fig = px.pie(
                    names=list(breakdown.keys()),
                    values=list(breakdown.values()),
                    hole=0.55,
                    color=list(breakdown.keys()),
                    color_discrete_map=CLASS_COLORS,
                )
                fig.update_traces(textposition="inside", textinfo="percent+label",
                                  sort=False)
                fig.update_layout(
                    showlegend=True, height=320, margin=dict(t=0, b=0, l=0, r=0),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No allocation data.")

        with col_right:
            st.subheader("Top-3 concentration")
            top3 = summary.get("top_3_holdings", [])
            if top3:
                df_top = pd.DataFrame(top3)
                st.dataframe(
                    df_top.rename(columns={
                        "ticker": "Ticker",
                        "weight": "Weight %",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
                fig_w = px.bar(
                    df_top,
                    x="ticker",
                    y="weight",
                    text="weight",
                    labels={"weight": "Weight (%)", "ticker": ""},
                )
                fig_w.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig_w.update_layout(height=280, margin=dict(t=10, b=10))
                st.plotly_chart(fig_w, use_container_width=True)
                st.caption(
                    "Executive tier: ticker and weight only — no position-level P&L or market values."
                )
            else:
                st.info("No holdings in summary.")

        st.divider()
        st.markdown(
            "**Segregation of duties:** This role sees fund-level KPIs and capped concentration "
            "indicators (top-3 weights) only. It cannot view the full holdings book, trade blotter, "
            "market movers, AI agent tooling, or audit logs. "
            "Operational detail is available to `analyst@local` and `risk@local`."
        )
        st.caption(
            "The full **Intelligence & operations** workspace (briefing, reporting, compliance) "
            "requires **`risk@local`** or **`admin@local`** login."
        )
    st.stop()

# ── Intern / onboarding — zero data access (RBAC demo) ───────────────────────
if me["role"] == "intern":
    st.title("Onboarding access")
    st.caption(
        "Training role with no data permissions · All portfolio, trade, and agent endpoints "
        "return RBAC denials · Attempts are written to the audit trail"
    )
    st.divider()

    st.info(
        "This login demonstrates **least-privilege** access for new joiners. "
        "The platform UI is available, but every data tool denies at the API layer — "
        "the same enforcement point used in production."
    )

    st.markdown("#### Live deny probe")
    st.caption("Calls the portfolio summary endpoint; expect a policy violation message.")
    if st.button("Test portfolio access", type="primary", key="intern_probe"):
        probe = api("/portfolio/summary", cache=False)
        if denied(probe):
            st.warning(probe.get("error", "Access denied."))
        else:
            st.error("Unexpected: intern received data — check RBAC configuration.")

    st.divider()
    st.markdown(
        "**Demo flow:** Re-authenticate with **`analyst@local`** (portfolio, no trades) or "
        "**`risk@local`** (full ops + audit) to explore the dashboard. "
        "Ask the AI *“which trades need review?”* as analyst to show a deny + audit log entry."
    )
    st.stop()

# ── Full dashboard (analyst + risk + admin) ──────────────────────────────────

# ════════════════════════════════════════════════════════════════════════════════
# PORTFOLIO
# ════════════════════════════════════════════════════════════════════════════════
if BOOK_MODE and active_view == "Portfolio":
    st.info(
        "**Demo book** — P&L and AUM are from a seeded model portfolio for this assessment, "
        "not live fund NAV. See **Reporting → P&L Attribution** for data-scope notes."
    )
    summary  = api("/portfolio/summary")
    exposure = api("/portfolio/exposure")

    if denied(summary):
        st.warning(summary.get("error", "Access denied."))
    elif summary.get("error"):
        st.info(summary["error"])
    else:
        # ── Top metrics ──────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total AUM",    f"${summary['total_aum']:,.0f}")
        c2.metric("Positions",     summary["num_positions"])
        pnl = summary["total_pnl"]
        c3.metric("Total P&L",    f"${pnl:,.0f}",
                  f"{summary['total_pnl_pct']:.2f}%",
                  delta_color="normal")
        breakdown = summary.get("asset_class_breakdown", {})
        top_class = max(breakdown, key=breakdown.get) if breakdown else "—"
        c4.metric("Largest Class", f"{top_class}",
                  f"{breakdown.get(top_class, 0):.1f}%")

        st.divider()
        col_l, col_r = st.columns([1, 1])

        # ── Asset-class allocation ───────────────────────────────────────────
        with col_l:
            st.subheader("Asset Allocation")
            if breakdown:
                fig = px.pie(
                    names=list(breakdown.keys()),
                    values=list(breakdown.values()),
                    hole=0.55,
                    color=list(breakdown.keys()),
                    color_discrete_map=CLASS_COLORS,
                )
                fig.update_traces(textposition="inside", textinfo="percent+label",
                                  sort=False)
                fig.update_layout(showlegend=False, height=320,
                                  margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig, use_container_width=True)

        # ── Top holdings ─────────────────────────────────────────────────────
        with col_r:
            st.subheader("Top Holdings")
            for h in summary.get("top_3_holdings", []):
                st.metric(
                    label=f"{h['ticker']}  ·  {h['weight']:.1f}% of AUM",
                    value=f"${h['mv']:,.0f}",
                    delta=f"{h['pnl_pct']:+.2f}%",
                    delta_color="normal",
                )

        st.divider()

        # ── Exposure bar + table ─────────────────────────────────────────────
        if not denied(exposure) and exposure.get("positions"):
            st.subheader("Position-Level Exposure")
            df = pd.DataFrame(exposure["positions"]).sort_values(
                "weight_pct", ascending=False
            )

            fig2 = px.bar(df, x="ticker", y="weight_pct",
                          color="asset_class",
                          labels={"weight_pct": "Weight (%)", "ticker": ""},
                          color_discrete_map=CLASS_COLORS)
            fig2.add_hline(y=8, line_dash="dot", line_color=ACCENT, opacity=0.7,
                           annotation_text="Single-name limit 8%",
                           annotation_position="top right")
            fig2.update_layout(height=300, margin=dict(t=10, b=10),
                               legend_title_text="", xaxis_title="")
            st.plotly_chart(fig2, use_container_width=True)
            st.caption(
                "The 8% concentration limit applies to single issuers. Pooled funds "
                "(equity ETFs, bond funds) are diversified by construction and are exempt."
            )

            df_disp = df.copy()
            df_disp["current_price"] = df_disp["current_price"].apply(lambda p: f"${p:,.2f}")
            df_disp["market_value"]  = df_disp["market_value"].apply(lambda v: f"${v:,.0f}")
            df_disp["quantity"]      = df_disp["quantity"].apply(lambda q: f"{q:,.2f}")
            df_disp["weight_pct"]    = df_disp["weight_pct"].apply(lambda w: f"{w:.1f}%")
            display_cols = ["ticker", "name", "asset_class", "quantity",
                            "current_price", "market_value", "weight_pct", "pnl_pct"]
            col_names = {
                "ticker": "Instrument", "name": "Name", "asset_class": "Class",
                "quantity": "Units", "current_price": "Price",
                "market_value": "Market Value", "weight_pct": "Weight",
                "pnl_pct": "Unrealised P&L",
            }
            table = df_disp[display_cols].rename(columns=col_names)
            st.dataframe(
                table.style.map(_pnl_style, subset=["Unrealised P&L"])
                .format({"Unrealised P&L": "{:+.2f}%"}),
                use_container_width=True,
                hide_index=True,
            )

# ════════════════════════════════════════════════════════════════════════════════
# TRADES
# ════════════════════════════════════════════════════════════════════════════════
elif BOOK_MODE and active_view == "Trades":
    st.subheader("Recent Trades")
    trades_data = api("/trades/recent", {"limit": 45})

    if denied(trades_data):
        st.warning(trades_data.get("error", "Access denied."))
    else:
        trades = trades_data.get("trades", [])
        if not trades:
            st.info("No trades found.")
        else:
            df = pd.DataFrame(trades)

            # Summary metrics
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Trades (window)", len(df))
            c2.metric("In Compliance Queue", int((df["status"] == "FLAGGED").sum()))
            c3.metric("Pending Settlement",  int((df["status"] == "PENDING").sum()))
            c4.metric("Avg Risk Score", f"{df['risk_score'].mean():.2f}")

            st.divider()

            # Filter controls
            col_f1, col_f2 = st.columns(2)
            status_filter = col_f1.multiselect(
                "Filter by status",
                options=df["status"].unique().tolist(),
                default=df["status"].unique().tolist(),
            )
            dir_filter = col_f2.multiselect(
                "Direction",
                options=df["direction"].unique().tolist(),
                default=df["direction"].unique().tolist(),
            )

            df_filtered = df[
                df["status"].isin(status_filter) &
                df["direction"].isin(dir_filter)
            ].copy()

            df_filtered["Risk"] = df_filtered["risk_score"].apply(
                lambda r: f"{r:.2f} ({risk_band(r)})"
            )
            df_filtered["Notional"] = df_filtered["notional"].apply(lambda n: f"${n:,.0f}")
            df_filtered["Price"]    = df_filtered["price"].apply(lambda p: f"${p:,.2f}")
            df_filtered["Qty"]      = df_filtered["quantity"].apply(lambda q: f"{q:,.0f}")

            display = df_filtered[[
                "status", "ticker", "direction", "Qty",
                "Price", "Notional", "trader", "Risk", "traded_at"
            ]].rename(columns={
                "status": "Status", "ticker": "Instrument", "direction": "Side",
                "trader": "Trader", "traded_at": "Timestamp (UTC)",
            })
            st.dataframe(display, use_container_width=True, hide_index=True)

            # Notional distribution chart
            st.subheader("Notional Distribution by Status")
            fig = px.box(df, x="status", y="notional", color="status",
                         color_discrete_map={
                             "EXECUTED": POS, "PENDING": WARN,
                             "FLAGGED": NEG, "CANCELLED": "#9aa3b2",
                         })
            fig.update_layout(height=260, showlegend=False,
                              margin=dict(t=10, b=10),
                              xaxis_title="", yaxis_title="Notional (USD)")
            st.plotly_chart(fig, use_container_width=True)

# ════════════════════════════════════════════════════════════════════════════════
# RISK
# ════════════════════════════════════════════════════════════════════════════════
elif BOOK_MODE and active_view == "Risk":
    col_alerts, col_flagged = st.columns([1, 1])

    with col_alerts:
        st.subheader("Active Risk Alerts")
        alerts_data = api("/risk/alerts")
        if denied(alerts_data):
            st.warning(alerts_data.get("error", "Access denied."))
        else:
            alerts = alerts_data.get("alerts", [])
            rules_checked = alerts_data.get("rules_checked", 0)
            if not alerts:
                st.success(f"No active breaches. {rules_checked} mandate rules evaluated.")
            else:
                st.error(f"{len(alerts)} active breach(es) across {rules_checked} mandate rules.")
                for a in alerts:
                    with st.expander(f"[{a['severity']}]  {a['rule']}"):
                        st.markdown(severity_pill(a["severity"]), unsafe_allow_html=True)
                        st.write(a["description"])
                        st.caption(a["detail"])

    with col_flagged:
        st.subheader("Flagged Trades")
        flagged_data = api("/risk/flagged")
        if denied(flagged_data):
            st.warning(flagged_data.get("error", "Access denied."))
        else:
            flagged = flagged_data.get("flagged_trades", [])
            if not flagged:
                st.success("Compliance queue clear. No trades pending review.")
            else:
                st.warning(f"{len(flagged)} trade(s) pending compliance review.")
                for t in flagged:
                    with st.expander(
                        f"{t['ticker']}  {t['direction']}  ·  "
                        f"risk {t['risk_score']:.2f}  ·  {t['status']}"
                    ):
                        c1, c2 = st.columns(2)
                        c1.write(f"**Notional:** ${t['notional']:,.0f}")
                        c2.write(f"**Trader:** {t['trader']}")
                        st.write(f"**Reason:** {t['explanation']}")
                        if t.get("notes"):
                            st.caption(f"Note: {t['notes']}")
                        st.caption(f"Traded: {t['traded_at']} UTC")

# ════════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ════════════════════════════════════════════════════════════════════════════════
elif BOOK_MODE and active_view == "Market":
    st.subheader("Market Movers")
    movers_data = api("/market/movers", {"top_n": 8})

    if denied(movers_data):
        st.warning(movers_data.get("error", "Access denied."))
    else:
        gainers = movers_data.get("top_gainers", [])
        losers  = movers_data.get("top_losers", [])

        col_g, col_l = st.columns(2)

        with col_g:
            st.markdown("#### Top Gainers")
            if gainers:
                for g in gainers:
                    st.metric(
                        label=f"{g['ticker']}",
                        value=f"${g['price']:,.2f}",
                        delta=f"{g['change_pct']:+.2f}%",
                        delta_color="normal",
                    )
            else:
                st.info("No gainers data.")

        with col_l:
            st.markdown("#### Top Losers")
            if losers:
                for l in losers:
                    st.metric(
                        label=f"{l['ticker']}",
                        value=f"${l['price']:,.2f}",
                        delta=f"{l['change_pct']:+.2f}%",
                        delta_color="normal",
                    )
            else:
                st.info("No losers data.")

        # Combined bar chart
        st.divider()
        all_movers = gainers + losers
        if all_movers:
            df_m = pd.DataFrame(all_movers)
            df_m = df_m.sort_values("change_pct", ascending=False)
            df_m["color"] = df_m["change_pct"].apply(
                lambda x: POS if x >= 0 else NEG
            )
            y_vals = df_m["change_pct"]
            y_span = float(y_vals.max() - y_vals.min()) or 1.0
            # Pad y-axis so outside bar labels are not clipped (top/bottom).
            label_pad = max(y_span * 0.22, float(y_vals.abs().max()) * 0.18, 0.6)
            y_min = min(float(y_vals.min()), 0.0) - label_pad
            y_max = max(float(y_vals.max()), 0.0) + label_pad
            n_bars = len(df_m)
            chart_h = max(360, 300 + n_bars * 12)

            fig = go.Figure(go.Bar(
                x=df_m["ticker"],
                y=df_m["change_pct"],
                marker_color=df_m["color"],
                text=df_m["change_pct"].apply(lambda x: f"{x:+.2f}%"),
                textposition="outside",
                cliponaxis=False,
            ))
            fig.update_layout(
                title="Daily Change (%)",
                xaxis_title="",
                yaxis_title="Change (%)",
                height=chart_h,
                margin=dict(t=56, b=64, l=56, r=24),
                showlegend=False,
                xaxis=dict(
                    automargin=True,
                    tickangle=-35 if n_bars > 6 else 0,
                    categoryorder="array",
                    categoryarray=df_m["ticker"].tolist(),
                ),
                yaxis=dict(
                    range=[y_min, y_max],
                    automargin=True,
                    zeroline=True,
                    zerolinecolor="rgba(255,255,255,0.25)",
                    zerolinewidth=1,
                ),
            )
            fig.add_hline(y=0, line_color="rgba(255,255,255,0.35)", line_width=1)
            st.plotly_chart(fig, use_container_width=True)

        src = movers_data.get("top_gainers", [{}])[0].get("source", "mock") if gainers else "mock"
        st.caption(f"Source: `{src}` · Updated: {movers_data.get('top_gainers', [{}])[0].get('fetched_at', '—') if gainers else '—'}")

# ════════════════════════════════════════════════════════════════════════════════
# AI AGENTS
# ════════════════════════════════════════════════════════════════════════════════
elif BOOK_MODE and active_view == "AI Agents":
    st.subheader("Ask the AI Agents")

    _agent_health = _fetch_health(API_URL, headers=headers)
    if _agent_health is None:
        st.warning(
            "Cannot verify LLM status. Portfolio, trades, risk, and market data still work — "
            "see `docs/OLLAMA.md` if AI answers fail."
        )
    elif not _agent_health.get("llm_online"):
        st.warning(
            "**AI answers unavailable** — Ollama is offline or still starting. "
            "Other dashboard tabs work without the LLM. "
            "Docker: wait for `docker compose up` to finish (Ollama health gate can take 1–3 min), "
            "then `docker exec arp_ollama ollama pull phi3:mini` and `make warm-model`."
        )
    else:
        _primary = _agent_health.get("llm_primary", "phi3:mini")
        _models = _agent_health.get("llm_models") or []
        _has_primary = any(
            _primary == m or _primary.split(":")[0] in m for m in _models
        )
        if not _models or not _has_primary:
            st.warning(
                f"Ollama is up but **`{_primary}` is not pulled**. "
                f"Run `docker exec arp_ollama ollama pull {_primary}` (or `ollama pull` locally) "
                "and `make warm-model` — otherwise replies show `[LLM UNAVAILABLE]`."
            )

    with st.expander("Docker / first-start notes", expanded=False):
        st.markdown(
            "- **First `docker compose up`** waits on Ollama healthy before the API starts "
            "(often 1–3 minutes — not a failed build).\n"
            "- **Ollama healthy ≠ model downloaded** — pull `phi3:mini` after the stack is up.\n"
            "- **Tools work without the LLM** — only this tab and other NL features need Ollama.\n"
            "- Before a live demo: `make warm-model` (avoids a 30–120s first question).\n"
            "- Details: `docs/OLLAMA.md` in the repository."
        )

    st.caption(
        "Choose **portfolio** or **risk** agent, then ask a question. Tool selection is a "
        "**keyword-scored pre-stage** (up to two RBAC-gated DB tools per question) — the LLM "
        "does not invoke tools itself. Answers synthesize live tool JSON, local RAG, and the "
        "knowledge graph via a **local LLM (no data leaves this machine)**. "
        "Production would add an LLM planner or MCP tools; tool access is audit-logged."
    )

    agent_choice = st.radio("Agent", ["portfolio", "risk"], horizontal=True)

    with st.expander("Conversation memory", expanded=False):
        st.caption(
            "The agents recall your recent interactions to keep follow-up questions "
            "in context. Memory is per-user and stored locally."
        )
        mem = api("/memory", {"limit": 5}, cache=False)
        mem_status = (mem or {}).get("status", {}) if isinstance(mem, dict) else {}
        st.caption(
            f"Stored for you: {mem_status.get('stored_user', 0)} interaction(s)  ·  "
            f"recall window: {mem_status.get('recall_limit', 0)} turns."
        )
        hist = (mem or {}).get("history", []) if isinstance(mem, dict) else []
        if hist:
            for h in hist:
                st.markdown(
                    f"- `{h.get('agent','')}` · {h.get('created_at','')} — "
                    f"{h.get('question','')}"
                )
        else:
            st.caption("No stored interactions yet — ask a question to start building memory.")
        if st.button("Clear my memory", key="clear_memory"):
            try:
                requests.delete(f"{API_URL}/memory", headers=headers, timeout=10)
                st.success("Memory cleared.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not clear memory: {exc}")

    EXAMPLES = {
        "portfolio": [
            "What are our top holdings?",
            "What is our asset allocation?",
            "Which assets moved the most today?",
            "Summarise portfolio performance",
        ],
        "risk": [
            "Are we overexposed to any asset?",
            "What is our crypto exposure limit?",
            "Which trades need review?",
            "What sign-off is required for large trades?",
        ],
    }

    if "ask_question" not in st.session_state:
        st.session_state.ask_question = ""

    st.caption("Quick questions:")
    btn_cols = st.columns(4)
    for i, q in enumerate(EXAMPLES[agent_choice]):
        if btn_cols[i].button(q, key=f"quick_{agent_choice}_{i}", use_container_width=True):
            st.session_state.ask_question = q
            st.rerun()

    with st.form("ask_agent_form", clear_on_submit=False):
        st.text_input(
            "Your question",
            key="ask_question",
            placeholder="Ask anything about the portfolio or risk…",
        )
        submitted = st.form_submit_button("Ask", type="primary")

    if submitted:
        question = st.session_state.get("ask_question", "").strip()
        if not question:
            st.warning("Enter a question first.")
        else:
            st.session_state["last_q"] = question
            model_label = chosen_model or "default"
            with st.spinner(
                f"Running agent + local LLM (`{model_label}`) — "
                "first reply can take 30–120s on a laptop…"
            ):
                t0 = time.time()
                try:
                    payload = {
                        "question": question.strip(),
                        "agent":    agent_choice,
                    }
                    if chosen_model:
                        payload["model"] = chosen_model

                    r = requests.post(
                        f"{API_URL}/ask",
                        headers=headers,
                        json=payload,
                        timeout=200,
                    )
                    elapsed = round(time.time() - t0, 2)
                    result  = r.json()
                    st.session_state["ask_result"] = {
                        "status": r.status_code,
                        "result": result,
                        "elapsed": elapsed,
                        "model": model_label,
                    }
                except requests.exceptions.Timeout:
                    st.session_state["ask_result"] = {
                        "error": (
                            "Request timed out. Large models (e.g. deepseek-llm:7b) are slow "
                            "on CPU — switch sidebar model to **llama3.2:latest** (if pulled) or confirm **phi3:mini** is loaded."
                        ),
                    }
                except Exception as e:
                    st.session_state["ask_result"] = {"error": str(e)}

    ask_out = st.session_state.get("ask_result")
    if ask_out:
        if ask_out.get("error"):
            st.error(ask_out["error"])
        else:
            result = ask_out["result"]
            elapsed = ask_out.get("elapsed", 0)
            model_label = ask_out.get("model", "local")
            if ask_out["status"] == 200:
                if result.get("allowed"):
                    if result.get("llm_error"):
                        st.error(
                            "Local model unavailable — Ollama did not return an answer. "
                            "The database tool ran successfully; start Ollama or pull the "
                            "sidebar model, then try again."
                        )
                        st.code(result.get("response", ""), language=None)
                    else:
                        mem_used = result.get("memory_used", 0)
                        mem_note = (
                            f"  ·  Memory: {mem_used} prior turn{'s' if mem_used != 1 else ''} recalled"
                            if mem_used else ""
                        )
                        tools_called = result.get("tools_called") or [result["tool_called"]]
                        if len(tools_called) > 1:
                            tool_label = " + ".join(f"`{t}`" for t in tools_called)
                        else:
                            tool_label = f"`{result['tool_called']}`"
                        llm_model = result.get("llm_model") or model_label
                        fallback_note = " (fallback)" if result.get("llm_fallback_used") else ""
                        st.success(
                            f"**Tool called:** {tool_label}  ·  "
                            f"Response time: {elapsed}s  ·  "
                            f"Model: `{llm_model}`{fallback_note}  ·  Data: on-device"
                            f"{mem_note}"
                        )
                        grounding = result.get("grounding") or {}
                        gq = grounding.get("quality", "")
                        if gq == "live_only":
                            st.info(
                                "Grounding: live database only — no policy RAG excerpts matched this question."
                            )
                        elif gq == "degraded":
                            st.warning(
                                "Grounding: degraded — RAG index is hash-only; verify policy claims against sources."
                            )
                        elif gq == "full" and grounding.get("detail"):
                            st.caption(f"Grounding: {grounding['detail']}")
                        st.write(result["response"])
                    if result.get("rag_sources"):
                        with st.expander(
                            f"RAG sources — {len(result['rag_sources'])} policy excerpts retrieved"
                        ):
                            for src in result["rag_sources"]:
                                st.markdown(
                                    f"**{src.get('title', 'Document')}** "
                                    f"(`{src.get('source', '')}`, "
                                    f"{src.get('retrieval', 'retrieval')})"
                                )
                                st.caption((src.get("text") or "")[:400] + "…")
                    if result.get("graph_facts"):
                        with st.expander(
                            f"Knowledge graph — {len(result['graph_facts'])} relationships traversed"
                        ):
                            st.caption(
                                "Entities and relationships derived from live data and policy, "
                                "linked across tickers, sectors, limits, trades, and regulations."
                            )
                            for fact in result["graph_facts"]:
                                detail = fact.get("detail") or ""
                                source = fact.get("source") or ""
                                tail = "  ·  ".join(b for b in (detail, source) if b)
                                st.markdown(
                                    f"`{fact.get('subject','')}` "
                                    f"**—[{fact.get('relation','')}]→** "
                                    f"`{fact.get('object','')}`"
                                )
                                if tail:
                                    st.caption(tail)
                    if result.get("data"):
                        with st.expander("Context transparency — live data provided to the model"):
                            st.caption(
                                "Structured database context passed to the local model."
                            )
                            st.json(result["data"])
                else:
                    st.error(
                        f"**Access denied.**\n\n{result.get('response', '')}\n\n"
                        f"This attempt has been logged to the audit trail."
                    )
            elif ask_out["status"] == 429:
                retry = result.get("retry_after", 1)
                st.warning(
                    f"Rate limit reached ({result.get('detail', 'too many requests')}). "
                    f"Wait {retry}s and try again, or set `RATE_LIMIT_ASK_MAX=120` / "
                    "`ENABLE_RATE_LIMITER=0` in `.env` for local testing."
                )
            else:
                st.error(
                    f"API error {ask_out['status']}: "
                    f"{result.get('detail', 'Unknown error')}"
                )

# ════════════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ════════════════════════════════════════════════════════════════════════════════
elif BOOK_MODE and active_view == "Audit Log":
    st.subheader("Audit Log")
    st.caption(
        "Every AI agent interaction — allowed or denied — is recorded with "
        "user identity, role, tool invoked, and a SHA-256 hash chain for tamper evidence. "
        "Access requires the **risk** or **admin** role."
    )

    audit_data = api("/audit/logs", {"limit": 100})

    if denied(audit_data):
        st.warning(
            audit_data.get("error",
                "Audit log access requires the risk or admin role. Log in as risk@local or admin@local.")
        )
    else:
        logs = audit_data.get("logs", [])
        if not logs:
            st.info("No audit entries yet.")
        else:
            df = pd.DataFrame(logs)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(df))
            c2.metric("Allowed", int((df["result"] == "ALLOWED").sum()))
            c3.metric("Denied",  int((df["result"] == "DENIED").sum()))
            deny_rate = round(int((df["result"] == "DENIED").sum()) / len(df) * 100, 1)
            c4.metric("Deny rate", f"{deny_rate}%")

            st.divider()
            st.markdown("#### Interaction timeline")
            for entry in logs[:10]:
                tag = entry["result"]
                hash_preview = entry.get("hash_preview", "")
                with st.expander(
                    f"[{tag}]  {entry['timestamp']}  ·  "
                    f"{entry['user_email']} ({entry['role']})  ·  "
                    f"`{entry['tool_called']}`"
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.write(f"**Decision:** {entry['result']}")
                    c2.write(f"**Tool:** `{entry['tool_called']}`")
                    if hash_preview:
                        c3.code(f"#{hash_preview}…", language=None)
                    if entry.get("question"):
                        st.write(f"**Question:** {entry['question']}")
                    if entry.get("deny_reason"):
                        st.error(f"Policy: {entry['deny_reason']}")

            st.divider()
            st.markdown("#### Full log")
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Export audit log (CSV)", csv,
                               "arp_audit_log.csv", "text/csv")

# ════════════════════════════════════════════════════════════════════════════════
# EXPANSION PANELS (Intelligence & operations workspace)
# ════════════════════════════════════════════════════════════════════════════════
elif INTEL_MODE and active_view in (
    "Briefing", "Email", "Research", "Factors", "Ideas",
    "Reporting", "Operations",
):
    import expansion_views as ev  # noqa: E402 — sibling module under frontend/

    if active_view == "Briefing":
        ev.render_briefing(API_URL, headers, me, chosen_model)
    elif active_view == "Email":
        ev.render_email_triage(API_URL, headers, chosen_model)
    elif active_view == "Research":
        ev.render_research(api, denied, API_URL, headers, chosen_model, me)
    elif active_view == "Factors":
        ev.render_factors(api, denied, me)
    elif active_view == "Ideas":
        ev.render_shadow_book(api, denied, API_URL, headers, me)
    elif active_view == "Reporting":
        ev.render_reporting(api, denied, API_URL, headers, me, chosen_model)
    elif active_view == "Operations":
        ev.render_operations(api, denied, API_URL, headers, me)

# ════════════════════════════════════════════════════════════════════════════════
# DIFC COMPLIANCE
# ════════════════════════════════════════════════════════════════════════════════
elif INTEL_MODE and active_view == "Compliance":
        st.subheader("DIFC Regulatory Compliance")
        st.caption(
            "This checklist maps platform features to DIFC Data Protection Law No. 5 of 2020 "
            "and DFSA Regulation 10 (Autonomous Systems, 2023). "
            "It is a design-time attestation — not a substitute for formal regulatory sign-off."
        )
        st.divider()

        # Live residency check from /health (cached)
        h = _fetch_health(API_URL)
        if h is None:
            residency_ok = llm_ok = False
            mkt_source = "unknown"
            primary = fallback = "unknown"
        else:
            residency_ok = h.get("data_residency") == "local"
            mkt_source   = h.get("market_data_source", "mock")
            primary      = h.get("llm_primary", "unknown")
            fallback     = h.get("llm_fallback", "unknown")
            llm_ok       = h.get("llm_online", False)

        mkt_egress = mkt_source not in ("mock", "unknown")

        # ── Live status banner ────────────────────────────────────────────────────
        # LLM inference and all client/position data stay local regardless of the
        # market-data source. A live price feed fetches only public quotes (no
        # client data leaves the machine).
        if residency_ok:
            market_note = (
                f"public quotes via `{mkt_source}`" if mkt_egress
                else "modelled (offline)"
            )
            st.success(
                f"Inference residency: LOCAL  ·  "
                f"Primary model: `{primary}`  ·  "
                f"Fallback: `{fallback}`  ·  "
                f"Market data: {market_note}"
            )
        else:
            st.error("Inference residency check failed — review local model configuration.")

        st.divider()

        # ── Compliance matrix ─────────────────────────────────────────────────────
        _mkt_detail = (
            f"The active market-data source is `{mkt_source}`, which retrieves only "
            "public price quotes — no personal, position, or trade data is transmitted. "
            if mkt_egress else
            "Market prices are modelled offline; no external network calls are made. "
        )
        CHECKS = [
            # (status, requirement, platform_feature, detail)
            (
                "PASS", "Local inference only",
                "DIFC DP Law Art. 24 — Cross-border transfer restriction",
                f"All LLM inference runs on-device via Ollama (`{primary}`). "
                "No personal data, position data, or trade data is transmitted to external APIs. "
                + _mkt_detail,
            ),
            (
                "PASS", "Immutable audit trail",
                "DFSA Reg. 10 — Accountability & traceability",
                "Every agent interaction is logged to an append-only audit_logs table: "
                "user identity, role, tool invoked, allow/deny decision, and UTC timestamp. "
                "The Audit Log tab provides a filterable view and one-click CSV export.",
            ),
            (
                "PASS", "Role-based access control",
                "DFSA GEN Rule 5.3.21 — Systems and controls",
                "Five roles (admin, analyst, risk, manager, intern) with a fixed permission matrix. "
                "Enforcement is at the tool function level — not just the API layer. "
                "Deny messages are policy-specific and name which role would grant access. "
                "Follows Bell-LaPadula no-read-up / no-write-down principle.",
            ),
            (
                "ACTION", "Agent orchestration (demo stage)",
                "DFSA Reg. 10 — Predictable autonomous behaviour",
                "Tool routing uses deterministic keyword scoring (`route_tools`) before the "
                "LLM runs — not LLM tool-calling or a multi-step planner. The model receives "
                "tool JSON in context and answers; it does not choose tools. "
                "This is intentional for demo reliability and auditability. Production would "
                "add an LLM planner or MCP tool registry behind the same RBAC-gated tools.",
            ),
            (
                "ACTION", "Database persistence (assessment)",
                "DFSA Operational Risk — Resilience & recoverability",
                "Book data persists in a single SQLite file (`ARP_PERSISTENCE=sqlite_demo`) "
                "on a seeded demonstration mandate — not production fund infrastructure. "
                "No HA failover, no safe multi-writer API replicas, and backup is manual "
                "(`make backup-db`) without automated point-in-time recovery. "
                "PostgreSQL is the named production target (`ARP_PERSISTENCE=postgresql`) "
                "for concurrent writers, managed backup/RPO, and multi-AZ resilience. "
                "Tool, RBAC, and audit layers are unchanged across the migration.",
            ),
            (
                "PASS", "Human-in-the-loop (HITL)",
                "DFSA Reg. 10 — Human oversight for autonomous systems",
                "No AI agent executes trades, sends investor communications, or modifies "
                "data autonomously. All agent outputs are advisory — human review and "
                "approval is required before any downstream action. "
                "Satisfies the DFSA two-person confirmation rule for trade execution.",
            ),
            (
                "PASS", "Input validation & prompt injection prevention",
                "DIFC DP Law Art. 11 — Data integrity",
                "User questions are length-capped and scrubbed for common injection phrases. "
                "RAG, memory, and research ingest pass through the same sanitizer. "
                "LLM prompts use delimiter-separated READ-ONLY context blocks; the model is "
                "instructed not to follow instructions embedded in context data. "
                "SQL queries use parameterised statements throughout.",
            ),
            (
                "PASS", "Segregation of duties",
                "DFSA GEN Rule 2.2.10 — Conflicts of interest",
                "Analyst and risk roles have non-overlapping data access. "
                "Trade data is not visible to analysts. Managers see aggregate risk counts only "
                "(no alert detail or remediation). Audit log access is restricted to "
                "risk and admin roles.",
            ),
            (
                "PASS", "Fallback & resilience",
                "DFSA Operational Risk — Business continuity",
                f"If the primary LLM (`{primary}`) fails, the system automatically retries "
                f"with the fallback model (`{fallback}`). "
                "Both run locally — no dependency on external API availability.",
            ),
            (
                "ACTION", "Automated Systems Officer (ASO)",
                "DFSA Reg. 10 Art. 8 — Designated AI governance officer",
                "DIFC Regulation 10 (2023) requires firms deploying high-risk autonomous systems "
                "to designate an Automated Systems Officer (analogous to a DPO for AI). "
                "This is an organisational designation — not a software feature. "
                "ARP must designate an ASO before moving this platform to production.",
            ),
            (
                "ACTION", "AI Impact Assessment (AIIA)",
                "DFSA Reg. 10 Art. 6 — Pre-deployment risk assessment",
                "A formal AI Impact Assessment is required for production deployment of "
                "systems making autonomous or semi-autonomous decisions. "
                "This prototype has not undergone a formal AIIA. "
                "Recommended before investor-facing or trade-execution features go live.",
            ),
            (
                "ACTION", "Data Processing Agreement (DPA)",
                "DIFC DP Law Art. 21 — Controller-processor obligations",
                "If any model inference or data processing is delegated to a third party "
                "(e.g. a managed GPU provider), a DPA is required. "
                "Current architecture avoids this by running inference locally. "
                "Any future cloud LLM integration requires a DPA and DIFC-approved jurisdiction.",
            ),
            (
                "GAP", "Production secrets management",
                "DFSA Operational Risk — Credential security",
                "Current authentication uses fixed demo bearer tokens (README tester table; SHA-256 digests in the database). "
                "Production deployment requires JWT with expiry + refresh, "
                "secrets stored in AWS Secrets Manager or HashiCorp Vault, "
                "and an audit log of credential issuance and revocation.",
            ),
            (
                "PASS", "Hash-chained audit log (tamper evidence)",
                "DFSA Reg. 10 — Tamper-evident recordkeeping",
                "Each audit record stores SHA-256(prev_hash || fields). "
                "Any deletion, reordering, or modification breaks the chain — "
                "detectable via the /audit/verify endpoint (visible in the Audit Log tab). "
                "Production hardening: export chain to S3 with Object Lock (WORM) "
                "and schedule periodic verify_chain() runs.",
            ),
        ]

        _STATUS_META = {
            "PASS":   ("pill-pos",  "Implemented"),
            "ACTION": ("pill-warn", "Organisational action required"),
            "GAP":    ("pill-neg",  "Production gap"),
        }
        for status, requirement, regulation, detail in CHECKS:
            css_class, label = _STATUS_META.get(status, ("pill-mute", status))
            with st.expander(f"[{status}]  {requirement} — {regulation}"):
                st.markdown(
                    pill(label, css_class) + f"  <span class='mc-sub'>{regulation}</span>",
                    unsafe_allow_html=True,
                )
                st.write(detail)

        st.divider()
        st.caption(
            "Legend:  PASS — implemented in this platform  ·  "
            "ACTION — requires organisational action (not a software gap)  ·  "
            "GAP — production gap, must be addressed before live deployment"
        )
        st.caption(
            "References: DIFC Data Protection Law No. 5 of 2020  ·  "
            "DFSA Regulation 10 on Autonomous Systems (2023)  ·  "
            "DFSA General Module (GEN) Rules  ·  "
            "Bell-LaPadula MAC model (NIST 800-53 AC-4)"
        )

        if me["role"] in ("risk", "admin"):
            st.divider()
            st.markdown("#### Compliance evidence export")
            fmt = st.radio("Format", ["markdown", "csv"], horizontal=True, key="compliance_fmt")
            if st.session_state.get("compliance_fmt_saved") != fmt:
                st.session_state.pop("compliance_rep", None)
            st.session_state["compliance_fmt_saved"] = fmt
            if st.button("Generate compliance pack", key="compliance_export"):
                rep = api("/report/compliance", {"format": fmt}, cache=False)
                if denied(rep):
                    st.warning(rep.get("error", "Access denied."))
                    st.session_state.pop("compliance_rep", None)
                else:
                    st.session_state["compliance_rep"] = rep
            rep = st.session_state.get("compliance_rep")
            if rep and not denied(rep):
                content = rep.get("content", "")
                if fmt == "markdown":
                    st.download_button(
                        "Download Markdown",
                        content,
                        file_name=rep.get("filename", "arp_compliance_pack.md"),
                        mime="text/markdown",
                        key="compliance_dl_md",
                    )
                    with st.expander("Preview"):
                        st.markdown(content[:8000])
                else:
                    st.download_button(
                        "Download CSV",
                        content,
                        file_name=rep.get("filename", "arp_compliance_pack.csv"),
                        mime="text/csv",
                        key="compliance_dl_csv",
                    )
