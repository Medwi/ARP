"""
Expansion dashboard panels — briefing, research, shadow book, reporting, etc.
Rendered via lazy navigation in frontend/app.py when ENABLE_ENHANCEMENTS=1.
"""

from __future__ import annotations

import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ACCENT = "#c8a86b"
POS = "#1f9d6b"
NEG = "#c0392b"
def _llm_request_params(chosen_model: str | None) -> dict:
    """Sidebar Switch model only — omit key when unset so API uses OLLAMA_MODEL."""
    return {"model": chosen_model} if chosen_model else {}


def _llm_request_body(chosen_model: str | None, **fields) -> dict:
    body = dict(fields)
    body.update(_llm_request_params(chosen_model))
    return body


def _parse_response(resp: requests.Response) -> dict:
    """Normalize API JSON; match frontend app.py _api_fetch behaviour."""
    try:
        if resp.status_code == 403:
            detail = resp.json().get("detail", "Access denied.")
            return {"error": detail, "allowed": False}
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            return {"error": detail, "allowed": False}
        return resp.json()
    except Exception as e:
        return {"error": str(e), "allowed": False}


def _access_denied(data: dict) -> bool:
    return data.get("allowed") is False


def _api_message(data: dict) -> str | None:
    """Validation or empty-state message when allowed but not successful."""
    if data.get("allowed") is True and data.get("error"):
        return data["error"]
    return None


def _budget_ticker_summary(items: list) -> str:
    """Format risk-budget overweight/underweight rows (list of dicts) for display."""
    if not items:
        return ""
    parts = []
    for row in items:
        if isinstance(row, dict):
            ticker = row.get("ticker", "?")
            actual = row.get("actual_pct", row.get("weight_pct"))
            ideal = row.get("ideal_pct")
            if actual is not None and ideal is not None:
                parts.append(f"{ticker} ({actual:.1f}% vs ideal {ideal:.1f}%)")
            elif actual is not None:
                parts.append(f"{ticker} ({actual:.1f}%)")
            else:
                parts.append(str(ticker))
        else:
            parts.append(str(row))
    return ", ".join(parts)


def _kyc_alert_line(alert) -> str:
    if isinstance(alert, dict):
        return (
            f"{alert.get('contact', '—')} ({alert.get('entity', '—')}): "
            f"{alert.get('kyc_status', '—')} — {alert.get('urgency', '')}"
        )
    return str(alert)


def _get_json(api_url: str, path: str, headers: dict, *, params: dict | None = None, timeout: int = 120) -> dict:
    try:
        resp = requests.get(f"{api_url}{path}", headers=headers, params=params, timeout=timeout)
        return _parse_response(resp)
    except Exception as e:
        return {"error": str(e), "allowed": False}


def _post_json(api_url: str, path: str, headers: dict, *, json_body: dict | None = None, timeout: int = 120) -> dict:
    try:
        resp = requests.post(f"{api_url}{path}", headers=headers, json=json_body or {}, timeout=timeout)
        return _parse_response(resp)
    except Exception as e:
        return {"error": str(e), "allowed": False}


def _invalidate_api_cache(path: str) -> None:
    """Drop cached GET responses for path (e.g. after a mutating POST)."""
    store = st.session_state.get("_api_cache")
    if not store:
        return
    for key in list(store):
        if key[0] == path:
            store.pop(key, None)


def _put_json(api_url: str, path: str, headers: dict, *, json_body: dict, timeout: int = 30) -> dict:
    try:
        resp = requests.put(f"{api_url}{path}", headers=headers, json=json_body, timeout=timeout)
        return _parse_response(resp)
    except Exception as e:
        return {"error": str(e), "allowed": False}


def _delete_json(api_url: str, path: str, headers: dict, timeout: int = 30) -> dict:
    try:
        resp = requests.delete(f"{api_url}{path}", headers=headers, timeout=timeout)
        return _parse_response(resp)
    except Exception as e:
        return {"error": str(e), "allowed": False}


