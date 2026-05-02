# Case Studies Section — Backend Handoff

A new "Recent Jobs in {area}" section is rendered on the Area Hub template
(`/areas/[area]`). Currently it pulls from a static TS file. This doc is the
contract the backend should implement so case studies can be served from an API
/ CMS and A/B-tested.

Live preview (frontend): `/areas/east-london`

---

## 1. Data Contract

```ts
// data/area-case-studies.ts
export interface CaseStudy {
  id?: string         // stable id — required from backend (for A/B reporting)
  variant?: string    // A/B bucket key: "A" | "B" | "control". Omitted = default.
  postcode: string    // outward code ONLY, e.g. "E5". NEVER include inward part.
  street: string      // street name without house number, e.g. "Elderfield Road"
  serviceTag: string  // short badge label, max ~24 chars, e.g. "Move-in security"
  title: string       // <= 70 chars, sentence case, no brand mentions
  summary: string     // 2–3 sentences, first-person plural ("we"), 40–80 words
}
```

### Sample JSON payload (response shape)

```json
{
  "area": "east-london",
  "variant": "A",
  "items": [
    {
      "id": "cs_e5_elderfield_movein_2025",
      "variant": "A",
      "postcode": "E5",
      "street": "Elderfield Road",
      "serviceTag": "Move-in security",
      "title": "Full lock change on a new flat in Clapton",
      "summary": "New tenants had just picked up the keys and wanted everything changed before they moved in — a referral from a neighbour up the street. We replaced the euro cylinder, rim cylinder, and 5-lever BS deadlock on the front entrance door in a single visit, with a fresh set of keys to take away."
    }
  ]
}
```

### Hard rules (frontend will reject / legal & SEO)

1. **No house numbers** — street name only.
2. **No full postcode** — outward code (`E5`) only, never `E5 0LE`.
3. **No customer names** — anonymise. Roles ok ("a property manager").
4. **No prices, no revenue, no avg job values** — bans match `CONTENT_STYLE_GUIDE.md`.
5. **Voice**: first-person plural ("we"), past tense, no marketing superlatives.
6. **Length**: summary 40–80 words; title <= 70 chars; serviceTag <= 24 chars.

If your CMS gives editors free-text fields, enforce these in validation, not at
render time.

---

## 2. Integration Points

To swap the static source for a backend, you only touch one function — the
component and page contract stay the same.

```
data/area-case-studies.ts          ← REPLACE getAreaCaseStudies() body
components/area/case-studies-section.tsx   ← do not change (pure render)
app/areas/[area]/page.tsx          ← already calls getAreaCaseStudies(slug, variant)
```

### Current implementation

```ts
// data/area-case-studies.ts
export function getAreaCaseStudies(slug: string, variant?: string): CaseStudy[] {
  const all = AREA_CASE_STUDIES[slug] ?? []
  if (!variant) return all.filter((cs) => !cs.variant)
  return all.filter((cs) => cs.variant === variant || !cs.variant)
}
```

### Backend-driven implementation (drop-in replacement)

```ts
export async function getAreaCaseStudies(
  slug: string,
  variant?: string,
): Promise<CaseStudy[]> {
  const url = new URL(`${process.env.CASE_STUDIES_API}/case-studies`)
  url.searchParams.set("area", slug)
  if (variant) url.searchParams.set("variant", variant)

  const res = await fetch(url, {
    next: { revalidate: 600 }, // ISR: 10 min cache, tune to your edit cadence
  })
  if (!res.ok) return []
  const json = (await res.json()) as { items: CaseStudy[] }
  return json.items
}
```

Note: if you make this `async`, also `await` it in
`app/areas/[area]/page.tsx` (the page is already an `async` server component, so
this is a one-line change).

---

## 3. A/B Testing Pattern

> **Chosen stack: PostHog (flags + events) + MongoDB (content).** Full wiring
> in §6. This section just lists the options for context.

The page is a Server Component. Pick the variant **on the server** before render
so each user sees a stable variant on first paint (no client flicker). Three
common sources, in order of preference:

### Option A — Cookie set by edge middleware (recommended)

`middleware.ts` (Next.js) assigns a sticky bucket to each visitor:

```ts
import { NextRequest, NextResponse } from "next/server"

export function middleware(req: NextRequest) {
  const res = NextResponse.next()
  if (!req.cookies.get("ab_case_studies")) {
    const bucket = Math.random() < 0.5 ? "A" : "B"
    res.cookies.set("ab_case_studies", bucket, {
      path: "/",
      maxAge: 60 * 60 * 24 * 30, // 30 days
    })
  }
  return res
}

export const config = { matcher: "/areas/:path*" }
```

