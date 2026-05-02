"""AutoResearch HITL control plane — Streamlit.

Run: `uv run --extra hitl streamlit run hitl_ui/app.py`
Talks to the FastAPI on http://localhost:8000 by default.
"""
import os
import time
from typing import Any

import httpx
import streamlit as st

API_BASE = os.environ.get("AUTORESEARCH_API_BASE", "http://localhost:8000")
API_KEY = os.environ.get("AUTORESEARCH_API_KEY", os.environ.get("INGEST_API_KEY", ""))
HEADERS = {"x-api-key": API_KEY} if API_KEY else {}

STATUS_BADGE = {
    "running": ("🔄", "blue"),
    "awaiting_gate_a": ("⏸ A", "orange"),
    "awaiting_gate_b": ("⏸ B", "orange"),
    "awaiting_gate_c": ("⏸ C", "orange"),
    "completed": ("✅", "green"),
    "aborted": ("❌", "red"),
}


def api_get(path: str) -> Any:
    r = httpx.get(f"{API_BASE}{path}", headers=HEADERS, timeout=10.0)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json: dict | None = None) -> Any:
    r = httpx.post(f"{API_BASE}{path}", headers=HEADERS, json=json or {}, timeout=10.0)
    r.raise_for_status()
    return r.json()


st.set_page_config(page_title="AutoResearch HITL", layout="wide", page_icon="🤖")
st.markdown(
    "<style>div.block-container{padding-top:1rem}</style>",
    unsafe_allow_html=True,
)

# --- sidebar ---
with st.sidebar:
    st.markdown("### AutoResearch")
    st.caption(f"API: `{API_BASE}`")
    if not API_KEY:
        st.error("AUTORESEARCH_API_KEY not set in env")

    page_id = st.text_input("Page ID", value="areas/east-london")
    if st.button("▶ Start new run", use_container_width=True, type="primary"):
        try:
            res = api_post("/v1/agent/runs", json={"page_id": page_id})
            st.session_state["selected_run"] = res["run_id"]
            st.success(f"Started: `{res['run_id']}`")
        except Exception as e:
            st.error(f"Start failed: {e}")

    st.divider()
    st.markdown("**Recent runs**")
    auto_refresh = st.checkbox("Auto-refresh (3s)", value=True)

    try:
        runs = api_get("/v1/agent/runs?limit=15").get("items") or []
    except Exception as e:
        runs = []
        st.error(f"List failed: {e}")

    for run in runs:
        emoji, _ = STATUS_BADGE.get(run["status"], ("•", "gray"))
        summary = (run.get("hypothesis_summary") or "").strip()
        if summary:
            tail = summary[:64] + ("…" if len(summary) > 64 else "")
        else:
            tail = f"(no hypothesis yet · {run['_id'][-8:]})"
        label = f"{emoji}  {tail}"
        if st.button(label, key=f"run-btn-{run['_id']}", use_container_width=True, help=run["_id"]):
            st.session_state["selected_run"] = run["_id"]


# --- main ---
selected = st.session_state.get("selected_run")

if not selected:
    st.markdown("# AutoResearch — agentic A/B research")
    st.info("Pick a run from the sidebar or start a new one.")
    st.stop()

try:
    run = api_get(f"/v1/agent/runs/{selected}")
except Exception as e:
    st.error(f"Failed to load run: {e}")
    st.stop()

emoji, color = STATUS_BADGE.get(run["status"], ("•", "gray"))
st.markdown(f"## {emoji} :{color}[{run['status'].upper()}] · `{run['_id']}`")
hdr_cols = st.columns(4)
hdr_cols[0].metric("page", run["page_id"])
hdr_cols[1].metric("iteration", run["iteration"])
hdr_cols[2].metric("current node", run["current_node"])
hdr_cols[3].metric("trigger", run["trigger"])

# --- HITL gate panel ---
gate = run.get("pending_gate")
if gate:
    gate_titles = {
        "A": "Gate A — Approve hypothesis before dispatch",
        "B": "Gate B — Confirm early-stop signal",
        "C": "Gate C — Approve final verdict",
    }
    st.warning(f"**{gate_titles.get(gate, f'Gate {gate}')}**", icon="⏸")
    cols = st.columns([1, 1, 4])
    if cols[0].button(f"✅ Approve gate {gate}", type="primary", use_container_width=True):
        try:
            api_post(f"/v1/agent/runs/{selected}/resume?after_gate={gate}")
            st.success(f"Gate {gate} approved — resuming")
            time.sleep(0.5)
            st.rerun()
        except Exception as e:
            st.error(f"Approve failed: {e}")
    if cols[1].button("✋ Hold (no-op)", use_container_width=True):
        st.info("Held — no-op (run remains paused)")

