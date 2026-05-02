> **STATUS: DEFERRED — handoff was misaligned.** Gabik is building Wild Coral (voice-capture agent for tradespeople), a separate product, not the mobile HITL for AutoResearch. We use Streamlit on our side for HITL gates A/B/C. The real integration with Wild Coral lives in `docs/rag-direct-access.md`. This doc is kept as design reference if mobile-HITL ever returns to scope.

# PowerSync Integration — HITL Mobile UI

**Audience**: Gabik (mobile app side).
**Goal**: real-time sync of agent runs / hypotheses / verdicts from our MongoDB Atlas to the mobile app's local SQLite via PowerSync, with approve/reject actions going through our HTTPS command endpoints.

PowerSync is the chosen path because **MongoDB Atlas Device Sync was sunset in September 2025**; PowerSync is the modern, supported way to sync Mongo → mobile.

---

## 1. Architecture

```
                     ┌──────────────────────────────────────┐
                     │   Our Python backend (FastAPI)        │
                     │                                       │
                     │   ┌─────────┐     ┌────────────────┐  │
                     │   │ Agent   │────▶│  MongoDB Atlas │  │
                     │   │ runtime │     │  (change       │  │
                     │   │ (LangG) │     │   streams on)  │  │
                     │   └─────────┘     └────────┬───────┘  │
                     │   ┌──────────────┐         │          │
                     │   │ HITL command │ writes  │          │
                     │   │ endpoints    │─────────┘          │
                     │   │ + JWT issuer │                    │
                     │   └──────┬───────┘                    │
                     └──────────┼────────────────┬───────────┘
                                │ JWT             │ change streams
                                │                 ▼
                                │       ┌──────────────────┐
                                │       │ PowerSync service │
                                │       │ (Cloud or self-   │
                                │       │  hosted)          │
                                │       └────────┬──────────┘
                                │                │ realtime sync
                                ▼                ▼
                          ┌─────────────────────────────────┐
                          │  Mobile app (Gabik)              │
                          │                                  │
                          │   ┌──────────┐   ┌────────────┐  │
                          │   │ PS SDK + │◀─▶│ HITL UI    │  │
                          │   │ SQLite   │   │ (approve/  │  │
                          │   │ (local)  │   │  reject)   │  │
                          │   └──────────┘   └─────┬──────┘  │
                          └────────────────────────┼─────────┘
                                                   │
                                                   ▼  HTTPS
                                       (calls our command endpoints
                                        with JWT in Authorization)
```

**Read path** = sync (Mongo → PowerSync → SQLite). UI binds to local SQLite, updates appear in real time.

**Write path** = command (mobile → HTTPS → our FastAPI → Mongo). PowerSync replicates the change back to the client; UI updates from the same local-SQLite subscription as everyone else. **Do not** use PowerSync's generic mutation-upload for HITL actions — approvals carry business logic (state-machine transitions, side effects on the LangGraph run) that must live server-side.

---

## 2. MongoDB source — what we expose

### Atlas cluster requirements

- Replica set (Atlas always provides this, even on M0/M2 — but PowerSync needs a tier with **change streams accessible from external IPs**, so plan for **M10+** in production; M0 works for local/dev with IP allowlist).
- Change streams: enabled by default on replica sets.
- Connection string + read-only user with `readAnyDatabase` on the source DB.
- IP allowlist: add PowerSync Cloud egress IPs (from PowerSync console) or the self-hosted PowerSync server IP. For demo via cloudflared, the PowerSync service still needs direct Mongo TCP — it does **not** go through our tunnel.

We will provide:
- `MONGO_URI` (read-only user)
- `MONGO_DB` (`agentic_evolution`)
- A confirmation that change streams are enabled on the relevant collections.

### Collections you will sync

| Collection         | Direction        | Purpose on mobile                                     |
|--------------------|------------------|--------------------------------------------------------|
| `agent_runs`       | read             | the queue: which runs are awaiting which gate          |
| `hypotheses`       | read             | gate A context: what the agent proposes to test         |
| `experiments`      | read             | gate B context: live experiment state, sample sizes     |
| `verdicts`         | read             | gate C context: draft verdict + reasoning + evidence    |
| `case_studies`     | read (optional)  | what's actually rendered on the page being experimented |
| `evidence_sessions`| read (optional)  | replay summaries the analyst leaned on                  |
| `learnings`        | read (optional)  | reflect-node output, useful for "why now" context        |