def render_briefing(api_url: str, headers: dict, me: dict, chosen_model: str | None) -> None:
    st.subheader("Morning intelligence")
    st.caption(
        "Two complementary reports for the CIO — **external overnight** vs **internal operations**. "
        "Requires **risk** or **admin** role. Uses the sidebar **Switch model** selection."
    )

    if me["role"] not in ("risk", "admin"):
        st.info("Morning intelligence requires risk or admin role.")
        return

    col_brief, col_digest = st.columns(2)

    with col_brief:
        st.markdown("##### Overnight pre-market briefing")
        st.markdown(
            "<span style='font-size:0.75rem; letter-spacing:0.06em; color:#c8a86b;'>"
            "EXTERNAL · MARKETS &amp; INBOX</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "What changed overnight outside the firm: macro movers, market tape, and filtered "
            "CIO inbox (urgent LP/regulator items). Read first — about 90 seconds. "
            "Uses the **same sample inbox** as the **Email** tab but a different pipeline "
            "(external briefing narrative; priorities may differ from full LLM triage)."
        )

    with col_digest:
        st.markdown("##### CIO morning digest")
        st.markdown(
            "<span style='font-size:0.75rem; letter-spacing:0.06em; color:#c8a86b;'>"
            "INTERNAL · OPS &amp; COMPLIANCE</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Control tower inside the firm: P&L attribution, risk queue, shadow book, pipeline "
            "health, LP follow-ups, and DFSA deadlines. No market essay or inbox — read second."
        )

    col_btn_brief, col_btn_digest = st.columns(2)

    with col_btn_brief:
        if st.button("Generate briefing", type="primary", key="gen_brief", use_container_width=True):
            with st.spinner("Synthesising overnight briefing…"):
                b = _get_json(
                    api_url, "/briefing", headers,
                    params=_llm_request_params(chosen_model),
                    timeout=180,
                )
                if _access_denied(b):
                    st.session_state["brief_result"] = {"error": b.get("response", b.get("error", "Access denied."))}
                elif msg := _api_message(b):
                    st.session_state["brief_result"] = {"error": msg}
                else:
                    st.session_state["brief_result"] = {"ok": b}
                st.rerun()

    with col_btn_digest:
        if st.button("Generate CIO digest", type="primary", key="gen_cio_digest", use_container_width=True):
            with st.spinner("Building CIO digest…"):
                d = _get_json(
                    api_url, "/digest", headers,
                    params=_llm_request_params(chosen_model),
                    timeout=180,
                )
                if _access_denied(d):
                    st.session_state["digest_result"] = {"error": d.get("error", "Access denied.")}
                elif msg := _api_message(d):
                    st.session_state["digest_result"] = {"error": msg}
                else:
                    st.session_state["digest_result"] = {"ok": d}
                st.rerun()

    st.divider()

    brief_out = st.session_state.get("brief_result")
    if brief_out:
        st.markdown("#### Overnight briefing output")
        if brief_out.get("error"):
            st.warning(brief_out["error"])
        else:
            b = brief_out["ok"]
            st.success(f"Generated at {b.get('generated_at', '—')} · external overnight view")
            st.markdown(b.get("briefing", b.get("response", "")))
            with st.expander("Briefing data (markets + inbox)"):
                st.json(b.get("data", {}))

    digest_out = st.session_state.get("digest_result")
    if digest_out:
        st.markdown("#### CIO digest output")
        if digest_out.get("error"):
            st.warning(digest_out["error"])
        else:
            d = digest_out["ok"]
            freshness = d.get("data_freshness", "—")
            st.success(
                f"Generated at {d.get('generated_at', '—')} · "
                f"freshness: {freshness} · internal operations view"
            )
            if str(freshness).startswith("DEMO"):
                st.caption(
                    "Demo assessment book — pipeline status reflects sync heartbeat only, "
                    "not a live Broadridge feed. Simulate sync does not reload trades or prices."
                )
            st.markdown(d.get("digest", ""))
            with st.expander("Digest data sources (ops + compliance)"):
                st.write(d.get("data_sources", []))
                st.json(d.get("raw_data", {}))


def render_email_triage(
    api_url: str,
    headers: dict,
    chosen_model: str | None = None,
) -> None:
    st.subheader("CIO Email Triage")
    st.caption(
        "Priority-sorted inbox digest from a sample institutional inbox. "
        "Requires **analyst**, **risk**, or **admin** role. "
        "Uses the sidebar **Switch model** selection. "
        "Same sample inbox as **Briefing** — full LLM JSON triage here; "
        "summaries and priorities may differ from the overnight briefing view."
    )

    if st.button("Run demo inbox triage", type="primary", key="email_demo"):
        with st.spinner("Triaging sample emails…"):
            et = _get_json(
                api_url, "/email-triage/demo", headers,
                params=_llm_request_params(chosen_model),
                timeout=180,
            )
            if _access_denied(et):
                st.warning(et.get("error", "Access denied."))
            elif msg := _api_message(et):
                st.warning(msg)
            else:
                stats = et.get("stats", {})
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Total", et.get("total", 0))
                c2.metric("Urgent", stats.get("URGENT", 0))
                c3.metric("High", stats.get("HIGH", 0))
                c4.metric("Normal", stats.get("NORMAL", 0))
                c5.metric("Noise filtered", stats.get("noise_filtered", 0))
                if et.get("digest_summary"):
                    st.info(f"**Digest:** {et['digest_summary']}")
                shown = 0
                for email in et.get("triaged", []):
                    if email.get("priority") == "NOISE":
                        continue
                    shown += 1
                    with st.expander(
                        f"[{email.get('priority', 'NORMAL')}] {email.get('id', '')} — "
                        f"{email.get('summary', 'No summary')}"
                    ):
                        c_a, c_b = st.columns(2)
                        c_a.write(f"**Sender type:** {email.get('sender_type', '—')}")
                        c_b.write(
                            f"**Action required:** "
                            f"{'Yes' if email.get('action_required') else 'No'}"
                        )
                        if email.get("action_items"):
                            st.write("**Action items:**")
                            for item in email["action_items"]:
                                st.write(f"• {item}")
                        if email.get("deadline"):
                            st.caption(f"Deadline: {email['deadline']}")
                        if email.get("reply_suggested"):
                            st.caption(f"Suggested reply: {email['reply_suggested']}")
                if shown == 0:
                    st.info("No non-noise emails in this batch.")
                if et.get("generated_at"):
                    st.caption(f"Generated at {et['generated_at']}")
                if et.get("hitl_note"):
                    st.caption(et["hitl_note"])


def _research_entry_label(entry: dict) -> str:
    label = f"[{entry.get('entry_type', '?')}] {entry.get('title', 'Untitled')}"
    if entry.get("ticker"):
        label += f" — {entry['ticker']}"
    if entry.get("analyst"):
        label += f" ({entry['analyst']})"
    return label