Then in the page:

```ts
import { cookies } from "next/headers"

const variant = (await cookies()).get("ab_case_studies")?.value
const caseStudies = await getAreaCaseStudies(areaSlug, variant)
```

Pros: sticky per visitor, server-rendered, works with full-page caching keyed on
the cookie. Cons: needs middleware.

### Option B — Feature flag service (GrowthBook / Statsig / LaunchDarkly)

```ts
const variant = await flags.getVariant("case_studies_v1", { userId })
const caseStudies = await getAreaCaseStudies(areaSlug, variant)
```

Pros: targeting rules, eventing, gradual rollouts out of the box. Cons: extra
dependency + budget; flag SDK call adds latency unless cached.

### Option C — Query string (for manual QA only, do NOT ship as the prod splitter)

```ts
const variant = searchParams.variant
```

Useful for letting QA / stakeholders preview each variant
(`?variant=A`, `?variant=B`). Don't use this as the bucketing mechanism in prod
— it's not sticky and it's gameable.

### Tracking impressions & conversions

The component currently doesn't fire events. Add a small client-side wrapper if
you need impression tracking:

```tsx
// components/area/case-study-impression.tsx ("use client")
useEffect(() => {
  window.gtag?.("event", "case_study_view", {
    case_study_id: id,
    variant,
    area: areaSlug,
  })
}, [])
```

Conversion event = phone click / callback form submit, joined to variant via the
cookie. Both `phone-link.tsx` and the callback API already fire gtag events;
attach `variant` to the payload.

---

## 4. Where the data lives

| Concern            | Today (static)                         | After backend integration                    |
| ------------------ | -------------------------------------- | -------------------------------------------- |
| Source of truth    | `data/area-case-studies.ts`            | API / CMS (your choice)                      |
| Editor workflow    | git PR                                 | CMS UI                                       |
| Validation         | TypeScript                             | Server-side schema (Zod recommended)         |
| Caching            | Build-time                             | ISR (`revalidate: 600`) or on-demand purge   |
| A/B variants       | `variant?` field on each item          | Same — backend filters by `?variant=` query  |
| Localisation       | n/a                                    | Add `locale` field if you go multi-lang      |

---

## 5. Constraints checklist for the backend / CMS

When wiring up the API, validate every payload:

- [ ] `postcode` matches `/^E[A-Z0-9]{1,3}$|^[A-Z]{1,2}[0-9]{1,2}$/` (outward code)
- [ ] `street` does NOT start with a digit
- [ ] `summary` does not contain `£`, `$`, `EUR`, `+ vat`, or numeric-only price tokens (`\b\d+(\.\d+)?\b\s*(quid|pounds)?`)
- [ ] `summary` does not contain `@` (email), or UK phone patterns (`\b07\d{9}\b`)
- [ ] `serviceTag.length <= 24`
- [ ] `title.length <= 70`
- [ ] `summary` word count between 40 and 80
- [ ] `variant`, if present, is one of an allowed enum

Reject on validation failure with a 400 + field error so editors get fast
feedback in the CMS.

---

## 6. PostHog + MongoDB integration (chosen stack)

**Architecture**

```
  ┌──────────────────┐    write     ┌──────────────────┐
  │  Custom agents   │─────────────▶│     MongoDB      │  source of truth
  │  (yours)         │              │  case_studies    │  for case-study text +
  └─────────▲────────┘              └─────────┬────────┘  variant labels
            │ read metrics                    │ read
            │ via PostHog API                 ▼
  ┌─────────┴────────┐              ┌──────────────────┐
  │     PostHog      │◀─────events──│   Next.js app    │  this repo
  │  flags + events  │──flag eval──▶│  (server + browser)
  └──────────────────┘              └──────────────────┘
```

- **MongoDB** stores case studies and which `variant` each belongs to. Agents
  generate / curate / retire studies there.
- **PostHog** owns the experiment: which user sees which `variant` (multivariate
  feature flag), and the impression / conversion events used to score it.
- **Next.js** asks PostHog for the variant on the server, then asks Mongo (via
  your API) for the case studies tagged with that variant.

### 6.1 Env vars

```env
POSTHOG_KEY=phc_xxx                   # personal/project API key (server)
POSTHOG_HOST=https://eu.i.posthog.com # or your self-hosted host
NEXT_PUBLIC_POSTHOG_KEY=phc_xxx       # public key (client SDK)
NEXT_PUBLIC_POSTHOG_HOST=https://eu.i.posthog.com
CASE_STUDIES_API=https://api.your-backend.example/v1
```

