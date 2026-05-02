# Wild Coral ↔ AutoResearch — Integration Runbook

**Audience**: Gabik (Wild Coral side).
**Goal**: get Wild Coral reading our case_studies via `$vectorSearch` and writing case_study candidates to our ingest endpoint.

For the *why* (decisions, trade-offs, schemas), see `docs/rag-direct-access.md`. This doc is action-oriented.

---

## 1. The shape of integration

```
   Wild Coral (yours)                    AutoResearch (ours)
   ─────────────────────────             ───────────────────────────────
                                         ┌──────────────────────┐
   LiveKit Agent                         │ FastAPI on laptop    │
   ┌──────────────────┐                  │ exposed via          │
   │ qualification    │ ── trader OK ──▶│ cloudflared tunnel   │
   │ agent            │   POST candidate │                      │
   │                  │                  │ → validate (§5 PII)  │
   │ @function_tool   │                  │ → embed (Voyage)     │
   │ find_similar_*   │                  │ → insert case_studies│
   └────────┬─────────┘                  └──────────┬───────────┘
            │                                       │
            │  $vectorSearch                        │ change stream
            │  (direct motor                        │ triggers
            │   to shared Atlas)                    ▼
            ▼                                ┌──────────────────┐
   ┌──────────────────────────┐              │ AutoResearch     │
   │ Atlas: agentic_evolution │              │ research loop    │
   │ collection case_studies  │◀─────────────│ (proposer →      │
   │ index case_studies_vec   │              │  verdict →       │
   └──────────────────────────┘              │  reflect)        │
                                             └──────────────────┘
```

Two integration surfaces only: **read = direct Mongo `$vectorSearch`**, **write = HTTP POST to our ingest endpoint**.

---

## 2. Credentials & URLs

All values live in `docs/credentials-for-gabik.md` (single authoritative file, hackathon-expedient — no 1Password vault, just inline values shared via Telegram).

| Item | env var on your side | Status |
|---|---|---|
| Atlas cluster connection string (admin) | `MONGODB_URI` | live |
| `MONGODB_AI_KEY` (your `al-...`) | `MONGODB_AI_KEY` | live (your own value) |
| AutoResearch ingest URL (quick-tunnel) | `INGEST_URL` | live, **rotates** on cloudflared restart |
| `INGEST_API_KEY` (shared secret for `x-api-key`) | `INGEST_API_KEY` | live |

Refer to `docs/credentials-for-gabik.md` for actual values.

---

## 3. Setup checklist (your side)

