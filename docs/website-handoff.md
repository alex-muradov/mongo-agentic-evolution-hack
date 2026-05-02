# AutoResearch ↔ Wild Coral website — case_studies render handoff

**From**: alexm (AutoResearch)
**To**: Gabik (Wild Coral website / Next.js)
**Date**: 2026-05-02
**Scope**: read-surface ONLY — your Next.js fetches `case_studies` from us, renders the "Recent Jobs in {area}" section.

The mobile-side / Wild Coral integration (RAG via `$vectorSearch`, live `case_study_candidate` POST after `trader_verdict`) is **parked for the demo**. See `docs/rag-direct-access.md` and `docs/wildcoral-integration.md` for the parked spec; both still apply contract-wise if/when mobile is revived.

---

## TL;DR

- **366 historical case_studies are live in Atlas** (6 months of real RS Lock jobs, LLM-rewritten "we" voice, §5-validated, Voyage-embedded). Vector index `case_studies_vec` ready+queryable.
- Your Next.js calls `GET {INGEST_URL}/v1/case-studies?area=…&variant=…` with `x-api-key`. Response shape matches `CaseStudyPublic` from your original `case-studies-handoff.md` §1 — no frontend changes needed.
- **Cache invalidation**: we POST to your `/api/revalidate?tag=case-studies:{slug}` when our agent writes new variants (T6). Your existing ISR `revalidate: 600` catches everything else.

---

## 1. Live state

| Asset | Status | Where |
|---|---|---|
| Atlas cluster | up, M0, EU | shared with `fieldcraft` (Option A confirmed) |
| `agentic_evolution.case_studies` | 366 docs, all with embedding (1024 dims, Voyage `voyage-4-large`, unit-norm) | inserted via `scripts/hydrate_historical.py` |
| Vector index `case_studies_vec` | `READY`, `queryable=true` | M0 search-index slot 1 of 3 |
| `INGEST_URL` | quick tunnel | see §3 caveat |
| `INGEST_API_KEY` | live | value in `docs/credentials-for-gabik.md` (env: `CASE_STUDIES_API_KEY`) |

### Distribution
- `areas/east-london`: ~80 docs (Hackney / Tower Hamlets / Newham / Waltham Forest / Redbridge boroughs)
- `areas/london-other`: ~150 docs (Islington, Haringey, Camden, etc — for future Area Hubs)
- `areas/uk-other`: ~136 docs (postcodes outside London — IG, SS, RG, MK, CM)

### Service-type mix (top 4)
- `lock_change` ~60%
- `upvc_repair` ~15%
- `security_audit` ~10%
- `emergency_lockout` ~10%

Real-data flavor: median price band £80–150, median duration 120 min, real London streets surfaced anonymised in `street`, postcodes outward-only.

---

## 2. HTTP contract — `GET /v1/case-studies`

```http
GET {INGEST_URL}/v1/case-studies?area={slug}&variant={A|B|control}
x-api-key: {INGEST_API_KEY}
```

| Param | Required | Note |
|---|---|---|
| `area` | yes | slug, e.g. `east-london`. Maps to `page_id = "areas/{area}"` server-side. |
| `variant` | no | `A` / `B` / `control`. If absent → returns docs where `variant` is null/missing only (default control). |

### Response

```http
200 OK
Content-Type: application/json

{
  "items": [
    {
      "id": "cs_historical_firebase_<firebase_id>",
      "variant": null,
      "postcode": "E8",
      "street": "Elderfield Road",
      "serviceTag": "Move-in security",
      "title": "Lock change for new tenants in Hackney",
      "summary": "We attended a flat in Hackney where new tenants had collected the keys and asked for everything changed before moving in. We replaced the euro cylinder, rim cylinder, and a five-lever BS deadbolt on the front door in a single visit, and supplied a fresh set of keys to take away."
    },
    ...
  ]
}
```