### 6.2 PostHog feature flag — naming convention

| Field        | Value                                                    |
| ------------ | -------------------------------------------------------- |
| Flag key     | `case_studies_v1`                                        |
| Type         | Multivariate                                             |
| Variants     | `control`, `A`, `B` (extend as needed)                   |
| Rollout      | 100% to flag, then split per variant (e.g. 50/50/0)      |
| Experiment   | "Case studies copy v1" — primary goal: `phone_click`     |

The variant string returned by PostHog (`"A"`, `"B"`, …) is what we pass into
`getAreaCaseStudies(slug, variant)` and into your Mongo API as `?variant=`.

### 6.3 Server-side flag evaluation (Next.js Server Component)

Use `posthog-node` so the variant is decided before render — no client flicker,
caches well.

```bash
npm i posthog-node posthog-js
```

```ts
// lib/posthog-server.ts
import { PostHog } from "posthog-node"

let client: PostHog | null = null
export function getPostHogServer() {
  if (!client) {
    client = new PostHog(process.env.POSTHOG_KEY!, {
      host: process.env.POSTHOG_HOST,
      flushAt: 1,           // tiny app, ship events immediately
      flushInterval: 0,
    })
  }
  return client
}
```

```ts
// lib/distinct-id.ts
import { cookies } from "next/headers"
import { randomUUID } from "crypto"

const COOKIE = "ph_did"
const ONE_YEAR = 60 * 60 * 24 * 365

export async function getOrCreateDistinctId(): Promise<{
  distinctId: string
  isNew: boolean
}> {
  const jar = await cookies()
  const existing = jar.get(COOKIE)?.value
  if (existing) return { distinctId: existing, isNew: false }

  const distinctId = randomUUID()
  jar.set(COOKIE, distinctId, {
    path: "/",
    maxAge: ONE_YEAR,
    sameSite: "lax",
    httpOnly: false, // client SDK needs to read it to stay aligned
  })
  return { distinctId, isNew: true }
}
```

> Setting cookies from a Server Component requires `next.config` to allow it OR
> you set the cookie from middleware (preferred — see §6.6).

```ts
// app/areas/[area]/page.tsx — variant lookup
import { getPostHogServer } from "@/lib/posthog-server"
import { getOrCreateDistinctId } from "@/lib/distinct-id"

const { distinctId } = await getOrCreateDistinctId()
const posthog = getPostHogServer()

const variant = (await posthog.getFeatureFlag("case_studies_v1", distinctId)) as
  | string
  | boolean
  | undefined

// IMPORTANT: PostHog Experiments need an exposure event. Server-side eval
// does NOT auto-fire $feature_flag_called — capture it ourselves.
if (typeof variant === "string") {
  posthog.capture({
    distinctId,
    event: "$feature_flag_called",
    properties: {
      $feature_flag: "case_studies_v1",
      $feature_flag_response: variant,
    },
  })
}

const caseStudies = await getAreaCaseStudies(
  areaSlug,
  typeof variant === "string" ? variant : undefined,
)
```

### 6.4 MongoDB-backed `getAreaCaseStudies`

Replace the static body in `data/area-case-studies.ts`:

```ts
export async function getAreaCaseStudies(
  slug: string,
  variant?: string,
): Promise<CaseStudy[]> {
  const url = new URL(`${process.env.CASE_STUDIES_API}/case-studies`)
  url.searchParams.set("area", slug)
  if (variant) url.searchParams.set("variant", variant)

  const res = await fetch(url, {
    next: { revalidate: 600, tags: [`case-studies:${slug}`] },
    headers: { "x-api-key": process.env.CASE_STUDIES_API_KEY ?? "" },
  })
  if (!res.ok) return []
  const json = (await res.json()) as { items: CaseStudy[] }
  return json.items
}
```

The Mongo-side handler should:

1. Look up `case_studies` where `area = slug` AND
   (`variant = ?variant` OR `variant` is null/missing).
2. Run the validation rules from §1 + §5 before serving.
3. Return shape `{ items: CaseStudy[] }`.

Use `revalidateTag('case-studies:east-london')` from your CMS / agent webhook
when content changes, so editors see updates without redeploy.

### 6.5 Client-side tracking (`posthog-js`)

Mount the SDK once in a client provider so events fire from the browser. Init
is **consent-gated** — the existing cookie banner (`components/cookie-consent.tsx`,
localStorage key `cookie-consent`) controls whether PostHog loads with full
features (events + Session Replay) or stays in opt-out mode. Session Replay
config in §6.9.

