# AutoResearch

A closed-loop agentic A/B research pipeline for programmatic-SEO landing pages. The agent **proposes** a hypothesis from past evidence, **dispatches** an A/B variant, **measures** real user behaviour, **judges** the result with a confidence interval, and **reflects** the finding into a vector-retrievable knowledge base — so the next iteration is grounded in what came before.

Built for the MongoDB / LangGraph / PostHog hackathon (May 2026). Real partner: [RS Lock and Safe](https://www.rslockandsafe.co.uk/areas/east-london), a London locksmith.

---

## What it does

```
case_studies (Atlas Vector Search) ────────┐
learnings    (Atlas Vector Search) ────RAG─┤
open_questions (scalar)             ──────┤
                                          ▼
   ┌───────────────────────────────────────────────────────────┐
   │                                                            │
   │   proposer ──gate A──▶ dispatcher ──▶ analyst ──gate B──▶  │
   │   (LLM)               (PostHog flag, (HogQL,    (early-    │
   │                        Mongo tag,    bootstrap   stop      │
   │                        Vercel        CI)         confirm)  │
   │                        revalidate)                          │
   │                                          │                  │
   │   replay_summarizer ◀──── (per session) ─┘                  │
   │   (PostHog rec. → LLM)                                      │
   │                                                              │
   │   verdict_node ──gate C──▶ reflect                           │
   │   (status,                  (Learning + embed,               │
   │    LLM reasoning)            close open_questions,           │
   │                              untag, revalidate)              │
   └───────────────────────────────────────────────────────────┘
                                          │
                                          ▼
   New Learning  ─────► future RAG (next iteration sees it)
```

Diagram: `diagrams/agent-graph.png` (full-resolution) and `.svg` / `.mmd`.

The load-bearing claim: **two channels of continuity feed the next iteration**:
1. **Vector retrieval** — past `learnings` are RAG-pulled by the proposer alongside `case_studies`.
2. **Open-question agenda** — `reflect` closes answered questions and records new ones; next `proposer` sees the updated list.

---

## Stack

| Concern | Component |
|---|---|
| Storage + vector search | MongoDB Atlas (M0, EU) — `case_studies_vec`, `learnings_vec` (1024-dim, cosine, unit-norm) |
| Embeddings | Voyage `voyage-4-large` via `https://ai.mongodb.com/v1/embeddings` (OpenAI-compatible gateway) |
| LLM (chat-completions) | OpenAI `gpt-4o-mini` with strict JSON-schema structured output |
| A/B routing | PostHog multivariate feature flag `case_studies_v1` (server-side eval) |
| Measurement | PostHog HogQL Query API + Session Recordings export |
| Cache invalidation | Vercel `revalidateTag` via shared-secret webhook |
| Backend | FastAPI (async, motor) — port 8000 |
| HITL UI | Streamlit — port 8501 |
| Public reach | cloudflared (quick tunnel to laptop) |

The agent runtime is a plain async runner over a fixed pipeline (LangGraph migration deferred — the topology is linear with HITL pauses, no branching, so a 100-line `runner.py` is enough). Hand-rolled bootstrap CI on lift; no scipy.

---

## Repo layout

```
agent/                  Pipeline nodes (each ~100-200 lines)
  proposer.py             RAG + LLM hypothesis drafting
  dispatcher.py           variant partition + Mongo tagging + Vercel revalidate
  analyst.py              HogQL pull, bootstrap CI, stop-signal logic
  replay_summarizer.py    PostHog session recordings → per-session LLM summary
  verdict_node.py         CI classification + LLM reasoning
  reflect_node.py         Learning write (with embedding) + cleanup
  runner.py               Sequential async runner with gate A/B/C pause/resume
  change_stream.py        Atlas change-stream watcher on case_studies

domain/                 Pydantic models — single source of truth
  case_study.py           CaseStudyInternal (Mongo) + CaseStudyPublic (API projection)
  hypothesis.py / experiment.py / verdict.py / learning.py / agent_run.py
  evidence_session.py / open_question.py / page.py
  enums.py / validators.py  §5 PII regexes from Gabik's contract

app/                    FastAPI HTTP surface
  routes/case_studies.py  POST /v1/ingest/case-study + GET /v1/case-studies
  routes/agent.py         POST/GET /v1/agent/runs + resume

integrations/
  embeddings.py           Voyage gateway client wrapper
  posthog.py              HogQL + session recordings reader

mongo/
  indexes.py              ensure_indexes + ensure_search_indexes (Atlas Vector Search)

hitl_ui/app.py          Streamlit — Approve buttons, log timeline, doc tabs

scripts/
  hydrate_historical.py   Bulk import of 366 RS Lock historical jobs (LLM rewriter + embed)
  repair_borough.py       Backfill via postcodes.io
  verify_hydration.py     Sanity report on Mongo state
  tunnel.sh               cloudflared launch

docs/                   Internal handoffs to/from the integration partner
docs/external/          Partner's original handoffs (the contracts we built against)
diagrams/               LangGraph topology (mermaid + rendered PNG/SVG)
```

---

## Run it locally

```bash
git clone https://github.com/alex-muradov/mongo-agentic-evolution-hack
cd mongo-agentic-evolution-hack

# Python deps
uv sync --extra agent --extra hitl

# Configure secrets
cp .env.example .env
$EDITOR .env  # fill MONGODB_URI, INGEST_API_KEY, MONGODB_AI_KEY, OPENAI_API_KEY,
              # POSTHOG_*, REVALIDATE_SECRET, NEXT_REVALIDATE_URL

# Start the backend (auto-creates Atlas search indexes on first boot)
uv run uvicorn app.main:app --port 8000

# In a second terminal — Streamlit HITL UI
AUTORESEARCH_API_KEY=$(grep INGEST_API_KEY .env | cut -d= -f2) \
  uv run --extra hitl streamlit run hitl_ui/app.py --server.port 8501

# In a third terminal — public tunnel (only if you need external POSTs)
./scripts/tunnel.sh
```

Open `http://localhost:8501`, click **▶ Start new run**, walk through gates A → C.

---

## Demo mode

For demos without live PostHog traffic, set:

```env
DEMO_SIMULATE_TRAFFIC=true
```

The analyst then fabricates realistic per-arm stats (n ≈ 100–140, ~6% phone-click base rate, lift respecting the hypothesis's `expected_direction`). Bootstrap CI runs over the synthetic numbers; verdict is meaningful (`confirmed-directional` / `refuted` / `inconclusive` based on actual sample).

---

## Hydrating real data

`scripts/hydrate_historical.py` ingests the partner's exported job records, runs each through:
1. PII strip (regex pass over address, postcode, names — outward postcode kept, full address dropped)
2. LLM rewrite (gpt-4o-mini, strict JSON schema): service_type classification, summary 50–75 words "we" voice, no prices, no full postcodes
3. §5 PII validators (case-studies-handoff §5)
4. Voyage embedding
5. Idempotent Mongo upsert (`_id = cs_<source>_<source_job_id>`)

```bash
uv run python scripts/hydrate_historical.py --limit 5 --dry-run  # spot-check 5
uv run python scripts/hydrate_historical.py                       # full set
uv run python scripts/repair_borough.py                           # postcodes.io backfill
uv run python scripts/verify_hydration.py                         # report
```

---

## Integration with the website

The partner's Next.js site at `/areas/[area]` calls `GET /v1/case-studies?area=&variant=`, where `variant` comes from the visitor's PostHog flag bucket. When the agent flips variants in Mongo, it POSTs to the site's `/api/revalidate?tag=case-studies:{area}` to bust the ISR cache so the next visitor sees fresh content.

Full contract: `docs/website-handoff.md`. The partner's original spec we built against: `docs/external/01-case-studies-handoff-from-gabik.md`.

---

## What's intentionally simple

- **No LangGraph yet** — the pipeline is linear with HITL pauses; a flat async runner is shorter than a graph DSL would be. Easy to migrate when branching/concurrency is needed.
- **Bootstrap CI hand-rolled** — 5000 iterations of binomial sampling per arm. Honest for small-n hackathon scale; no scipy.
- **No write-side PostHog** — the flag is configured once in PostHog UI; the agent only reads. Keeps blast radius small and the integration narrative cleaner.
- **Cloudflared quick-tunnel, not a hosted backend** — single laptop. URL rotates per restart; partner's env-var pattern handles it.

## What's known-incomplete

- Replay-summarizer requires the partner's client-side capture to attach `variant` property to events; without that, sessions are skipped (`skipped_no_variant`).
- Proposer prompt occasionally cites < 6 of 8 retrieved case_studies, leading to imbalanced 4-vs-1 variant assignments. Tighten before larger-scale demos.
- Loop iteration cap = 3 (in-runner constant, not per-experiment).

---

## License

This is a hackathon submission. Code under MIT-style permissive use; check with the partner before reusing the historical jobs dataset.