All writes happen via §6 command endpoints. **No collection is mobile-writeable through PowerSync.**

---

## 3. Mongo document shapes (canonical)

Pydantic models live in `domain/` of our backend. The fields below are what lands in change-stream payloads — design your SQLite schema (§4) against these.

### `agent_runs`

```jsonc
{
  "_id": "run_2026_05_02_e8_001",          // string id (we generate, not ObjectId)
  "page_id": "areas/east-london",
  "status": "awaiting_gate_a",              // see enum below
  "current_node": "proposer",
  "pending_gate": "A",                      // "A" | "B" | "C" | null
  "started_at": "2026-05-02T14:00:00Z",
  "updated_at": "2026-05-02T14:03:21Z",
  "current_hypothesis_id": "hyp_...",       // null until proposer emits
  "current_experiment_id": "exp_...",       // null until dispatcher fires
  "current_verdict_id": "vrd_...",          // null until verdict drafted
  "iteration": 1,                           // loop counter; demo cap = 3
  "trigger": "change_stream",               // "change_stream" | "manual"
  "log_tail": [                             // last ~20 node-level events for UI
    { "at": "...", "node": "proposer", "msg": "drafted hypothesis hyp_..." }
  ]
}
```

`status` enum: `running` | `awaiting_gate_a` | `awaiting_gate_b` | `awaiting_gate_c` | `completed` | `aborted`.

### `hypotheses`

```jsonc
{
  "_id": "hyp_2026_05_02_001",
  "run_id": "run_2026_05_02_e8_001",
  "page_id": "areas/east-london",
  "statement": "Adding postcode-tagged case-study cards to /areas/east-london will lift phone_click vs the control by ≥15%.",
  "rationale": "Across 14 retrieved case studies in E5/E8/N1, postcode mention correlates with…",
  "expected_metric": "phone_click",         // primary metric for verdict scoring
  "secondary_metrics": ["callback_form_submit"],
  "expected_direction": "increase",
  "expected_effect_size": "≥15% lift",
  "rag_sources": ["cs_...","cs_...","lrn_..."],   // case_study + learning ids the proposer used
  "open_questions_delta": ["Does effect hold outside E5?"],
  "status": "proposed",                     // proposed | approved | rejected | dispatched | measured
  "created_at": "2026-05-02T14:02:55Z"
}
```

### `experiments`

```jsonc
{
  "_id": "exp_2026_05_02_001",
  "run_id": "run_2026_05_02_e8_001",
  "hypothesis_id": "hyp_2026_05_02_001",
  "page_id": "areas/east-london",
  "posthog_flag_key": "case_studies_v1",
  "variant_a": { "label": "control", "case_study_ids": ["cs_..."] },
  "variant_b": { "label": "B", "case_study_ids": ["cs_..."] },
  "started_at": "2026-05-02T14:05:00Z",
  "ended_at": null,
  "min_sample_per_arm": 80,
  "max_runtime_minutes": 90,                // demo cap
  "live_stats": {                           // analyst writes; refreshed on HogQL pull
    "variant_a": { "n": 41, "phone_click": 3, "callback_form_submit": 1 },
    "variant_b": { "n": 39, "phone_click": 6, "callback_form_submit": 2 }
  },
  "stop_signal": null,                      // null | "convergence" | "min_sample" | "max_runtime" | "early_stop_requested"
  "status": "running"                       // running | awaiting_stop_confirm | stopped
}
```

### `verdicts`

```jsonc
{
  "_id": "vrd_2026_05_02_001",
  "run_id": "run_2026_05_02_e8_001",
  "experiment_id": "exp_2026_05_02_001",
  "hypothesis_id": "hyp_2026_05_02_001",
  "status": "confirmed-directional",        // confirmed-high | confirmed-directional | refuted | inconclusive
  "primary_metric": {
    "name": "phone_click",
    "variant_a": { "n": 102, "conv": 8,  "rate": 0.0784 },
    "variant_b": { "n": 99,  "conv": 14, "rate": 0.1414 },
    "lift": 0.803,
    "ci_method": "bootstrap",
    "ci_low": 0.04, "ci_high": 1.62,
    "direction": "increase"
  },
  "secondary_metrics": [ /* same shape */ ],
  "stop_rule": "max_runtime",
  "reasoning": "B beat A by ~80% relative on phone_click; CI excludes zero but is wide…",
  "replay_evidence_refs": ["evs_...","evs_..."],
  "confidence": "directional",
  "counter_evidence": "Sample below the pre-registered min_sample_per_arm; effect could shrink at scale.",
  "generated_open_questions": ["Does the lift survive on lower-intent boroughs (Camden)?"],
  "hitl_edits": [],                         // appended at gate C
  "approved_by": null,
  "approved_at": null,
  "created_at": "2026-05-02T15:30:00Z"
}
```

