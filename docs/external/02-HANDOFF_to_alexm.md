# Handoff to alexm — Wild Coral ≠ HITL Approver app

**From**: Gabik (mobile)
**To**: alexm (your team)
**Re**: your `powersync-integration.md` (HITL Mobile UI)
**Date**: 2026-05-02

Thanks for the doc. Read it end-to-end. Ack on the architecture pattern; flagging that **what I'm actually building is a different product** so we can decide whether to (a) integrate, (b) ship side-by-side on the same Atlas cluster, or (c) treat as totally separate. Detail below.

---

## TL;DR

| | Your doc | What I'm building |
|---|---|---|
| Product | HITL approver UI for agentic A/B testing of web case studies | **Wild Coral** — voice-capture agent for UK tradespeople |
| Mongo DB name | `agentic_evolution` | `fieldcraft` |
| Backend | Your FastAPI (Python) | Atlas App Services (TypeScript) |
| Tenant | `rslockandsafe` | A demo plumber ("Mike") |
| Mobile stack | TBD per Q1 | **Flutter** (locked) |
| PowerSync hosting | Cloud or self-host | **PowerSync Cloud** (locked) |
| Agent runtime | LangGraph in your FastAPI | **Native LiveKit Agents** (Python worker) with LiveKit Inference for STT/LLM/TTS |
| HITL pattern | Gate A/B/C state machine on `agent_runs.status` | Approve/Reject on a single `agent_hypotheses` row per job, after the agent proposes |
| Domain entities | `agent_runs · hypotheses · experiments · verdicts · case_studies · evidence_sessions · learnings` | `traders · jobs · agent_runs · observed_outcomes · trader_signals · agent_hypotheses · skill_files` |

The collections named `agent_runs` and `hypotheses` exist in both systems but with **different shapes and semantics** — they are not the same documents.

---

## Answers to your §10 open questions (for the version of this doc that matches your product, in case you find another mobile builder)

1. **Q1 — mobile stack**: I'm on Flutter for Wild Coral. If you want me to also build your HITL UI, Flutter works (PowerSync Flutter SDK is solid). Otherwise pick whatever — RN PowerSync SDK is also production-ready.
2. **Q2 — PowerSync hosting**: Cloud, free tier, single project per environment. Same call I'd make for you.
3. **Q3 — role/tenant**: Single `approver` + single `rslockandsafe` tenant is fine for the demo. Add `tenant_id` to all docs anyway so the sync rule is future-proofed (cheap now, costly later).
4. **Q4 — push notifications on new pending gate**: For demo, polling-on-foreground is fine. If you want pushes, the cleanest is FCM/APNs from your FastAPI on the gate transition — no need for a webhook-to-me indirection.
5. **Q5 — optimistic UI**: Yes, optimistic from the command response, reconcile from sync. Standard.

---

## What I picked up from your doc that I'm applying to Wild Coral

These are good ideas regardless of products:

1. **Atlas M0 only works for local/dev** — M10+ for any externally-reachable PowerSync. Updating my setup notes; for the hackathon I'll stay on M0 + IP allowlist.
2. **PowerSync connects to Mongo directly, not via my tunnel.** Was implicit in my plan; making it explicit. Cloudflared / ngrok is only for App Services HTTPS endpoints (the write path).
3. **Read = sync, write = HTTPS commands** — same principle. My write path goes to App Services functions (`POST /sync/upload` for PowerSync uploadData fanout, plus dedicated HTTPS routes for verdicts).
4. **Idempotency on state-machine transitions** — applying to my `POST /jobs/:id/trader_verdict`: a second call after success returns 200 with the same updated doc, not 409. (Your 409 model is also valid; 200-with-current-state feels less surprising for the trader UI.)
5. **JWT claim shape** — `sub`, `iat`, `exp`, `aud`, `iss`, `role`. I'll mirror this in `backend/functions/powersyncToken.ts`. My `aud` is `powersync`, `iss` is `wild-coral`, `role` is `trader`, plus `trader_id` claim for sync-rule scoping. HS256 demo, RS256/JWKS for prod — same path you described.

---

## Where the two products could converge (if you want)

Re-reading your doc alongside the `POST /v1/ingest/debrief` contract that came in separately (job_id format `rsl_…`, worker_id, transcript, audio_url) — **these are the same partner**. So convergence is more likely than I first thought. Three options:

**Option A — fully separate.** Two Atlas clusters, two PowerSync projects, two backends, two mobile apps. Clean, no coordination tax. Cleanest if your team has its own mobile builder and we're independent demos.

**Option B — share Atlas, separate everything else.** One cluster, two databases (`agentic_evolution` + `fieldcraft`). Two PowerSync projects. Saves one Atlas bill, no functional sharing.

**Option C — Wild Coral is the field-capture front-end for your autoresearch back-end.** Most interesting for a single end-to-end demo:
- A worker (rslockandsafe's "tom" or my "Mike") finishes a job and captures via Wild Coral (voice with LiveKit, or async)
- Wild Coral's qualification agent runs propose-execute-evaluate, surfaces a verdict to the trader
- On trader approval, the resulting `case_study` candidate flows into your `case_studies` collection
- Your gate A picks it up as a hypothesis to A/B test on the web
- Your gates B and C run as you designed
- Convergence point: the `case_study` doc shape

The two loops are the same pattern at different abstraction levels — mine is per-job qualification, yours is per-page A/B testing.

**My read of intent**: the `POST /v1/ingest/debrief` spec was sent to me as if Wild Coral should expose that endpoint to receive debriefs from your workers' apps. If yes, that's Option C. **Please confirm so I scope correctly.** If yes, I'll:
- Implement `POST /v1/ingest/debrief` as a write-only ingress that creates a `jobs` doc with `capture_mode: "ingested"` and dispatches the qualification agent (idempotent on partner `job_id`)
- Define the `case_study` document we hand back to your `case_studies` collection on trader approval
- Drop the assumption that "trader" == "approver" (your HITL is at a higher abstraction; my "trader_verdict" stays in-Wild-Coral and produces an artifact your gates consume)

---

## What's "off" in your doc relative to current PowerSync best practice (minor)

These don't affect us if we go Option A, but flagging in case useful:

1. **`@powersync/web` import in §4** — If your mobile target is React Native, the import is `@powersync/react-native`, not `@powersync/web`. You hint at both. Lock the choice once Q1 is answered, then keep one example.
2. **`Schema` / `Table` / `Column` constructors** — These are valid, but the modern PowerSync JS SDK also supports an object-literal form (`new Table({ name, columns: [...] })` works; `new Schema({ tables })` is also valid). Either way. For Flutter, the Dart SDK has its own shape (`Table('name', [Column.text('foo')])`).
3. **"`id` is implicit on every PowerSync table — it maps from Mongo `_id`"** — Correct, **as long as Mongo `_id` is a string**. If anything in your pipeline lets ObjectIds slip in, PowerSync's MongoDB connector serializes them to strings but watch for client-side filter mismatches. Your `_id: "run_2026_..."` convention solves this.
4. **JWT auth `secret` field name** — In PowerSync's current YAML schema this section is `client_auth.jwks` for JWKS or `client_auth.shared_secret`, not `auth.jwks`. Check the latest config reference; the field name has shifted between versions.
5. **No mention of `post_images`** — For the MongoDB connector, you need `post_images: auto_configure` in the `replication.connections[*]` block, otherwise updates won't include the full document. Just a heads up.

---

## What I'd appreciate from you

Nothing blocking. Three small things if/when you're around:

1. **Atlas MongoDB connection string format you're using** — sanity check. (Your doc says `MONGO_URI` + `MONGO_DB`; mine is `MONGODB_URI` + `MONGODB_DB`. Just naming, no functional issue.)
2. **Confirmation we're going Option A** so I can stop reading your endpoint contract as if I needed to implement against it. (I assumed Option A and wrote this; correct me if wrong.)
3. **Your team name / project name** so I can cite you correctly if our architectures end up referenced together (paper, post, demo writeup).

---

## What I'm shipping (so you have visibility)

- Flutter app `app/` — capture picker (voice / quick), agent session, run feed, run detail with streaming reasoning chunks, skill files viewer
- Atlas App Services `backend/` — `uploadData`, `photoUpload`, `livekitToken`, `onJobInserted` (trigger), `onTraderVerdict`, `streamReasoning`, `powersyncToken`
- LiveKit Agents Python worker `agent/` — single `qualification-agent` with native `@function_tool` methods, dispatched both for voice (trader joins) and async (trigger) modes
- PowerSync `powersync/sync_rules.yaml` — bucket per trader, syncing 6 collections
- Seed scripts `scripts/`

Repo: `/Users/gabik/Documents/GitHub/agentmongohack`

— Gabik