```tsx
// app/providers.tsx ("use client")
"use client"
import posthog from "posthog-js"
import { PostHogProvider } from "posthog-js/react"
import { useEffect, useState } from "react"

export function PHProvider({
  distinctId,
  children,
}: {
  distinctId: string
  children: React.ReactNode
}) {
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const consent = localStorage.getItem("cookie-consent")
    if (consent !== "accepted") {
      // Decline / not yet decided — don't init. (Or init with disabled
      // recording & autocapture if you want anonymous flag eval only.)
      setReady(true)
      return
    }
    if (!posthog.__loaded) {
      posthog.init(process.env.NEXT_PUBLIC_POSTHOG_KEY!, {
        api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST,
        bootstrap: { distinctID: distinctId }, // align with server eval
        capture_pageview: "history_change",
        // Session Replay — see §6.9 for full privacy config
        session_recording: {
          maskAllInputs: true,
          maskTextSelector: "[data-ph-mask]",
          blockSelector: "[data-ph-no-capture]",
        },
        autocapture: { dom_event_allowlist: ["click", "submit"] },
      })
    }
    setReady(true)
  }, [distinctId])

  if (!ready) return <>{children}</> // render unblocked
  return <PostHogProvider client={posthog}>{children}</PostHogProvider>
}
```

Wrap the tree in `app/layout.tsx`, passing the same `distinctId` you read in
the page so server and client agree on the user.

When the user clicks "Accept All" in the cookie banner, call
`posthog.opt_in_capturing()` so the SDK starts recording without a page
reload. Add this to the existing `handleAccept` in
`components/cookie-consent.tsx`. Mirror it with `posthog.opt_out_capturing()`
on `handleDecline`.

### 6.6 Middleware (sticky distinct_id, no flicker)

Cleaner than setting the cookie from inside Server Components:

```ts
// middleware.ts
import { NextRequest, NextResponse } from "next/server"

export function middleware(req: NextRequest) {
  const res = NextResponse.next()
  if (!req.cookies.get("ph_did")) {
    res.cookies.set("ph_did", crypto.randomUUID(), {
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
      sameSite: "lax",
    })
  }
  return res
}

export const config = { matcher: "/:path*" }
```

### 6.7 Event taxonomy (lock this in before shipping)

Send these from the client. Every event MUST carry the variant so agents on
Mongo can score by bucket via the PostHog API.

| Event name                  | When                                      | Required props                                                         |
| --------------------------- | ----------------------------------------- | ---------------------------------------------------------------------- |
| `case_study_impression`     | Card enters viewport (IntersectionObs.)   | `case_study_id`, `variant`, `area`, `position` (0-based index)         |
| `case_study_click`          | User clicks anywhere inside a card        | `case_study_id`, `variant`, `area`, `position`                          |
| `phone_click`               | Tel: link tap (already exists — extend it) | add: `variant`, `surface` (`"case_studies"` if click came from card)   |
| `callback_form_submit`      | Form success                              | add: `variant`, `surface`                                                |
| `$feature_flag_called`      | Auto from server-side capture (see §6.3) | (PostHog handles)                                                       |

Conversion in the PostHog Experiment = `phone_click` OR `callback_form_submit`,
filtered by `surface = "case_studies"` for direct-attribution view, plus an
overall view including all phone clicks on the page for indirect-attribution.

### 6.8 Component changes for impression tracking

Convert the card to a thin client wrapper:

```tsx
// components/area/case-study-card.tsx — "use client"
"use client"
import { useEffect, useRef } from "react"
import posthog from "posthog-js"
import type { CaseStudy } from "@/data/area-case-studies"

export function CaseStudyCard({
  cs, area, variant, position,
}: { cs: CaseStudy; area: string; variant?: string; position: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        posthog.capture("case_study_impression", {
          case_study_id: cs.id,
          variant,
          area,
          position,
        })
        obs.disconnect()
      }
    }, { threshold: 0.5 })
    obs.observe(ref.current)
    return () => obs.disconnect()
  }, [cs.id, variant, area, position])

  const onClick = () => {
    posthog.capture("case_study_click", {
      case_study_id: cs.id, variant, area, position,
    })
  }

  return (
    <div ref={ref} onClick={onClick}>
      {/* existing card markup */}
    </div>
  )
}
```

`case-studies-section.tsx` stays a Server Component and just maps cards.

### 6.9 Session Replay — privacy, consent, sampling

Replay is enabled in §6.5. Three things must be locked down before it ships.

#### Consent gate (UK GDPR)