`case_studies`, `evidence_sessions`, `learnings` — see `diagrams/schemas.md` (forthcoming) for full shapes; for sync purposes they're read-only and you only need fields you actually render.

---

## 4. SQLite schema (PowerSync client app-schema)

PowerSync requires explicit columns; nested objects live in TEXT columns as JSON and you parse on read.

```ts
import { Schema, Table, Column, ColumnType } from '@powersync/web' // or @powersync/react-native

export const AppSchema = new Schema([
  new Table({
    name: 'agent_runs',
    columns: [
      new Column({ name: 'page_id',                type: ColumnType.TEXT }),
      new Column({ name: 'status',                 type: ColumnType.TEXT }),
      new Column({ name: 'current_node',           type: ColumnType.TEXT }),
      new Column({ name: 'pending_gate',           type: ColumnType.TEXT }),
      new Column({ name: 'started_at',             type: ColumnType.TEXT }),
      new Column({ name: 'updated_at',             type: ColumnType.TEXT }),
      new Column({ name: 'current_hypothesis_id',  type: ColumnType.TEXT }),
      new Column({ name: 'current_experiment_id',  type: ColumnType.TEXT }),
      new Column({ name: 'current_verdict_id',     type: ColumnType.TEXT }),
      new Column({ name: 'iteration',              type: ColumnType.INTEGER }),
      new Column({ name: 'trigger',                type: ColumnType.TEXT }),
      new Column({ name: 'log_tail',               type: ColumnType.TEXT }), // JSON
    ],
  }),
  new Table({
    name: 'hypotheses',
    columns: [
      new Column({ name: 'run_id',                 type: ColumnType.TEXT }),
      new Column({ name: 'page_id',                type: ColumnType.TEXT }),
      new Column({ name: 'statement',              type: ColumnType.TEXT }),
      new Column({ name: 'rationale',              type: ColumnType.TEXT }),
      new Column({ name: 'expected_metric',        type: ColumnType.TEXT }),
      new Column({ name: 'secondary_metrics',      type: ColumnType.TEXT }), // JSON array
      new Column({ name: 'expected_direction',     type: ColumnType.TEXT }),
      new Column({ name: 'expected_effect_size',   type: ColumnType.TEXT }),
      new Column({ name: 'rag_sources',            type: ColumnType.TEXT }), // JSON array
      new Column({ name: 'open_questions_delta',   type: ColumnType.TEXT }), // JSON array
      new Column({ name: 'status',                 type: ColumnType.TEXT }),
      new Column({ name: 'created_at',             type: ColumnType.TEXT }),
    ],
  }),
  new Table({
    name: 'experiments',
    columns: [
      new Column({ name: 'run_id',                 type: ColumnType.TEXT }),
      new Column({ name: 'hypothesis_id',          type: ColumnType.TEXT }),
      new Column({ name: 'page_id',                type: ColumnType.TEXT }),
      new Column({ name: 'posthog_flag_key',       type: ColumnType.TEXT }),
      new Column({ name: 'variant_a',              type: ColumnType.TEXT }), // JSON
      new Column({ name: 'variant_b',              type: ColumnType.TEXT }), // JSON
      new Column({ name: 'started_at',             type: ColumnType.TEXT }),
      new Column({ name: 'ended_at',               type: ColumnType.TEXT }),
      new Column({ name: 'min_sample_per_arm',     type: ColumnType.INTEGER }),
      new Column({ name: 'max_runtime_minutes',    type: ColumnType.INTEGER }),
      new Column({ name: 'live_stats',             type: ColumnType.TEXT }), // JSON
      new Column({ name: 'stop_signal',            type: ColumnType.TEXT }),
      new Column({ name: 'status',                 type: ColumnType.TEXT }),
    ],
  }),
  new Table({
    name: 'verdicts',
    columns: [
      new Column({ name: 'run_id',                 type: ColumnType.TEXT }),
      new Column({ name: 'experiment_id',          type: ColumnType.TEXT }),
      new Column({ name: 'hypothesis_id',          type: ColumnType.TEXT }),
      new Column({ name: 'status',                 type: ColumnType.TEXT }),
      new Column({ name: 'primary_metric',         type: ColumnType.TEXT }), // JSON
      new Column({ name: 'secondary_metrics',      type: ColumnType.TEXT }), // JSON
      new Column({ name: 'stop_rule',              type: ColumnType.TEXT }),
      new Column({ name: 'reasoning',              type: ColumnType.TEXT }),
      new Column({ name: 'replay_evidence_refs',   type: ColumnType.TEXT }), // JSON array
      new Column({ name: 'confidence',             type: ColumnType.TEXT }),
      new Column({ name: 'counter_evidence',       type: ColumnType.TEXT }),
      new Column({ name: 'generated_open_questions', type: ColumnType.TEXT }), // JSON array
      new Column({ name: 'hitl_edits',             type: ColumnType.TEXT }), // JSON array
      new Column({ name: 'approved_by',            type: ColumnType.TEXT }),
      new Column({ name: 'approved_at',            type: ColumnType.TEXT }),
      new Column({ name: 'created_at',             type: ColumnType.TEXT }),
    ],
  }),
  // case_studies / evidence_sessions / learnings: define when you wire those screens
])
```