def render_research(
    api_fn,
    denied_fn,
    api_url: str,
    headers: dict,
    chosen_model: str | None = None,
    me: dict | None = None,
) -> None:
    st.subheader("Research Lake")
    st.info(
        "**Research samples are demo narratives** — seeded `RISK_REPORT` entries include VaR and "
        "stress figures as **static text**, not output from a live risk engine. "
        "See `docs/RISK_MODELS.md`."
    )
    st.caption(
        "Full-text search over meeting notes, research, and investment decisions. "
        "Available to **analyst**, **risk**, and **admin** roles."
    )

    role = (me or {}).get("role", "")
    if role in ("risk", "admin"):
        if st.button("Load sample research", key="seed_research"):
            seeded = _post_json(api_url, "/research/seed-samples", headers, timeout=15)
            if _access_denied(seeded):
                st.warning(seeded.get("error", "Access denied."))
            elif msg := _api_message(seeded):
                st.warning(msg)
            elif seeded.get("seeded", 0) > 0:
                st.success(seeded.get("message", "Sample research loaded."))
                st.rerun()
            else:
                st.info(seeded.get("message", "Research lake already has entries."))
    else:
        st.caption("Loading sample research requires **risk** or **admin** role.")

    stats_r = api_fn("/research/stats")
    if denied_fn(stats_r):
        st.warning(stats_r.get("error", "Access denied."))
    elif stats_r.get("total_entries", 0) > 0:
        st.metric("Total entries", stats_r["total_entries"])
    elif stats_r.get("message"):
        st.info(stats_r["message"])

    st.divider()
    st.markdown("#### Browse catalog")
    type_options = ["All"]
    if stats_r.get("by_type"):
        type_options.extend(sorted(stats_r["by_type"].keys()))
    else:
        type_options.extend(
            ["RESEARCH", "MEETING_NOTE", "INVESTMENT_DECISION", "EARNINGS_CALL",
             "PODCAST_TRANSCRIPT", "RISK_REPORT"]
        )
    col_bt, col_bk = st.columns(2)
    browse_type = col_bt.selectbox("Entry type", type_options, key="research_browse_type")
    browse_ticker = (col_bk.text_input("Ticker filter", key="research_browse_ticker") or "").upper()

    browse_params: dict = {"limit": 50}
    if browse_type != "All":
        browse_params["entry_type"] = browse_type
    if browse_ticker:
        browse_params["ticker"] = browse_ticker

    catalog = api_fn("/research/entries", browse_params)
    if denied_fn(catalog):
        st.warning(catalog.get("error", "Access denied."))
    elif catalog.get("count", 0) == 0:
        st.info("No entries match these filters.")
    else:
        entries = catalog.get("entries", [])

        selected_id = st.selectbox(
            "Select entry",
            options=[e["id"] for e in entries],
            format_func=lambda eid: _research_entry_label(
                next(e for e in entries if e["id"] == eid)
            ),
            key="research_catalog_select",
        )
        if selected_id:
            detail = _get_json(api_url, f"/research/entries/{selected_id}", headers)
            if _access_denied(detail):
                st.warning(detail.get("error", "Access denied."))
            elif msg := _api_message(detail):
                st.warning(msg)
            elif entry := detail.get("entry"):
                meta = []
                if entry.get("ticker"):
                    meta.append(f"**Ticker:** `{entry['ticker']}`")
                if entry.get("analyst"):
                    meta.append(f"**Analyst:** {entry['analyst']}")
                if entry.get("source"):
                    meta.append(f"**Source:** {entry['source']}")
                if entry.get("conviction"):
                    meta.append(f"**Conviction:** {entry['conviction']}/5")
                if entry.get("authored_at") or entry.get("created_at"):
                    meta.append(
                        f"**Date:** {entry.get('authored_at') or entry.get('created_at')}"
                    )
                if meta:
                    st.markdown("  ·  ".join(meta))
                st.markdown(entry.get("content", ""))
                if entry.get("tags"):
                    st.caption("Tags: " + ", ".join(entry["tags"]))

    st.divider()
    st.markdown("#### Search")
    q = st.text_input("Search", placeholder="e.g. NVDA margin expansion", key="research_q")
    col_sf, col_st, col_sl = st.columns(3)
    search_type = col_sf.selectbox(
        "Type",
        type_options,
        key="research_search_type",
    )
    search_ticker = (col_st.text_input("Ticker", key="research_search_ticker") or "").upper() or None
    search_limit = col_sl.slider("Max results", 3, 20, 8, key="research_search_limit")

    if q:
        search_params: dict = {"q": q, "limit": search_limit}
        if search_type != "All":
            search_params["entry_type"] = search_type
        if search_ticker:
            search_params["ticker"] = search_ticker
        results_r = api_fn("/research/search", search_params)
        if denied_fn(results_r):
            st.warning(results_r.get("error", "Access denied."))
        elif results_r.get("count", 0) == 0:
            st.info("No results — try another query or browse the catalog above.")
        else:
            st.caption(f"{results_r['count']} result(s) for “{q}”")
            for r in results_r.get("results", []):
                with st.expander(
                    f"[{r['entry_type']}] {r['title']}"
                    + (f" — {r['ticker']}" if r.get("ticker") else "")
                ):
                    st.caption(
                        f"Analyst: {r.get('analyst', '—')}  ·  "
                        f"Source: {r.get('source', '—')}  ·  "
                        f"Conviction: {r.get('conviction') or '—'}/5"
                    )
                    st.write((r.get("snippet") or "") + "…")

    st.divider()
    st.markdown("#### Ask the research assistant")
    st.caption(
        "Select one or more entries, then ask a question. Grounding is **entry text only** "
        "(not the same stack as **Book & markets → AI Agents**, which uses live DB + RAG + "
        "knowledge graph). Local LLM; uses the sidebar **Switch model** selection."
    )

    qa_catalog = api_fn("/research/entries", {"limit": 100})
    if denied_fn(qa_catalog):
        st.warning(qa_catalog.get("error", "Access denied."))
    elif qa_catalog.get("count", 0) == 0:
        st.info("Load sample research above to enable Q&A.")
    else:
        qa_entries = qa_catalog.get("entries", [])
        qa_ids = [e["id"] for e in qa_entries]
        default_ids: list[int] = []
        catalog_sel = st.session_state.get("research_catalog_select")
        if catalog_sel in qa_ids:
            default_ids = [catalog_sel]

        selected_for_qa = st.multiselect(
            "Selected research entries (max 5)",
            options=qa_ids,
            default=default_ids,
            format_func=lambda eid: _research_entry_label(
                next(e for e in qa_entries if e["id"] == eid)
            ),
            key="research_qa_entries",
        )

        example_questions = [
            "What are the key investment risks mentioned?",
            "Summarize the investment thesis in three bullet points.",
            "What decisions were made and who approved them?",
        ]
        ex_cols = st.columns(len(example_questions))
        for i, prompt in enumerate(example_questions):
            if ex_cols[i].button(prompt, key=f"research_qa_prompt_{i}", use_container_width=True):
                st.session_state["research_qa_input"] = prompt
                st.rerun()

        question = st.text_area(
            "Your question",
            key="research_qa_input",
            height=100,
            placeholder="e.g. How does this research affect our NVDA position?",
        )

        if st.button("Ask research assistant", type="primary", key="research_qa_ask"):
            if not selected_for_qa:
                st.warning("Select at least one research entry.")
            elif not (question or "").strip():
                st.warning("Enter a question.")
            else:
                with st.spinner("Consulting selected research…"):
                    resp = _post_json(
                        api_url,
                        "/research/ask",
                        headers,
                        json_body=_llm_request_body(
                            chosen_model,
                            question=question.strip(),
                            entry_ids=selected_for_qa[:5],
                        ),
                        timeout=180,
                    )
                if _access_denied(resp):
                    st.warning(resp.get("error", "Access denied."))
                elif msg := _api_message(resp):
                    st.warning(msg)
                elif resp.get("answer"):
                    if resp.get("sources"):
                        src = ", ".join(
                            f"[{s.get('entry_type', '?')}] {s.get('title', 'Untitled')}"
                            for s in resp["sources"]
                        )
                        st.caption(f"Grounded in: {src}")
                    st.markdown(resp["answer"])
                else:
                    st.warning("No answer returned — check that Ollama is running.")


