# AutoResearch ↔ Wild Coral — Direct Atlas Vector Search Access

**From**: alexm (AutoResearch)
**To**: Gabik (Wild Coral)
**Date**: 2026-05-02
**Re**: your `HANDOFF_to_alexm.md` + supersedes `powersync-integration.md`

---

## Decisions taken

1. **Convergence — Option C confirmed.** Wild Coral feeds AutoResearch via a `case_study_candidate` artifact after your `trader_verdict` approves a job. We host the `case_studies` collection; you produce its candidates. The Atlas Change Stream on `case_studies` triggers our research loop.
2. **Cluster topology — Option B confirmed.** Shared Atlas cluster (M0 free, EU), two DBs: `fieldcraft` (yours) + `agentic_evolution` (ours). One Atlas account, separate DB-level perms.
3. **My PowerSync handoff is retracted.** It was pitched as if you were building HITL for AutoResearch — you're not. We use Streamlit on our side. `docs/powersync-integration.md` is now marked deferred.
4. **RAG access for your LiveKit agent — direct `$vectorSearch`.** No HTTP RAG service from us. You connect to our `agentic_evolution.case_studies` from your Python worker via motor and query the vector index directly. Lowest latency, fewest moving parts, removes our service from your critical path.
5. **Embedding stack — Voyage via MongoDB AI gateway.** Per your follow-up proposal: single `MONGODB_AI_KEY` (your `al-...`) covers both sides, model `voyage-4-large` at 1024 dims, endpoint `https://ai.mongodb.com/v1/embeddings`. Replaces my earlier `text-embedding-3-small`/1536 plan. We have zero docs embedded yet, so the swap is free.

---

## What we provision and hand over

| Item | Delivery |
|---|---|
| Atlas cluster (shared) | created on my account, EU region; connection string in `docs/credentials-for-gabik.md` |
| DB `agentic_evolution` | created |
| Collection `case_studies` | created with vector index `case_studies_vec` |
| Atlas user `wildcoral_rag_ro` | role: `read` on `agentic_evolution.case_studies` ONLY; no other DB access |
| `POST /v1/ingest/case-study` endpoint | for Option C handoff |
| Cloudflared named tunnel for the ingest endpoint | stable hostname; we share once |

---

## Embedding contract — locked, frozen for the hackathon

You generate vectors on your side; we agree on the model so `$vectorSearch` semantics match.

| Field | Value |
|---|---|
| Model | `voyage-4-large` (Voyage AI, hosted by MongoDB) |
| Endpoint | `https://ai.mongodb.com/v1/embeddings` |
| Auth | single `MONGODB_AI_KEY` — your existing `al-...`; OpenAI-compatible response shape |
| Dimensions | 1024 |
| Similarity | cosine |
| Normalization | unit-norm on store AND query sides |
| Field path in our docs | `embedding` |

> **Why this gateway**: one auth surface across both products, no OpenAI account needed on either side. The gateway speaks the OpenAI Embeddings API shape, so `openai-python` works unchanged with `base_url="https://ai.mongodb.com/v1"`.

---

## Vector index definition (`case_studies_vec`)

```json
{
  "name": "case_studies_vec",
  "type": "vectorSearch",
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine" },
    { "type": "filter", "path": "borough" },
    { "type": "filter", "path": "postcode_outward" },
    { "type": "filter", "path": "service_type" },
    { "type": "filter", "path": "completed_at" },
    { "type": "filter", "path": "outcome" },
    { "type": "filter", "path": "schema_version" }
  ]
}
```

---

## `case_studies` document — fields you can read

```jsonc
{
  "_id": "cs_<source>_<source_job_id>",       // string, stable, idempotent
  "schema_version": "v1",                     // bumped only on breaking change
  "source": "live_debrief" | "historical_firebase",
  "source_job_id": "rsl_2025_01_17_e8_0042",
  "page_id": "areas/east-london",

  "borough": "Hackney",
  "postcode_outward": "E8",
  "service_type": "lock_change",              // enum below
  "service_tag": "Move-in security",          // ≤24 chars

  "completed_at": "2026-02-14T22:11:00Z",
  "duration_minutes": 65,
  // price_band is on the doc but is INTERNAL — please do not surface

  "problem": "...",
  "solution": "...",
  "outcome": "success" | "partial" | "referred",
  "title": "...",                             // ≤70 chars
  "summary": "...",                           // 40–80 words, "we" voice, no prices

  "street": "Elderfield Road",
  "customer_feedback": null | { "quote": "...", "rating": 5, "consent_given_at": "..." },

  "embedding": [/* 1024 floats, unit-norm */],
  "variant": null | "control" | "A" | "B",

  "created_at": "...",
  "updated_at": "..."
}
```

`service_type` enum: `emergency_lockout` | `lock_change` | `safe_opening` | `key_extraction` | `upvc_repair` | `security_audit`.

---

## Query example — your LiveKit agent (Python)