`id` is implicit on every PowerSync table — it maps from the Mongo `_id` (which we always emit as a string, not ObjectId). Don't redefine it.

Useful convenience views in your UI layer:

```sql
-- pending approvals queue
SELECT * FROM agent_runs WHERE status LIKE 'awaiting_gate_%' ORDER BY updated_at DESC;

-- gate A detail
SELECT h.* FROM hypotheses h
JOIN agent_runs r ON r.current_hypothesis_id = h.id
WHERE r.id = ?;
```

---

## 5. Sync rules (PowerSync service config)

YAML lives in PowerSync admin / config. For the demo, scope = "any authenticated approver sees everything"; we can tighten by tenant later.

```yaml
bucket_definitions:
  approver_global:
    parameters: |
      SELECT request.user_id() as user_id
      WHERE request.jwt() ->> 'role' = 'approver'
    data:
      - SELECT * FROM agent_runs
      - SELECT * FROM hypotheses
      - SELECT * FROM experiments
      - SELECT * FROM verdicts
      - SELECT * FROM case_studies
      - SELECT * FROM evidence_sessions
      - SELECT * FROM learnings
```

To later scope by tenant (e.g. multi-SMB), add a `tenant_id` field to all docs and filter:

```yaml
    data:
      - SELECT * FROM agent_runs       WHERE tenant_id = request.jwt() ->> 'tenant'
      # ...
```

---

## 6. Auth — JWT contract

We issue JWTs; PowerSync verifies; mobile app keeps token in secure storage.

### Token endpoint

```http
POST {API}/v1/auth/token
Content-Type: application/json

{ "client_id": "mobile-approver", "client_secret": "<shared-for-demo>" }

→ 200
{ "token": "eyJhbGciOi...", "expires_at": "2026-05-02T16:00:00Z" }
```

### Claims

```jsonc
{
  "sub": "operator-1",
  "iat": 1714665600,
  "exp": 1714669200,
  "aud": "powersync",
  "iss": "agentic-evolution-hackathon",
  "role": "approver",            // used by sync-rule filter
  "tenant": "rslockandsafe"      // future-proofing; ignore if scope=global
}
```

### Signing

- **Demo**: HS256 with shared secret. PowerSync config gets the same secret.
- **Production path** (write-up only, not built for demo): JWKS endpoint at `GET /v1/auth/jwks.json`, RS256 with rotating keys. PowerSync config points at the JWKS URL.

### PowerSync side

In PowerSync `auth` config:

```yaml
auth:
  jwks:
    # demo: static
    type: shared_secret
    secret: ${POWERSYNC_JWT_SECRET}
    audience: powersync
    issuer: agentic-evolution-hackathon
```

---

## 7. Write path — HITL command endpoints

All approve/reject actions go to our FastAPI. Mobile app calls these directly with the same JWT (auth middleware checks `role=approver`).

### Common response