def render_factors(api_fn, denied_fn, me: dict) -> None:
    st.subheader("Factor Analysis & Stress Testing")
    st.info(
        "**Illustrative risk analytics** — factor tilts and stress P&L use heuristic mappings "
        "on the demo book, not a production factor model or historical simulation. "
        "`docs/RISK_MODELS.md`"
    )
    st.caption(
        "Sector weights from snapshot holdings; stress scenarios apply fixed shocks to weights — "
        "not a 252-day Broadridge returns window."
    )
    col_sec, col_fac = st.columns(2)

    with col_sec:
        st.markdown("#### Sector exposure")
        sectors_data = api_fn("/portfolio/sectors")
        if denied_fn(sectors_data):
            st.warning(sectors_data.get("error", "Access denied."))
        elif msg := _api_message(sectors_data):
            st.info(msg)
        else:
            hhi = sectors_data.get("hhi", 0)
            st.metric("HHI concentration", hhi)
            if sectors_data.get("hhi_interpretation"):
                st.caption(sectors_data["hhi_interpretation"])
            sw = sectors_data.get("sector_weights", [])
            if sw:
                df_sw = pd.DataFrame(sw)
                fig_s = px.pie(df_sw, values="weight_pct", names="sector", hole=0.4)
                fig_s.update_layout(height=280, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_s, use_container_width=True)

    with col_fac:
        st.markdown("#### Factor exposures")
        factors_data = api_fn("/portfolio/factors")
        if denied_fn(factors_data):
            st.warning(factors_data.get("error", "Access denied."))
        else:
            for factor, score in factors_data.get("portfolio_factors", {}).items():
                st.metric(factor.replace("_", " ").title(), f"{score:.2f}")

    st.divider()
    st.markdown("#### Stress scenarios")
    if me["role"] in ("risk", "admin"):
        stress_data = api_fn("/risk/stress-tests")
        if not denied_fn(stress_data):
            scenarios = stress_data.get("scenarios", [])
            if scenarios:
                df_stress = pd.DataFrame(scenarios)
                fig_st = go.Figure(go.Bar(
                    x=df_stress["scenario"],
                    y=df_stress["total_pnl"],
                    marker_color=[NEG if p < 0 else POS for p in df_stress["total_pnl"]],
                ))
                fig_st.update_layout(height=300, margin=dict(t=30, b=10))
                st.plotly_chart(fig_st, use_container_width=True)
    else:
        st.warning("Stress tests require risk role.")