- [ ] OpenAI-python SDK installed (`pip install openai`) — used for embeddings via gateway
- [ ] `motor` installed (`pip install motor`) — for direct `$vectorSearch`
- [ ] `httpx` installed for the ingest POST
- [ ] Pull `MONGODB_URI` from `docs/credentials-for-gabik.md` into your env (note: it's the admin connection string — single user for both DBs, hackathon scope)
- [ ] Pull `autoresearch/MONGODB_AI_KEY` → `MONGODB_AI_KEY` in your env
- [ ] Pull `autoresearch/INGEST_URL` and `INGEST_API_KEY` → use in your `onTraderVerdict` Atlas function
- [ ] Confirm `voyage-4-large` is the model your `embed_one` calls (rebuild `case_studies_vec` is on alexm — no work for you)
- [ ] Smoke `find_similar_jobs` against the cluster (see § 5)

---

## 4. Read path — `find_similar_jobs` LiveKit tool

Full schema + filterable fields: `docs/rag-direct-access.md` § "case_studies document" + § "Vector index definition".

Minimum viable tool:

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
    """Find prior locksmith jobs similar to the current one to ground the agent's reasoning."""
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

Filterable fields available on `case_studies_vec`: `borough`, `postcode_outward`, `service_type`, `completed_at`, `outcome`, `schema_version`. Anything else needs scalar `.find()` post-filter.

---

## 5. Smoke test (read path) — verify before wiring into your agent

```bash
export MONGODB_URI="<value from docs/credentials-for-gabik.md>"
export MONGODB_AI_KEY="<value from docs/credentials-for-gabik.md>"

python - <<'PY'
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI

async def main():
    mongo = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    oai = AsyncOpenAI(base_url="https://ai.mongodb.com/v1", api_key=os.environ["MONGODB_AI_KEY"])
    r = await oai.embeddings.create(model="voyage-4-large", input="emergency lockout euro cylinder")
    v = r.data[0].embedding
    n = sum(x*x for x in v)**0.5
    qv = [x/n for x in v]
    cur = mongo["agentic_evolution"]["case_studies"].aggregate([
        {"$vectorSearch": {"index":"case_studies_vec","queryVector":qv,"path":"embedding","numCandidates":50,"limit":3,"filter":{"schema_version":"v1"}}},
        {"$project": {"_id":1,"title":1,"score":{"$meta":"vectorSearchScore"}}},
    ])
    async for d in cur: print(d)

asyncio.run(main())
PY
```

Expected: empty list until alexm runs T3.5 hydration and ingests historical jobs. After that, top-3 hits with scores in [0.4, 0.9].

---

## 6. Write path — `case_study_candidate` POST

Full payload schema: `docs/rag-direct-access.md` § "case_study_candidate ingest".

In your `onTraderVerdict` Atlas function (TypeScript):

```ts
import { fetch } from "node-fetch"

export default async function onTraderVerdict(arg: { jobId: string }) {
  const job = await context.services.get("mongodb-atlas").db("fieldcraft").collection("jobs").findOne({ _id: arg.jobId })
  if (!job?.trader_verdict?.approved) return

  const candidate = {
    source: "live_debrief",
    source_job_id: job._id.toString(),
    partner: "rslockandsafe",
    page_id: "areas/east-london",
    borough: job.location.borough,
    postcode_outward: job.location.postcode_outward,
    service_type: job.service_type,
    service_tag: job.service_tag,
    completed_at: job.completed_at,
    duration_minutes: job.duration_minutes,
    price_band: job.price_band,
    problem: job.qualification.problem,
    solution: job.qualification.solution,
    outcome: job.qualification.outcome,
    title: job.qualification.title,
    summary: job.qualification.summary,
    street: job.location.street,
    customer_feedback: job.feedback ?? null,
  }

  const r = await fetch(`${context.values.get("INGEST_URL")}/v1/ingest/case-study`, {
    method: "POST",
    headers: {
      "x-api-key": context.values.get("INGEST_API_KEY"),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(candidate),
  })

  if (!r.ok) {
    if (r.status === 422) {
      const err = await r.json()
      console.error(`AutoResearch rejected candidate (validation): ${JSON.stringify(err)}`)
      // do NOT retry — this is a content rule violation, fix in your qualification step
    } else if (r.status >= 500) {
      throw new Error(`AutoResearch ingest 5xx: ${r.status}`) // Atlas Functions will retry
    } else {
      console.error(`AutoResearch ingest unexpected: ${r.status}`)
    }
    return
  }

  const ack = await r.json()
  // ack.status: "queued_for_embedding" | "duplicate"
  console.log(`AutoResearch ack: ${ack.id} ${ack.status}`)
}
```

---

## 7. Failure modes & retry policy

| Status | Meaning | What you do |
|---|---|---|
| `202 { status: "queued_for_embedding" }` | Stored, embedded async (or skipped if our Voyage key was missing) | Done |
| `202 { status: "duplicate" }` | Same `(source, source_job_id)` already exists — idempotent no-op | Done |
| `401` | Bad/missing `x-api-key` | Re-check value in `docs/credentials-for-gabik.md`; do not retry blindly |
| `422 { detail: [...] }` | Validation failed (PII regex, length, postcode shape, etc.) | **Do not retry** — fix in your qualification step. Inspect `detail` for the field |
| `5xx` | Our service / tunnel down | Retry with exponential backoff (Atlas Functions does this for free if you `throw`) |
| Timeout | Tunnel cold or our backend restarting | Retry; alexm gets a heads-up if we're rotating cloudflared |

Idempotency is on `(source, source_job_id)`. Safe to retry the exact same payload — you'll either get `queued_for_embedding` (first time) or `duplicate` (subsequent).

---

## 8. What we still owe you

- [ ] Atlas cluster provisioned (alexm — pending)
- [x] Atlas cluster live; admin user (single, both DBs) — value in `docs/credentials-for-gabik.md`
- [x] cloudflared quick-tunnel up — `INGEST_URL` in `docs/credentials-for-gabik.md` (rotates on restart, alexm pings)
- [x] `INGEST_API_KEY` live — value in `docs/credentials-for-gabik.md`
- [ ] Historical case_studies hydrated (alexm — T3.5; needed before your `find_similar_jobs` returns useful hits)

I'll ping you in our chat as each lands. You can wire the code now against env-var placeholders; smoke tests in § 5 will only return data once T3.5 completes.

---

## 9. What we need from you

- ~~`MONGODB_AI_KEY`~~ — already shared, T3.5 hydration completed against your `al-...` key (366 historicals embedded)
- Confirm `_id` convention `cs_<source>_<source_job_id>` is acceptable as opaque on your side
- Confirm you're handling diarization inside Wild Coral (we don't see raw transcripts, so we don't care which speaker is which — just that `problem` / `solution` / `outcome` come out clean)

---

## 10. Cross-reference

- `docs/rag-direct-access.md` — full design, decision rationale, schema details, vector index definition, complete payload shapes
- `docs/atlas-setup.md` — Atlas provisioning steps (alexm-side, FYI only)
- `docs/powersync-integration.md` — DEFERRED, kept as reference. Disregard for this integration.