All command endpoints return `200` with the updated `agent_run` document (so mobile can optimistically update local state while sync catches up):

```jsonc
{
  "run_id": "run_2026_05_02_e8_001",
  "status": "running",
  "pending_gate": null,
  "updated_at": "2026-05-02T14:11:09Z"
}
```

### Endpoints

```http
POST /v1/hitl/gate-a/{run_id}/approve
POST /v1/hitl/gate-a/{run_id}/reject
  body: { "reason"?: "...", "edits"?: { "statement"?: "...", "expected_effect_size"?: "..." } }

POST /v1/hitl/gate-b/{run_id}/confirm-stop
POST /v1/hitl/gate-b/{run_id}/continue
  body: { "reason"?: "..." }

POST /v1/hitl/gate-c/{run_id}/approve
POST /v1/hitl/gate-c/{run_id}/edit-and-approve
  body: {
    "edits": {
      "status"?: "confirmed-directional" | "...",
      "reasoning"?: "...",
      "confidence"?: "high" | "directional" | "low"
    }
  }
```

### Errors

- `401` — JWT missing/expired/invalid
- `403` — `role != approver`
- `404` — run not found
- `409 Conflict` — gate already resolved (idempotent UI: treat as success and refresh from sync)
- `422` — validation (e.g. edits violate schema)

---

## 8. Conflict & idempotency

- HITL approvals are **state-machine transitions**, not free-form edits. Server enforces: a gate can only be resolved while `agent_runs.status = awaiting_gate_X`. Second approver on the same gate gets `409`. Treat 409 as "already done" in the UI.
- Mobile may queue approvals offline → on reconnect, replay them. Server is idempotent on a per-`run_id`-per-gate basis: replaying the same approval after success returns the same updated `agent_run`.
- Reads are eventually consistent: after an approval, your local SQLite updates via sync within a few seconds. Don't wait for sync — render from the command response immediately.

---

## 9. Local dev / demo

- We will run FastAPI on a laptop and expose it via cloudflared **named tunnel** (stable hostname). You will get one URL like `https://agentic-evolution.<acct>.cfargotunnel.com` for both `/v1/auth/token` and `/v1/hitl/*`.
- PowerSync service connects to **MongoDB Atlas directly** — not through our tunnel. We provide the Mongo connection string and ensure IP allowlist permits the PowerSync service.
- Two PowerSync instances are fine: one Cloud project for shared dev, one for demo day. Sync-rule YAML is identical.

---

## 10. Open questions (please ack)

1. **Mobile stack** — RN, Flutter, native iOS/Android? Determines which PowerSync client SDK and language samples we should reference in this doc.
2. **PowerSync hosting** — PowerSync Cloud (fastest to stand up, free tier) or self-host? Cloud is the default recommendation here.
3. **Role/tenant model** — for the hackathon demo, single role `approver`, single tenant `rslockandsafe` is enough. Confirm?
4. **Push notifications on new pending gate** — out of scope for our side; if you want them, easiest is a small `POST /webhook/notify-gabik` we call when an `agent_run` transitions to `awaiting_gate_*`. Want that wired, or polling-on-app-open is fine for demo?
5. **Optimistic UI vs strict sync-only** — recommended optimistic (use command response, then reconcile from sync). Confirm.

---

## 11. What we deliver vs what's on you

| Item                                                   | Owner   |
|--------------------------------------------------------|---------|
| MongoDB Atlas cluster + change streams + RO user       | Us      |
| Document schemas (this doc + `diagrams/schemas.md`)    | Us      |
| FastAPI command endpoints (`/v1/hitl/*`, `/v1/auth/*`) | Us      |
| JWT issuing + signing key                              | Us      |
| Cloudflared named tunnel (stable URL)                  | Us      |
| PowerSync service stand-up + sync-rules deploy         | Gabik   |
| PowerSync app-schema in mobile project                 | Gabik   |
| Mobile UI (queue + per-gate detail screens)            | Gabik   |
| Mobile auth flow (token fetch, refresh, secure store)  | Gabik   |
| Push notifications (if wanted, see Q4)                 | Gabik   |

Ping `alexm` when you've got Q1–5 answered; we'll share secrets via `docs/credentials-for-gabik.md` (no 1Password vault — hackathon expediency). Schemas in §3 are stable enough to start the SQLite mapping today — any field changes will be additive.