def render_shadow_book(api_fn, denied_fn, api_url: str, headers: dict, me: dict) -> None:
    st.subheader("Trade Ideas Notebook")
    st.caption("Capture and track trade ideas independently of execution.")
    col_l, col_r = st.columns([1, 1])

    with col_l:
        st.markdown("#### New idea")
        with st.form("shadow_form"):
            sb_ticker = st.text_input("Ticker", value="NVDA").upper()
            sb_direction = st.selectbox("Direction", ["BUY", "SELL", "SHORT"])
            sb_conviction = st.slider("Conviction", 1, 5, 3)
            sb_thesis = st.text_area("Thesis", height=80)
            submitted = st.form_submit_button("Submit idea", type="primary")
        if submitted:
            if not sb_thesis.strip():
                st.warning("Thesis cannot be empty.")
            else:
                resp = _post_json(
                    api_url,
                    "/shadow-book/ideas",
                    headers,
                    json_body={
                        "ticker": sb_ticker,
                        "direction": sb_direction,
                        "thesis": sb_thesis,
                        "conviction": sb_conviction,
                    },
                    timeout=15,
                )
                if _access_denied(resp):
                    st.warning(resp.get("error", "Submission failed."))
                elif msg := _api_message(resp):
                    st.warning(msg)
                else:
                    st.success(f"Idea #{resp.get('idea_id')} added to the notebook.")
                    st.session_state.pop("editing_idea_id", None)
                    st.rerun()

    with col_r:
        st.markdown("#### Open ideas")
        ideas_data = api_fn("/shadow-book/ideas", {"status": "OPEN"}, cache=False)
        if denied_fn(ideas_data):
            st.warning(ideas_data.get("error", "Access denied."))
        elif not ideas_data.get("ideas"):
            st.info("No open ideas yet — submit one on the left.")
        else:
            editing_id = st.session_state.get("editing_idea_id")
            for idea in ideas_data.get("ideas", []):
                idea_id = idea["id"]
                label = (
                    f"{idea['ticker']} {idea['direction']} · "
                    f"conviction {idea['conviction']}/5 · {idea['submitted_by']}"
                )
                with st.expander(label, expanded=(editing_id == idea_id)):
                    if editing_id == idea_id:
                        with st.form(f"edit_idea_{idea_id}"):
                            ed_ticker = st.text_input(
                                "Ticker", value=idea["ticker"], key=f"ed_tk_{idea_id}",
                            ).upper()
                            ed_direction = st.selectbox(
                                "Direction",
                                ["BUY", "SELL", "SHORT"],
                                index=["BUY", "SELL", "SHORT"].index(idea["direction"]),
                                key=f"ed_dir_{idea_id}",
                            )
                            ed_conviction = st.slider(
                                "Conviction", 1, 5, int(idea["conviction"]),
                                key=f"ed_conv_{idea_id}",
                            )
                            ed_thesis = st.text_area(
                                "Thesis", value=idea.get("thesis", ""),
                                height=80, key=f"ed_th_{idea_id}",
                            )
                            save_col, cancel_col = st.columns(2)
                            save = save_col.form_submit_button("Save", type="primary")
                            cancel = cancel_col.form_submit_button("Cancel")
                        if save:
                            if not ed_thesis.strip():
                                st.warning("Thesis cannot be empty.")
                            else:
                                upd = _put_json(
                                    api_url,
                                    f"/shadow-book/ideas/{idea_id}",
                                    headers,
                                    json_body={
                                        "ticker": ed_ticker,
                                        "direction": ed_direction,
                                        "thesis": ed_thesis,
                                        "conviction": ed_conviction,
                                    },
                                )
                                if _access_denied(upd):
                                    st.warning(upd.get("error", "Update failed."))
                                elif msg := _api_message(upd):
                                    st.warning(msg)
                                else:
                                    st.session_state.pop("editing_idea_id", None)
                                    st.success(upd.get("message", "Idea updated."))
                                    st.rerun()
                        if cancel:
                            st.session_state.pop("editing_idea_id", None)
                            st.rerun()
                    else:
                        st.write(idea.get("thesis", ""))
                        if idea.get("target_price"):
                            st.caption(f"Target: ${idea['target_price']:,.2f}")
                        if idea.get("stop_loss"):
                            st.caption(f"Stop: ${idea['stop_loss']:,.2f}")
                        btn_edit, btn_remove = st.columns(2)
                        if btn_edit.button("Edit", key=f"edit_btn_{idea_id}"):
                            st.session_state.editing_idea_id = idea_id
                            st.rerun()
                        if btn_remove.button("Remove", key=f"remove_btn_{idea_id}"):
                            rem = _delete_json(
                                api_url, f"/shadow-book/ideas/{idea_id}", headers,
                            )
                            if _access_denied(rem):
                                st.warning(rem.get("error", "Remove failed."))
                            elif msg := _api_message(rem):
                                st.warning(msg)
                            elif not rem.get("deleted"):
                                st.warning(rem.get("message", "Could not remove idea."))
                            else:
                                st.session_state.pop("editing_idea_id", None)
                                st.success(rem.get("message", "Idea removed."))
                                st.rerun()

    if me["role"] in ("risk", "admin"):
        st.divider()
        report_data = api_fn("/shadow-book/report")
        if not denied_fn(report_data):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total ideas", report_data.get("total_ideas", 0))
            c2.metric("Hit rate", f"{report_data.get('hit_rate_pct', 0):.1f}%")
            c3.metric("Conv. hit rate", f"{report_data.get('conviction_hit_rate_pct', 0):.1f}%")
            c4.metric("Total P&L", f"${report_data.get('total_pnl', 0):,.0f}")


