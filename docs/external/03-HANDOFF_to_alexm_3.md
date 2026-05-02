# Handoff #3 to alexm — Wild Coral wired, awaiting cluster

**From**: Gabik (Wild Coral)
**To**: alexm (AutoResearch)
**Date**: 2026-05-02
**Re**: your `wildcoral-integration.md` runbook — alignment confirmed

---

## TL;DR

Wild Coral side is **code-complete** for both RAG paths. Single blocker on
my end is your cluster. One thing I owe you (the AI key) before T3.5.

---

## Code status — done on my side

| Path | Where | Smoke-tested |
|---|---|---|
| Embedder against `https://ai.mongodb.com/v1` (OpenAI SDK, voyage-4-large, unit-norm) | `agent/qualification/embeddings.py` | locally, against the gateway — works |
| Auto-RAG hook on user turns → `$vectorSearch` over `fieldcraft.observed_outcomes` → injected into chat ctx | `agent/qualification/agent.py` `on_user_turn_completed` | pending Atlas |
| Cross-system tool → `$vectorSearch` over `agentic_evolution.case_studies` (your runbook §4 verbatim) | `agent/qualification/agent.py` `find_similar_case_studies` | pending Atlas + T3.5 |
| Outbound POST `/v1/ingest/case-study` per your §6, full §7 retry semantics (422 = no-retry, 5xx = throw → App Services backoff) | `backend/functions/onTraderVerdict.ts` | pending `INGEST_URL` + `INGEST_API_KEY` |
| PII validator mirroring your §5 (money / email / UK mobile / 40–80 word summary / postcode outward / street-not-leading-digit) | `agent/qualification/pii.py` + TS mirror in `onTraderVerdict.ts` | unit-checkable, will run end-to-end after Atlas |
| Smoke script per your §5 | `scripts/smoke_rag.py` | runs, returns empty (expected) until cluster + T3.5 |

Env names match your runbook 1:1: `MONGODB_AI_KEY`, `INGEST_URL`,
`INGEST_API_KEY`, `MONGODB_URI`, `MONGODB_AI_BASE_URL`,
`MONGODB_AI_EMBED_MODEL=voyage-4-large`.

---

## What I'm dropping in 1Password right now

- **`autoresearch/MONGODB_AI_KEY`** = `al-REDACTED`
  - Same key I'm using on Wild Coral side. You need it for T3.5 historical
    embedding. Use the same model + unit-norm convention so the index
    semantics line up between your bulk-embedded historicals and my
    live-embedded queries.

That's the one thing I owe you per your §9.

---

## Acks on your §9 questions

- **`_id` opaque** — confirmed. We treat `cs_<source>_<source_job_id>` as a
  string pointer; stored on `jobs.external_case_study_id`.
- **Diarization** — confirmed handled inside Wild Coral. LiveKit STT
  produces only the trader's transcript; the agent never sees customer
  audio. `problem` / `solution` / `summary` come from the agent's reasoning
  over the trader's debrief, not raw multi-speaker audio.

---

## Still waiting on you (per your §8)

- [ ] Atlas cluster provisioned
- [ ] `wildcoral_rag_ro` user → `autoresearch/wildcoral_rag_ro` in 1P
- [ ] `INGEST_URL` (cloudflared named tunnel hostname) in 1P
- [ ] `INGEST_API_KEY` in 1P
- [ ] T3.5 hydration of historical case_studies

When you ping me with each, my checklist on this side is:

```bash
# 1. Paste URI into .env
echo 'MONGODB_URI=...' >> .env

# 2. Apply our two vector indexes (Atlas UI or atlas-cli)
atlas clusters search indexes create -f backend/schema/skill_files_vector_index.json
atlas clusters search indexes create -f backend/schema/observed_outcomes_vector_index.json

# 3. Seed our trader + outcomes (embeds via the shared gateway)
python scripts/seed_trader.py
python scripts/seed_demo_outcomes.py

# 4. Smoke read path
python scripts/smoke_rag.py "boiler swap"
# Expect: empty until your T3.5 lands. After T3.5: 3 hits, score 0.4–0.9.

# 5. Once INGEST_URL + INGEST_API_KEY land, paste into .env, then run an
#    end-to-end voice session and approve a job. Should land in your
#    case_studies collection within ~2s of the trader tap.
```

---

## One open question

For the schemas you index in your cluster — when you create our `fieldcraft`
DB on the same cluster, do **we** create our collections + vector indexes
inside it (using a write user), or do you want to provision them too? My
preference is option A (we own everything inside `fieldcraft`, you own
everything inside `agentic_evolution`, both in the same cluster). Says so in
the JSON files we'd run via `atlas-cli`.

---

## Cross-reference

- This handoff supersedes `HANDOFF_to_alexm.md` (deferred, PowerSync
  misalignment) and `REPLY_to_alexm_2.md` (Option 1 push — accepted).
- `docs/INTEGRATION_status.md` is the living source of truth on our side.
- Your `wildcoral-integration.md` runbook is the source of truth for the contract.

— Gabik
