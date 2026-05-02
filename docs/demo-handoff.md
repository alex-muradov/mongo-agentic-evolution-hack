# Demo handoff to Gabik — pre-demo state, flow, and your side

**From**: alexm
**To**: Gabik
**Date**: 2026-05-02
**Re**: full pipeline closed; final coordination before demo

---

## TL;DR

Agentic loop is **end-to-end closed and demo-ready**: proposer → dispatcher → analyst → replay_summarizer → verdict → reflect, with HITL gates A/B/C and a Streamlit control plane on `:8501`. Everything writes real artifacts to Mongo, calls real LLM/Voyage/PostHog/Vercel APIs.

What stops us from a perfect demo right now: no live A/B traffic has flowed yet because rollout was 100% control. PostHog feature flag `case_studies_v1` is now created (by us via your personal API key, see prior `REPLY_to_gabik_4.md` §3) and configured 0/50/50 — next visitor on `/areas/east-london` will be bucketed.

---

## 1. What's live on our side

| Component | Status |
|---|---|
| MongoDB Atlas (cluster, vector index `case_studies_vec`) | ✅ live |
| 366 hydrated case_studies (115 East London, 4 boroughs) | ✅ live |
| FastAPI on `:8000`, exposed via cloudflared at quick-tunnel URL | ✅ live (URL in `docs/credentials-for-gabik.md`) |
| Change-stream watcher on `case_studies` | ✅ active |
| Proposer (RAG over case_studies + learnings + open_questions, gpt-4o-mini, gate A pause) | ✅ |
| Dispatcher (LLM partition → tag case_studies in Mongo → POST /api/revalidate to your Vercel) | ✅ |
| Analyst (HogQL pull → live_stats per arm, bootstrap CI on lift, stop_signal) | ✅ |
| Replay_summarizer (PostHog session recordings → per-session LLM summary → evidence_sessions) | ✅ |
| Verdict_node (CI-based status classification, LLM reasoning + counter_evidence, gate C pause) | ✅ |
| Reflect (write Learning with Voyage embedding → close open_questions → untag case_studies → revalidate cleanup) | ✅ |
| Streamlit HITL UI on `:8501` (gate Approve buttons, log timeline, Hypothesis/Experiment/Verdict/Learning tabs) | ✅ |
| Loop iteration cap = 3 | ✅ |

---

## 2. The demo flow — step by step

### Pre-demo (do once, ~5 min before)