```python
import os
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI
from livekit.agents import function_tool

mongo = AsyncIOMotorClient(os.environ["MONGODB_URI"])
case_studies = mongo["agentic_evolution"]["case_studies"]
oai = AsyncOpenAI(
    base_url="https://ai.mongodb.com/v1",
    api_key=os.environ["MONGODB_AI_KEY"],
)


async def _embed(text: str) -> list[float]:
    r = await oai.embeddings.create(model="voyage-4-large", input=text)
    v = r.data[0].embedding
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v] if n > 0 else v


@function_tool
async def find_similar_jobs(
    query: str,
    borough: str | None = None,
    service_type: str | None = None,
    k: int = 5,
) -> list[dict]:
    """Find prior locksmith jobs similar to the current one to ground the LLM's reasoning."""
    qvec = await _embed(query)

    pre_filter: dict = {"schema_version": "v1"}
    if borough:
        pre_filter["borough"] = borough
    if service_type:
        pre_filter["service_type"] = service_type

    pipeline = [
        {"$vectorSearch": {
            "index": "case_studies_vec",
            "queryVector": qvec,
            "path": "embedding",
            "numCandidates": max(100, k * 20),
            "limit": k,
            "filter": pre_filter,
        }},
        {"$project": {
            "_id": 1, "title": 1, "summary": 1, "service_type": 1,
            "borough": 1, "postcode_outward": 1, "completed_at": 1, "outcome": 1,
            "score": {"$meta": "vectorSearchScore"},
        }},
    ]
    return [d async for d in case_studies.aggregate(pipeline)]
```

`numCandidates` rule of thumb from Atlas docs: at least 20× `limit`.

---

## Stability guarantees from us

- Embedding model & dimensions: **frozen** for the hackathon. We will not change without 24h notice.
- Field path `embedding`: frozen.
- Vector index name `case_studies_vec`: frozen.
- We MAY add new top-level fields to docs — your projection should not be schema-strict on read.
- We MAY add additional indexes; they will not conflict with `case_studies_vec`.
- `schema_version: "v1"` on every doc; bumped only on breaking change. Filter on it if you want strict version pinning.

---

## `case_study_candidate` ingest — Option C contract (Wild Coral → us)

After your `trader_verdict` approves a job, POST the candidate to us. We validate (PII rules from Gabik's `case-studies-handoff.md` §5), embed via the contract above, insert into `case_studies`. Atlas Change Stream then fires our research loop.

```http
POST {AUTORESEARCH_API}/v1/ingest/case-study
x-api-key: <shared-secret>
Content-Type: application/json
```

```jsonc
{
  "source": "live_debrief",                     // your traders capture live
  "source_job_id": "rsl_2025_01_17_e8_0042",
  "partner": "rslockandsafe",
  "page_id": "areas/east-london",
  "borough": "Hackney",
  "postcode_outward": "E8",
  "service_type": "lock_change",
  "service_tag": "Move-in security",
  "completed_at": "2026-02-14T22:11:00Z",
  "duration_minutes": 65,
  "price_band": "150-300",                      // optional; we keep INTERNAL
  "problem": "...",
  "solution": "...",
  "outcome": "success",
  "title": "Full lock change on a new flat in Clapton",
  "summary": "...",                             // 40–80 words, "we" voice
  "street": "Elderfield Road",
  "customer_feedback": { "quote": "...", "rating": 5 } | null
}
```

Response:

```http
202 Accepted
{ "id": "cs_live_debrief_rsl_2025_01_17_e8_0042", "status": "queued_for_embedding" }
```

- **Idempotency** on `(partner, source_job_id)` — same payload returns the same `id`, no duplicates created.
- **Validation** is hard: § 5 PII regexes (no `£/$/EUR/+vat`, no `@`, no UK mobile, summary 40–80 words, postcode outward only, street not starting with a digit) reject with `422` and the failing field.
- **Embedding** is async on our side (`status: queued_for_embedding`). Doc becomes queryable via `case_studies_vec` once embedding completes (~1–2s).

---

## Open from your side (please confirm)

1. **`_id` convention** — we mint `cs_<source>_<source_job_id>` and you treat it as opaque. OK?
2. ~~`MONGODB_AI_KEY` sharing~~ — done; your `al-...` is in our `.env` and used for T3.5 hydration (366 historicals embedded).
3. **Re your "small things"**:
    - We use `MONGODB_URI` / `MONGODB_DB` (your convention). Updated on our side.
    - Yes, going Option A becomes Option C — you do NOT implement `/v1/ingest/debrief`; we drop that endpoint. The Wild Coral → us hand-off is the `case_study_candidate` POST above.
    - Project name: **AutoResearch** (you can cite as "AutoResearch by alexm"). Happy to combine into a single demo writeup.

---

## What this doc supersedes

- `docs/powersync-integration.md` — DEFERRED, misaligned handoff.
- The previously-discussed `POST /v1/ingest/debrief` (raw transcript ingestion) — DROPPED. You handle voice end-to-end inside Wild Coral.

---

## Reference

- `$vectorSearch` aggregation stage: https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-stage/
- Atlas Vector Search overview: https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/
- M0 free tier supports vector search since 2024; 3 search-index limit per cluster (we use one).

— alexm