Replay = personal data. You can only record visitors who opted in. The cookie
banner in `components/cookie-consent.tsx` already controls this — the SDK init
in §6.5 reads `localStorage.cookie-consent === "accepted"` and skips init
otherwise.

**TODO (legal copy)** — must update before shipping:

- `components/cookie-consent.tsx` banner currently mentions only "Google
  Analytics". Add: "session replay (PostHog) which records on-page
  interactions". Wording should make it clear that form inputs are masked.
- `app/privacy-policy/page.tsx` — add a section listing PostHog as a
  processor, what's recorded (anonymous interactions, masked inputs), retention
  period, and the opt-out mechanism.

#### PII masking — what to hide

The site has two forms with personal data and one component that auto-fills
real numbers. All MUST be masked from replay:

| Surface                                              | Marker to add                                              |
| ---------------------------------------------------- | ---------------------------------------------------------- |
| `components/request-callback-form.tsx` (name/phone)  | wrap form root with `data-ph-no-capture`                   |
| `components/subcontractor/...` form (full PII)       | wrap form root with `data-ph-no-capture`                   |
| `components/phone-link.tsx` (the business phone)     | safe to record — it's the business's own published number |
| Any future input we add                              | default config (`maskAllInputs: true`) handles it         |
| Free-text fields rendered from data (case study text)| safe — public marketing copy                              |

`data-ph-no-capture` (set in `blockSelector` in §6.5) means the element renders
as a placeholder block in replays — DOM, text and inputs are all hidden. Use
this for whole forms. Use `data-ph-mask` (set in `maskTextSelector`) when you
want the layout visible but the text replaced with `*`.

Audit before shipping:

```bash
# every input must either be inside a [data-ph-no-capture] container OR
# rely on the default maskAllInputs:true. Spot-check the two forms:
grep -RInE "<(input|textarea|select)" components/request-callback-form.tsx \
                                       components/subcontractor/
```

#### Sampling

Replay storage is the expensive bit. Start higher to debug & calibrate, then
turn it down once the experiment has data.

```ts
session_recording: {
  // ...masking config from §6.5...
  // PostHog respects sample_rate at the project level (Settings → Recording).
  // For per-init control, use the older option:
  // recordCrossOriginIframes: false,
}
// Project-level recommendations:
//   experiment phase:  100% sampling, min duration 10s
//   steady state:      10–20% sampling, min duration 15s
//   minimum_duration:  set on PostHog project settings to drop bounces
```

Configure sample rate on the **PostHog project settings page** rather than in
code — that way you can tune without a redeploy.

#### Linking replays to the experiment

Because §6.7 attaches `variant` to every event, PostHog Replays can be
filtered:

> Recordings → filter `event = case_study_impression` AND
> `properties.variant = "A"`

This gives you the exact set of sessions where a user actually saw variant A,
which is what you want for qualitative review of why one variant converts
better than another. Surface the same property on `phone_click` and
`callback_form_submit` so you can also look at sessions that converted.

#### Identity stitching (replays after a callback submit)

If a visitor submits the callback form, you can call:

```ts
posthog.identify(hashedEmail, { ph_did: distinctId })
```

This joins anonymous and identified sessions for the same person, so the
recording before they filled the form is still attached to them. **Don't ship
this without legal sign-off** — see §7 question 5.

### 6.10 Closing the loop with your agents

Agents read PostHog via the
[PostHog Query API](https://posthog.com/docs/api/queries) (HogQL), join
`case_study_*` events to `$feature_flag_called` to get per-variant CTR /
conversion, then write decisions back to Mongo (e.g. retire a B-variant copy,
promote A to control). The Next.js app doesn't need to know any of this — it
keeps fetching by `(slug, variant)` from the API.

---

## 7. Open questions for the backend dev

1. Mongo schema — single `case_studies` collection with `area` + `variant`
   indexes, or split per area? (Recommend single collection, compound index on
   `{ area: 1, variant: 1 }`.)
2. Mongo writes — agents write directly, or do they go through your API for
   validation? (Recommend API gate so §1 + §5 rules are enforced server-side.)
3. PostHog hosting — EU cloud or self-hosted? Affects `POSTHOG_HOST`.
4. Cache invalidation — webhook from your API to a Next.js
   `/api/revalidate?tag=case-studies:east-london` route, triggered when the
   agents update Mongo.
5. Identity stitching — once a visitor submits a callback, do we want to call
   `posthog.identify(emailHash, { ph_did: distinctId })` so future sessions
   join up? (Recommend yes, but only after legal sign-off on the privacy
   notice.)