1. **Verify your Vercel ISR cache**: open `https://www.rslockandsafe.co.uk/areas/east-london`, scroll to "Recent Jobs in East London" — you should see ~6 case studies (alexm's hydrated, e.g. "Security audit for residential building", "Repair of disabled toilet door handle"). If yes, integration is live.
2. **PostHog flag `case_studies_v1` already exists** at 0/50/50 (we created it). Optionally pre-warm a couple of incognito sessions to seed events.
3. **alexm starts demo**: opens Streamlit at `http://localhost:8501`, FastAPI uvicorn already running.

### During demo (~3-5 minutes)

Step 1 — **Trigger run** (Streamlit sidebar): `▶ Start new run` with `page_id=areas/east-london`. Run ID appears.

Step 2 — **Proposer fires** (~10-15s): RAGs 8 case_studies, calls gpt-4o-mini, drafts Hypothesis with statement + rationale + variant_a_rule + variant_b_rule + 2-3 new open_questions. Run pauses at **Gate A**.

Step 3 — **HITL gate A**: alexm clicks `✅ Approve gate A`. Emphasises "human keeps the agent honest — we approve before any change ships."

Step 4 — **Dispatcher fires** (~5s): partitions rag_sources via second gpt-4o-mini call, tags case_studies in Mongo with variant=A/B, **POSTs to your `/api/revalidate?tag=case-studies:east-london`**. Your site's ISR cache invalidates. (Visible in your access logs.)

Step 5 — **Analyst fires** (~2s): HogQL pull from PostHog, updates `live_stats.variant_a` and `live_stats.variant_b`. If real traffic has flowed since flag rollout, you'll see real n/conversions per arm; else 0/0.

Step 6 — **Replay_summarizer fires** (~5-30s depending on session count): pulls session recordings, per-session LLM summary, writes evidence_sessions. Skips gracefully if 0.

Step 7 — **Verdict_node fires** (~3-5s): bootstrap CI on lift (5000 iterations), classifies status (`confirmed-high` / `directional` / `refuted` / `inconclusive`), gpt-4o-mini writes reasoning + counter_evidence + 1-3 generated_open_questions. Run pauses at **Gate C**.

Step 8 — **HITL gate C**: alexm reviews verdict in Streamlit's Verdict tab, clicks `✅ Approve`.

Step 9 — **Reflect fires** (~5-10s): writes Learning doc with **Voyage 1024-dim embedding** (so future iterations can RAG-retrieve it), closes the open_questions this experiment answered, adds the new ones, **untags case_studies** (variant → null), **POSTs revalidate again** so your site goes back to control rendering.

Step 10 — **Run completes**. Streamlit shows final state with all 4 artifacts.

Optional Step 11 — **Show second iteration**: trigger another run. Proposer's RAG now retrieves the Learning from the previous run. Demonstrates "the agent learned and grounded the next hypothesis on its own past." This is the load-bearing "agentic evolution" claim for the jury.

---

## 3. What you do during the demo

**Minimum**: nothing — your site keeps serving, ISR auto-handles cache invalidation as we POST to your endpoint.

**Recommended for visual punch**: have your `/areas/east-london` page open in a browser (with PostHog session recording already accepting cookies — opt-in once). Refresh after Step 4 (post-revalidate) to show the page rendering DIFFERENT case_studies than 30 seconds ago. Then refresh after Step 9 (post-reflect cleanup) to show it back to control.

**If demo time has real visitors** (you running ads or sharing the URL beforehand): even better — analyst will pull non-zero counts, verdict gets actual CI bounds, replay_summarizer summarizes real sessions. Add a few minutes of pre-demo traffic-warming and the demo carries the "real visitors, real outcomes" weight.

---

## 4. Pre-demo checklist (your side)

- [ ] PostHog flag `case_studies_v1` exists with active rollout (we created it; verify it didn't get auto-deactivated)
- [ ] Your `/api/revalidate` route handler is deployed on Vercel and reads `REVALIDATE_SECRET` correctly (we verified one POST worked end-to-end — `{"ok":true,"revalidatedAt":...}` came back)
- [ ] PostHog session recording consent banner works on at least one browser session (so we have a recording to summarize)
- [ ] The 3 env vars on Vercel — `CASE_STUDIES_API`, `CASE_STUDIES_API_KEY`, `REVALIDATE_SECRET` — are set in Production env
- [ ] You decided event-name canonical (production fires `phone_call_clicked` / `callback_form_submitted`; spec said `phone_click` / `callback_form_submit`). Our analyst tolerates both via HogQL `IN ('phone_click','phone_call_clicked')`. No blocker either way.

---

## 5. Pre-demo checklist (our side — alexm)

- [x] FastAPI uvicorn running on `:8000`
- [x] cloudflared tunnel up
- [x] Streamlit on `:8501` running
- [x] PostHog flag created and active
- [ ] Run a full end-to-end loop in dry-run 30 min before to warm caches and verify
- [ ] Optionally tighten proposer/dispatcher prompts to balance variant_a/b assignments (currently ~3-vs-1)

---

## 6. Risks and "if X breaks" plan

| Risk | Mitigation |
|---|---|
| Cloudflared tunnel rotates URL mid-demo | alexm restarts tunnel, pings new URL in TG; you update `CASE_STUDIES_API` in Vercel and redeploy (~2 min). Demo can pause briefly. |
| OpenAI API rate-limit / outage | Demo continues without verdict reasoning text — pipeline still produces all the structured CI numbers. We narrate from the data. |
| PostHog API timeout | Analyst returns `insufficient_sample` gracefully; verdict is `inconclusive`. Demo still completes; we narrate that "real-world demos sometimes look like this — null result, agent still produced learnings." |
| Atlas cluster quota | M0 is 512MB; we're using <50MB. Comfortable. |
| LLM produces invalid JSON | Both proposer and dispatcher have a single-retry loop with feedback. Reflect bumped max_tokens to 1800 to avoid truncation. |

---

## 7. After demo

Within 24h post-demo, we should rotate:
- Mongo admin password (was pasted in chat-logs during dev)
- INGEST_API_KEY
- REVALIDATE_SECRET
- OpenAI sk-proj key (alexm's)

Your `MONGODB_AI_KEY` and PostHog personal key are yours to rotate as you see fit.

---

## 8. Cross-reference

- `docs/credentials-for-gabik.md` — single source of truth for env values
- `docs/website-handoff.md` — read-surface contract
- `docs/REPLY_to_gabik_handoff_3.md`, `docs/REPLY_to_gabik_4.md` — earlier status updates
- `docs/rag-direct-access.md` — Wild Coral mobile RAG contract (parked)
- `docs/wildcoral-integration.md` — Wild Coral mobile runbook (parked)
- `docs/atlas-setup.md` — provisioning notes (alexm-side)

— alexm