Field shape is **exactly** your `CaseStudy` interface from `case-studies-handoff.md` §1 — `id, variant?, postcode, street, serviceTag, title, summary`. We strip everything else server-side (no `borough`, no `service_type`, no `price_band`, no `embedding` leaks).

### Errors

| Code | When | Action |
|---|---|---|
| `200 { items: [] }` | area unknown / no case_studies match | render empty state on your side |
| `401` | bad/missing `x-api-key` | re-check value in `docs/credentials-for-gabik.md` |
| `5xx / timeout` | tunnel down or our backend restarting | your existing ISR fallback (`revalidate: 600`) hides the blip; if `fetch` fails, return `[]` and log |

We hard-cap the response at 20 docs per request — well above what any single Area Hub will render.

---

## 3. `INGEST_URL` caveat (quick-tunnel mode)

Currently: **`https://lots-joan-motorcycles-acdbentity.trycloudflare.com`**

The named tunnel was supposed to land on `autoresearch.alexmdev.xyz`, but that domain isn't actually registered with the registrar — the zone exists in alexm's CF account from years ago without ever being purchased. Falling back to a quick tunnel until that's resolved.

**For your code:**
```ts
// data/area-case-studies.ts
const url = `${process.env.CASE_STUDIES_API}/v1/case-studies?area=${slug}` +
            (variant ? `&variant=${variant}` : '')
```

Read `CASE_STUDIES_API` from env, **never** hard-code. When alexm restarts cloudflared (sleep, network change), the URL rotates — alexm pings via Telegram with the new value. Update your Vercel env var, redeploy.

If this becomes painful for you in the demo run-up, ping me — I'll register the domain (5 min, ~$2/yr) and switch to a named tunnel for stable hostname.

---

## 4. Drop-in replacement for `getAreaCaseStudies()`

Your original handoff (`case-studies-handoff.md` §2) already has the right shape — just swap the body:

```ts
// data/area-case-studies.ts
export interface CaseStudy {
  id?: string
  variant?: string
  postcode: string
  street: string
  serviceTag: string
  title: string
  summary: string
}

export async function getAreaCaseStudies(
  slug: string,
  variant?: string,
): Promise<CaseStudy[]> {
  const url = new URL(`${process.env.CASE_STUDIES_API}/v1/case-studies`)
  url.searchParams.set("area", slug)
  if (variant) url.searchParams.set("variant", variant)

  const res = await fetch(url, {
    headers: { "x-api-key": process.env.CASE_STUDIES_API_KEY ?? "" },
    next: { revalidate: 600, tags: [`case-studies:${slug}`] },
  })
  if (!res.ok) return []
  const json = (await res.json()) as { items: CaseStudy[] }
  return json.items
}
```

`components/area/case-studies-section.tsx` and `app/areas/[area]/page.tsx` stay untouched — same async-server-component contract.

---

## 5. Variant routing — unchanged from your plan

Your `case-studies-handoff.md` §6.3 server-side PostHog flag evaluation is exactly how we want it. Recap of the flow we expect during demo:

```ts
// app/areas/[area]/page.tsx
import { getPostHogServer } from "@/lib/posthog-server"
import { getOrCreateDistinctId } from "@/lib/distinct-id"

const { distinctId } = await getOrCreateDistinctId()
const variant = await getPostHogServer().getFeatureFlag("case_studies_v1", distinctId)

// IMPORTANT — fire exposure event for Experiment scoring (server-side eval doesn't auto-fire)
if (typeof variant === "string") {
  posthog.capture({ distinctId, event: "$feature_flag_called", properties: {
    $feature_flag: "case_studies_v1", $feature_flag_response: variant,
  }})
}

const caseStudies = await getAreaCaseStudies(
  areaSlug,
  typeof variant === "string" ? variant : undefined,
)
```

**Today**: `case_studies_v1` flag has no rollout — every visitor gets `undefined` → our API returns the default control set.