def _render_position_sizing(api_fn, denied_fn, api_url: str, headers: dict) -> None:
    """Kelly, vol sizing, and risk-budget tools — shared by Operations only."""
    sub_kelly, sub_vol, sub_budget = st.tabs(["Kelly", "Volatility", "Risk budget"])

    with sub_kelly:
        c1, c2, c3 = st.columns(3)
        win_rate = c1.number_input("Win rate", 0.0, 1.0, 0.55, 0.01, key="kelly_wr")
        avg_win = c2.number_input("Avg win (decimal)", 0.0, 1.0, 0.15, 0.01, key="kelly_aw")
        avg_loss = c3.number_input("Avg loss (decimal)", 0.0, 1.0, 0.07, 0.01, key="kelly_al")
        if st.button("Calculate Kelly", key="kelly_btn"):
            resp = _post_json(
                api_url,
                "/trade/size/kelly",
                headers,
                json_body={"win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss},
                timeout=15,
            )
            if _access_denied(resp):
                st.warning(resp.get("error", "Access denied."))
            elif msg := _api_message(resp):
                st.warning(msg)
            else:
                m1, m2 = st.columns(2)
                m1.metric("Recommended %", f"{resp.get('recommended_pct', 0)}%")
                m2.metric("Position USD", f"${resp.get('position_usd', 0):,.0f}")
                st.caption(resp.get("interpretation", ""))
                if resp.get("hitl_note"):
                    st.info(resp["hitl_note"])

    with sub_vol:
        ticker = st.text_input("Ticker", value="AAPL", key="vol_ticker").upper()
        annual_vol = st.number_input("Annual vol (decimal)", 0.01, 2.0, 0.25, 0.01, key="vol_ann")
        risk_budget = st.number_input("Risk budget % of AUM", 0.1, 5.0, 1.0, 0.1, key="vol_budget")
        if st.button("Calculate vol size", key="vol_btn"):
            resp = _post_json(
                api_url,
                "/trade/size/volatility",
                headers,
                json_body={
                    "ticker": ticker,
                    "annual_vol": annual_vol,
                    "risk_budget_pct": risk_budget,
                },
                timeout=15,
            )
            if _access_denied(resp):
                st.warning(resp.get("error", "Access denied."))
            elif msg := _api_message(resp):
                st.warning(msg)
            else:
                v1, v2, v3 = st.columns(3)
                v1.metric("Recommended %", f"{resp.get('recommended_pct', 0)}%")
                v2.metric("VaR 95% (USD)", f"${resp.get('var_95_usd', 0):,.0f}")
                v3.metric("Current weight", f"{resp.get('current_weight_pct', 0)}%")
                st.caption(resp.get("interpretation", ""))

    with sub_budget:
        budget = api_fn("/portfolio/risk-budget")
        if denied_fn(budget):
            st.warning(budget.get("error", "Access denied."))
        else:
            c1, c2 = st.columns(2)
            c1.metric("Positions", budget.get("total_positions", 0))
            c2.metric("Risk used", f"{budget.get('total_risk_used', 0):.2f}%")
            ow = budget.get("overweight") or []
            uw = budget.get("underweight") or []
            if ow:
                st.warning(f"Overweight: {_budget_ticker_summary(ow)}")
            if uw:
                st.info(f"Underweight: {_budget_ticker_summary(uw)}")
            if budget.get("note"):
                st.caption(budget["note"])
            if budget.get("positions"):
                st.dataframe(pd.DataFrame(budget["positions"]), hide_index=True)


def render_reporting(api_fn, denied_fn, api_url: str, headers: dict, me: dict, chosen_model: str | None) -> None:
    st.subheader("Reporting")
    st.caption(
        "Investor-facing and fund-admin outputs: performance attribution, "
        "client letters, and manager / regulatory reporting."
    )
    tab_attr, tab_letter, tab_mgr = st.tabs(
        ["P&L Attribution", "Investor Letter", "Manager Accounts"]
    )

    with tab_attr:
        st.markdown("#### Performance attribution")
        st.caption(
            "Book-level P&L breakdown and top contributors. "
            "Demo uses snapshot cost basis — see attribution note below."
        )
        attr = api_fn("/portfolio/attribution")
        if denied_fn(attr):
            st.warning(attr.get("error", "Access denied."))
        elif msg := _api_message(attr):
            st.info(msg)
        else:
            if attr.get("data_scope") == "demo_snapshot":
                st.caption(f"ℹ️ {attr.get('attribution_note', '')}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Total P&L", f"${attr.get('total_pnl', 0):,.0f}")
            c2.metric("P&L %", f"{attr.get('total_pnl_pct', 0):+.2f}%")
            c3.metric("AUM", f"${attr.get('total_aum', 0):,.0f}")
            st.caption(attr.get("narrative", ""))
            if attr.get("best_contributors"):
                st.markdown("**Top contributors**")
                st.dataframe(pd.DataFrame(attr["best_contributors"]), hide_index=True)

    with tab_letter:
        st.markdown("#### Investor letter draft")
        st.caption(
            "LLM-assisted client communication — requires human review before send. "
            "Uses the sidebar **Switch model** selection. "
            "Performance numbers come from the **demo snapshot book**, not live fund NAV "
            "or audited admin data."
        )
        if me["role"] not in ("risk", "admin"):
            st.warning("Investor letters require risk or admin role.")
        else:
            period = st.selectbox("Period", ["monthly", "quarterly"], key="letter_period")
            tier = st.selectbox(
                "Tier",
                ["INSTITUTIONAL", "HNW", "FAMILY_OFFICE", "SOVEREIGN"],
                key="letter_tier",
            )
            investor = st.text_input("Investor name", value="Valued Investor", key="letter_name")
            if st.button("Generate letter draft", key="gen_letter"):
                with st.spinner("Drafting letter…"):
                    resp = _post_json(
                        api_url,
                        "/letters/generate",
                        headers,
                        json_body=_llm_request_body(
                            chosen_model,
                            period=period,
                            tier=tier,
                            investor_name=investor,
                        ),
                        timeout=180,
                    )
                    if _access_denied(resp):
                        st.warning(resp.get("error", "Access denied."))
                    elif msg := _api_message(resp):
                        st.warning(msg)
                    else:
                        if resp.get("data_scope") == "demo_snapshot":
                            note = resp.get("data_scope_note") or (
                                "Demo snapshot book — not live fund NAV."
                            )
                            st.caption(f"ℹ️ {note}")
                        st.markdown(resp.get("full_letter", ""))
                        if resp.get("compliance_gate"):
                            st.warning(resp["compliance_gate"])

    with tab_mgr:
        st.markdown("#### Manager & regulatory accounts")
        st.caption("Fund admin metrics, revenue, and upcoming filing deadlines.")
        mgr = api_fn("/reporting/manager-accounts")
        if denied_fn(mgr):
            st.warning(mgr.get("error", "Access denied."))
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("AUM", f"${mgr.get('aum', 0):,.0f}")
            c2.metric("Monthly revenue", f"${mgr.get('total_revenue_monthly', 0):,.0f}")
            c3.metric(
                "EBCR check (demo)",
                "Pass" if mgr.get("all_tests_passed") else "Fail",
            )
            if mgr.get("synthetic"):
                st.info("Synthetic regulatory metrics — illustrative DFSA tests and filing calendar.")
            if mgr.get("data_scope_note"):
                st.caption(mgr["data_scope_note"])
            if mgr.get("data_note"):
                st.caption(mgr["data_note"])
            if mgr.get("sign_off_note"):
                st.caption(mgr["sign_off_note"])
            if mgr.get("upcoming_filings"):
                st.dataframe(pd.DataFrame(mgr["upcoming_filings"]), hide_index=True)


def render_operations(api_fn, denied_fn, api_url: str, headers: dict, me: dict) -> None:
    st.subheader("Operations")
    st.caption(
        "Day-to-day platform operations: market-data ingestion, investor CRM, "
        "trade expression tooling, position sizing, and research-supplier analytics."
    )
    tab_data, tab_crm, tab_opt, tab_size, tab_analyst = st.tabs(
        [
            "Data Pipeline",
            "Investor CRM",
            "Trade Optimizer",
            "Position Sizing",
            "Analyst Rankings",
        ]
    )

    with tab_data:
        st.markdown("#### Broadridge data pipeline")
        if msg := st.session_state.pop("pipeline_sync_msg", None):
            st.success(msg)
        pipe = api_fn("/pipeline/status", cache=False)
        if denied_fn(pipe):
            st.warning(pipe.get("error", "Access denied."))
        else:
            interval = pipe.get("sync_interval_target", 15)
            st.caption(
                f"Monitors Broadridge-style trade/price ingestion (demo: mock sync runs). "
                f"Not investor CRM. Target interval: every {interval} minutes."
            )
            health = pipe.get("health", "—")
            st.metric("Pipeline health", health)
            if pipe.get("book_freshness"):
                st.caption(f"Book freshness: {pipe['book_freshness']}")
            if pipe.get("data_freshness_note"):
                st.caption(pipe["data_freshness_note"])
            st.caption(
                f"Last sync: {pipe.get('minutes_since_sync', '—')} min ago · "
                f"Trades: {pipe.get('trade_records', 0)} · "
                f"Prices: {pipe.get('price_records', 0)}"
            )
            if pipe.get("recent_runs"):
                with st.expander("Recent pipeline runs", expanded=health != "HEALTHY"):
                    st.dataframe(
                        pd.DataFrame(pipe["recent_runs"]),
                        use_container_width=True,
                        hide_index=True,
                    )
            if health == "STALE":
                st.info(
                    "STALE means no successful sync within twice the target interval "
                    f"({pipe.get('sync_interval_target', 15)} min). "
                    "Run **Simulate sync** to record a fresh ingestion run."
                )
            elif health == "NOT_CONFIGURED":
                st.info("No pipeline runs yet — use **Simulate sync** to record the first run.")
            if me["role"] in ("risk", "admin"):
                if st.button("Simulate sync", type="primary", key="sim_sync"):
                    with st.spinner("Recording ingestion run…"):
                        r = _post_json(api_url, "/pipeline/simulate-sync", headers, timeout=30)
                    if _access_denied(r):
                        st.warning(r.get("error", "Access denied."))
                    elif r.get("error"):
                        st.error(r.get("error"))
                    elif r.get("allowed") is True or r.get("status") == "SUCCESS":
                        st.session_state["pipeline_sync_msg"] = r.get(
                            "message",
                            "Sync simulated — SUCCESS row added to pipeline_runs "
                            "(demo only; does not reload trades/prices).",
                        )
                        _invalidate_api_cache("/pipeline/status")
                        st.rerun()
                    else:
                        st.error("Simulate sync failed — unexpected API response.")
            else:
                st.caption("Simulate sync requires **risk** or **admin** role.")

    with tab_crm:
        st.markdown("#### Investor CRM")
        st.caption("LP pipeline, KYC status, and follow-up actions.")
        if me["role"] not in ("risk", "admin"):
            st.info("Investor CRM requires risk or admin role.")
        else:
            if st.button("Seed sample investors", key="crm_seed"):
                seeded = _post_json(api_url, "/crm/seed-samples", headers, timeout=15)
                if _access_denied(seeded):
                    st.warning(seeded.get("error", "Access denied."))
                else:
                    st.success(seeded.get("message", "CRM seeded."))
                    st.rerun()
            pipe = api_fn("/crm/pipeline")
            if denied_fn(pipe):
                st.warning(pipe.get("error", "Access denied."))
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Contacts", pipe.get("contact_count", 0))
                c2.metric("AUM committed", f"${pipe.get('total_aum_committed', 0):,.0f}")
                c3.metric("Overdue follow-ups", pipe.get("overdue_count", 0))
                if pipe.get("contacts"):
                    st.dataframe(pd.DataFrame(pipe["contacts"]), hide_index=True)
                if pipe.get("kyc_alerts"):
                    st.markdown("**KYC alerts**")
                    for alert in pipe["kyc_alerts"][:5]:
                        st.write(f"• {_kyc_alert_line(alert)}")
                overdue = pipe.get("overdue_followups") or []
                if overdue:
                    st.markdown("**Overdue follow-ups**")
                    for contact in overdue[:5]:
                        if isinstance(contact, dict):
                            st.write(
                                f"• {contact.get('name', '—')}: "
                                f"{contact.get('next_action', '—')} "
                                f"(due {contact.get('next_action_due', '—')})"
                            )
                        else:
                            st.write(f"• {contact}")

    with tab_opt:
        st.markdown("#### Trade expression optimizer")
        st.caption("Instrument and structure recommendations for a given thesis and notional.")
        if me["role"] not in ("risk", "admin"):
            st.info("Trade optimizer requires risk or admin role.")
        else:
            with st.form("trade_opt_form"):
                o_ticker = st.text_input("Ticker", value="NVDA").upper()
                o_dir = st.selectbox("Direction", ["LONG", "SHORT"])
                o_conv = st.slider("Conviction", 1, 5, 4)
                o_notional = st.number_input("Notional (USD)", 10000, 5000000, 500000, 10000)
                o_horizon = st.selectbox("Horizon", ["short", "medium", "long"])
                submitted = st.form_submit_button("Optimize expression", type="primary")
            if submitted:
                resp = requests.post(
                    f"{api_url}/trade/optimize",
                    headers=headers,
                    params={
                        "ticker": o_ticker,
                        "direction": o_dir,
                        "conviction": o_conv,
                        "notional": o_notional,
                        "time_horizon": o_horizon,
                    },
                    timeout=30,
                )
                data = _parse_response(resp)
                if _access_denied(data):
                    st.warning(data.get("error", "Access denied."))
                elif msg := _api_message(data):
                    st.warning(msg)
                else:
                    st.success(f"Top: **{data.get('top_recommendation', '—')}**")
                    st.caption(data.get("rationale", ""))
                    if data.get("recommendations"):
                        st.dataframe(pd.DataFrame(data["recommendations"]), hide_index=True)
            scores = api_fn("/trade/score-positions")
            if not denied_fn(scores) and scores.get("scored"):
                st.divider()
                st.markdown("**Position expression scores**")
                st.dataframe(pd.DataFrame(scores["scored"]), hide_index=True)

    with tab_size:
        st.markdown("#### Position sizing")
        st.caption("Pre-trade sizing calculators and portfolio risk-budget utilisation.")
        st.warning(
            "**VaR 95%** here is a simplified parametric estimate for sizing maths — "
            "not fund regulatory VaR or a certified risk system (`docs/RISK_MODELS.md`)."
        )
        _render_position_sizing(api_fn, denied_fn, api_url, headers)

    with tab_analyst:
        st.markdown("#### Research supplier rankings")
        st.caption(
            "Analyst / broker hit rates for commission and coverage decisions. "
            "Rankings from seeded demo data — not live broker coverage analytics."
        )
        if me["role"] in ("risk", "admin"):
            if st.button("Seed sample analysts", key="analyst_seed"):
                seeded = _post_json(api_url, "/analysts/seed-samples", headers, timeout=15)
                if _access_denied(seeded):
                    st.warning(seeded.get("error", "Access denied."))
                else:
                    st.success(seeded.get("message", "Analysts seeded."))
                    st.rerun()
        else:
            st.caption("Seeding sample analysts requires **risk** or **admin** role.")
        ranks = api_fn("/analysts/rankings")
        if denied_fn(ranks):
            st.warning(ranks.get("error", "Access denied."))
        elif ranks.get("rankings"):
            st.metric("Analysts tracked", ranks.get("analyst_count", 0))
            st.dataframe(pd.DataFrame(ranks["rankings"]), hide_index=True)
        else:
            st.info("No analyst data — seed samples to populate rankings.")