# --- tabs ---
tabs = st.tabs(["Timeline", "Hypothesis", "Experiment", "Verdict", "Learning", "Raw"])

with tabs[0]:
    st.markdown("##### Log tail (newest first)")
    for e in (run.get("log_tail") or [])[::-1]:
        ts = (e.get("at") or "")[11:19]
        st.markdown(f"`{ts}` · **{e['node']}** · {e['msg']}")

with tabs[1]:
    h = run.get("hypothesis")
    if not h:
        st.caption("No hypothesis yet (proposer hasn't run).")
    else:
        st.markdown(f"### {h['statement']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("primary metric", h.get("expected_metric", "-"))
        c2.metric("expected direction", h.get("expected_direction", "-"))
        c3.metric("effect size", h.get("expected_effect_size", "-"))
        c4.metric("status", h.get("status", "-"))
        st.markdown("**Rationale**")
        st.write(h.get("rationale", "-"))
        col_a, col_b = st.columns(2)
        col_a.markdown("**Variant A rule**")
        col_a.info(h.get("variant_a_rule", "-"))
        col_b.markdown("**Variant B rule**")
        col_b.info(h.get("variant_b_rule", "-"))
        st.markdown(f"**RAG sources** ({len(h.get('rag_sources') or [])})")
        st.code("\n".join(h.get("rag_sources") or []), language="text")
        if h.get("open_questions_delta"):
            st.markdown("**New open questions raised**")
            for q in h["open_questions_delta"]:
                st.markdown(f"- {q}")

with tabs[2]:
    e = run.get("experiment")
    if not e:
        st.caption("No experiment yet (dispatcher hasn't run).")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("status", e.get("status", "-"))
        c2.metric("stop signal", e.get("stop_signal") or "—")
        c3.metric("min sample/arm", e.get("min_sample_per_arm", "-"))
        c4.metric("max runtime (min)", e.get("max_runtime_minutes", "-"))
        ls = e.get("live_stats") or {}
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Variant A**")
            va = ls.get("variant_a") or {}
            st.write(f"impressions: {va.get('n', 0)}")
            st.write(f"phone_click: {va.get('phone_click', 0)}")
            st.write(f"callback_form_submit: {va.get('callback_form_submit', 0)}")
            st.markdown("**case_study_ids**")
            st.code("\n".join(e.get("variant_a", {}).get("case_study_ids") or []), language="text")
        with col_b:
            st.markdown("**Variant B**")
            vb = ls.get("variant_b") or {}
            st.write(f"impressions: {vb.get('n', 0)}")
            st.write(f"phone_click: {vb.get('phone_click', 0)}")
            st.write(f"callback_form_submit: {vb.get('callback_form_submit', 0)}")
            st.markdown("**case_study_ids**")
            st.code("\n".join(e.get("variant_b", {}).get("case_study_ids") or []), language="text")
        if ls.get("last_pulled_at"):
            st.caption(f"Last HogQL pull: {ls['last_pulled_at']}")

with tabs[3]:
    v = run.get("verdict")
    if not v:
        st.caption("No verdict yet (verdict_node hasn't run).")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("status", v.get("status", "-"))
        c2.metric("confidence", v.get("confidence", "-"))
        c3.metric("stop rule", v.get("stop_rule", "-"))
        pm = v.get("primary_metric") or {}
        st.markdown(f"**Primary metric** `{pm.get('name','-')}` · lift={pm.get('lift'):.4f} · CI=[{pm.get('ci_low'):.4f}, {pm.get('ci_high'):.4f}]" if pm else "")
        st.markdown("**Reasoning**")
        st.write(v.get("reasoning", "-"))
        if v.get("counter_evidence"):
            st.markdown("**Counter evidence (what would invalidate)**")
            st.write(v["counter_evidence"])
        if v.get("generated_open_questions"):
            st.markdown("**Generated open questions**")
            for q in v["generated_open_questions"]:
                st.markdown(f"- {q}")

with tabs[4]:
    l = run.get("learning")
    if not l:
        st.caption("No learning yet (reflect hasn't run).")
    else:
        c1, c2 = st.columns(2)
        c1.metric("borough", l.get("borough") or "—")
        c2.metric("service_type", l.get("service_type") or "—")
        st.markdown("**What worked**")
        st.success(l.get("what_worked", "-"))
        st.markdown("**Reasoning** (embedded into vector index for future RAG)")
        st.write(l.get("reasoning", "-"))
        if l.get("counter_factors"):
            st.markdown("**Counter factors**")
            st.write(l["counter_factors"])

with tabs[5]:
    st.json(run)


if auto_refresh and run["status"] in {"running", "awaiting_gate_a", "awaiting_gate_b", "awaiting_gate_c"}:
    time.sleep(3)
    st.rerun()