**T6 (our side)**: when our dispatcher node fires an experiment, it (a) tags ~5 case_studies with `variant: "A"` and 5 with `variant: "B"` directly in Mongo, and (b) configures the PostHog flag rollout (50/50). Your code is already correct for this — no change.

---

## 6. Cache invalidation contract

When the dispatcher tags new variants in Mongo (T6), we hit your revalidate route:

```http
POST {YOUR_NEXTJS_URL}/api/revalidate?tag=case-studies:east-london
x-revalidate-secret: <shared — value in docs/credentials-for-gabik.md>
```

Your route handler (already in your handoff §6.4 plan):

```ts
// app/api/revalidate/route.ts
export async function POST(request: Request) {
  const url = new URL(request.url)
  if (request.headers.get("x-revalidate-secret") !== process.env.REVALIDATE_SECRET) {
    return new Response("forbidden", { status: 403 })
  }
  const tag = url.searchParams.get("tag")
  if (tag) revalidateTag(tag)
  return new Response("ok")
}
```

**Status**: `REVALIDATE_SECRET` value is already published in `docs/credentials-for-gabik.md`. Until your route is deployed, your ISR `revalidate: 600` (10-minute) cache is sufficient for demo timing — we'll tune to 60s right before the demo if needed.

---

## 7. Smoke test from your side

```bash
export INGEST_URL="https://lots-joan-motorcycles-acdbentity.trycloudflare.com"
export INGEST_API_KEY="<value from docs/credentials-for-gabik.md>"

# 1. Health
curl -s $INGEST_URL/healthz
# {"ok":true}

# 2. Default (control) — should return ~20 docs
curl -s -H "x-api-key: $INGEST_API_KEY" \
  "$INGEST_URL/v1/case-studies?area=east-london" | jq '.items | length'

# 3. Sample doc shape
curl -s -H "x-api-key: $INGEST_API_KEY" \
  "$INGEST_URL/v1/case-studies?area=east-london" | jq '.items[0]'

# 4. Variant=A — same default set today (no variants tagged yet); after T6 will diverge
curl -s -H "x-api-key: $INGEST_API_KEY" \
  "$INGEST_URL/v1/case-studies?area=east-london&variant=A" | jq '.items | length'
```

Expected `.items[0]` shape:
```json
{
  "id": "cs_historical_firebase_NfXvOGNEtaBcnEZ3k9eU",
  "variant": null,
  "postcode": "E8",
  "street": "Elderfield Road",
  "serviceTag": "Move-in security",
  "title": "Lock change for new tenants in Hackney",
  "summary": "We attended a flat in Hackney…"
}
```

---

## 8. Things you do NOT need from us for site integration

- ❌ Mongo connection string — your Next.js never talks to Mongo directly
- ❌ Vector index access — that's RAG-side (your LiveKit agent), not website
- ❌ `MONGODB_AI_KEY` — embeddings are our concern; you just consume rendered text
- ❌ Atlas users — site integration is HTTP-only

If the mobile/Wild Coral side comes back into scope, those are needed; that's the parked `wildcoral-integration.md` runbook.

---

## 9. What we still owe you

- [x] `REVALIDATE_SECRET` published — see `docs/credentials-for-gabik.md` (already in our `.env`, ready when you wire §6)
- [ ] Stable hostname — I'll register `alexmdev.xyz` if quick-tunnel rotation bites
- [ ] T6 dispatcher live — tagging variants in Mongo + driving PostHog flag rollout. Until that lands, every fetch returns default control set.

---

## 10. Cross-reference

- `docs/rag-direct-access.md` — schema source-of-truth + mobile RAG contract (parked)
- `docs/wildcoral-integration.md` — mobile-side runbook (parked)
- `docs/powersync-integration.md` — DEFERRED, retained as design ref only
- Your `case-studies-handoff.md` — original frontend contract, still authoritative for §1 shape + §5 PII rules + §6 PostHog setup
