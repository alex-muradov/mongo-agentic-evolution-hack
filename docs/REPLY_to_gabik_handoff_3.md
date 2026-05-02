# Reply to Gabik HANDOFF #3 — cluster live, tunnel quick-mode, LLM-proxy ask

**From**: alexm (AutoResearch)
**To**: Gabik (Wild Coral)
**Date**: 2026-05-02
**Re**: your `HANDOFF_to_alexm_3.md` — items unblocked + one new ask before T3.5

---

## TL;DR

Cluster up, ingest reachable, embedding contract verified end-to-end on Voyage. Two things to flag and one new ask:

1. **Tunnel is quick-mode, not named** — domain `alexmdev.xyz` was on my Cloudflare account but **not actually registered with the registrar** (`whois.nic.xyz` returns NOT FOUND). Falling back to quick tunnel for now; URL ROTATES per cloudflared restart. Pin it as an env var, not a constant. New URL ping-pong is the worst-case cost.
2. **Your AI key was off by one char** — `HANDOFF_to_alexm_3.md` has trailing `EZh`; gateway 401s on it. Working value is `…EZ` (no trailing `h`). I'm using that one. Likely a copy-paste artefact in your file — worth checking your own `.env` is the same.
3. **`ai.mongodb.com` is embeddings-only** — `/v1/chat/completions` returns 404 on every model name I tried. Voyage gateway covers retrieval, not generation. We're handling chat-completions on our side with a direct OpenAI key — **no ask from you, no LiveKit-Inference proxy** (see §6).

---

## 1. Live and verified

```
INGEST_URL  = https://lots-joan-motorcycles-acdbentity.trycloudflare.com
              ⚠ rotates on cloudflared restart — read from env, do not hard-code; alexm pings via TG
```

End-to-end smoke through this URL:

```bash
$ curl -s $INGEST_URL/healthz
{"ok":true}

$ curl -s -X POST $INGEST_URL/v1/ingest/case-study \
    -H "x-api-key: <INGEST_API_KEY from docs/credentials-for-gabik.md>" \
    -H "Content-Type: application/json" -d @candidate.json
{"id":"cs_live_debrief_…","status":"queued_for_embedding"}
```

The doc lands in `agentic_evolution.case_studies` with full schema (25 fields, `schema_version: "v1"`, `embedding: <1024 floats, unit-norm>`). `case_studies_vec` is `READY` and `queryable`. Your `find_similar_case_studies` will return empty until T3.5 historical hydration completes (see §5).

---

## 2. Cluster credentials — see `docs/credentials-for-gabik.md`

Single authoritative file with all values inline. We bypass 1Password for hackathon expediency — values shared via Telegram. Quick reference of what's there:

| Item | Use |
|---|---|
| `MONGODB_URI` (admin) | Your `MONGODB_URI` for both read AND write — alexm chose to skip per-user scoping for the hackathon |
| `INGEST_API_KEY` | Your `x-api-key` header on `POST /v1/ingest/case-study` (and on the GET from your Next.js) |
| `INGEST_URL` | Quick-tunnel hostname, see §1 caveat (rotates on restart) |
| `MONGODB_AI_KEY` | Your Voyage AI key — corrected (`…EZ`, NOT `…EZh`) |
| `REVALIDATE_SECRET` | For your `/api/revalidate` route handler |

> **Caveat on the admin user**: it has read+write across the whole cluster — `agentic_evolution` AND your `fieldcraft`. We dropped the `wildcoral_rag_ro` / `gabik_fieldcraft_rw` split for hackathon scope. Use clean boundaries inside your own code (your services should still write only to `fieldcraft`); if a junior dev later writes to `agentic_evolution.case_studies` directly, our ingest validator gets bypassed. Tighten before any post-demo extension.

---

## 3. Tunnel reality

I tried to set up `https://autoresearch.alexmdev.xyz` per your runbook §1 expectation. The DNS route registered, but `alexmdev.xyz` itself is **not registered with the registrar** — the zone exists in alexm's CF account from years ago without ever being purchased, so public DNS NXDOMAINs.

For the demo window, I'm running `cloudflared tunnel --url http://localhost:8000` which gives `*.trycloudflare.com` URL **stable for the lifetime of the process** but rotating on each restart.

Practical impact for you:
- **Wire `INGEST_URL` as an env var, never hard-code** — when alexm restarts cloudflared (sleep, network change), URL changes and alexm pings the new value via Telegram.
- If this becomes painful for your testing cycle, alexm will register the domain (5 min, ~$2/yr) and switch to named tunnel. Flag if it bites.

---

## 4. Your open Q (Option A) — confirmed

Per your §"One open question": **Option A — you own everything inside `fieldcraft`, we own everything inside `agentic_evolution`, both in the same cluster.** You drive `atlas-cli search indexes create -f backend/schema/skill_files_vector_index.json` etc. with the admin connection string in §2.

Naming convention: please prefix any ad-hoc collections in `fieldcraft` clearly so nothing collides if we ever consolidate. We use `case_studies / hypotheses / experiments / verdicts / agent_runs / evidence_sessions / learnings / open_questions / pages` — feel free to overlap names since DBs are separate, just keep semantic ownership clear.

---

## 5. T3.5 hydration — status

Currently **blocked on LLM access** (see §6). Once unblocked, I run a one-shot rewriter over `firebase-jobs-raw-historical.json` (392 jobs, 6 months, RS Lock real data), producing 392 properly-validated `case_studies` rows with:

- Service-type classification (one of our 6 enum values)
- Summary 40–80 words "we" voice, §5-PII-clean
- Borough mapping from postcode outward
- Voyage embedding (your same gateway, same model)
- Atlas insert idempotent on `(source, source_job_id)`

Expected runtime: ~10–15 min once LLM is wired. After this lands, your `find_similar_case_studies` returns real hits. I'll ping when done.

---

## 6. LLM access — handled on our side

Background: `ai.mongodb.com` (Voyage gateway) is embeddings-only — `/v1/chat/completions` returns `{"detail":"Not Found"}` on every model name. We need chat-completions for:
- T3.5 hydration rewriter (~400 calls, one-shot)
- T5 proposer (3 calls/iteration × 3 iterations)
- T6 replay_summarizer (per-session, streaming) + analyst convergence
- T7 verdict drafting + reflect

**Decision: AutoResearch uses a direct OpenAI key on our side.** No LiveKit-Inference proxy, no shared key, no Atlas Function from you. Independent billing, no coordination tax. This means:

- Embeddings stay shared via `MONGODB_AI_KEY` (Voyage gateway, your key) — ONE single canonical embedding contract across both products. Critical for `$vectorSearch` semantics.
- Chat-completions are independent per product. You keep using whatever LiveKit Inference routes to. We use OpenAI directly. The two products never need to call each other's LLM.

**Nothing required from you on this thread.**

---

## 7. Still pending from my side

- [ ] Hydrate historical case_studies — starting now that the OpenAI key is in our `.env`
- [ ] Stable `INGEST_URL` if quick-tunnel rotation becomes painful (gated on alexm registering a domain)
- [ ] T4–T7 — the actual research loop (proposer / dispatcher / analyst / verdict / reflect / Streamlit HITL). Independent of you; will not change the contract you're integrating against.

---

## 8. Cross-reference

- `docs/rag-direct-access.md` — full design + RAG contract (Voyage updated)
- `docs/wildcoral-integration.md` — your operational runbook (§5 retry semantics still authoritative)
- `docs/powersync-integration.md` — DEFERRED (kept as design ref only)
- This doc supersedes nothing; appends to the integration thread

— alexm
