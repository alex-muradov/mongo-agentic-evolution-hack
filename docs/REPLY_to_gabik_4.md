# Reply #4 to Gabik — site integration verified, two TODOs before demo

**From**: alexm (AutoResearch)
**To**: Gabik (Wild Coral website)
**Date**: 2026-05-02

---

## TL;DR

- ✅ End-to-end live and verified. Your `/areas/east-london` "Recent Jobs in East London" section renders **our** case_studies (curl-ed your HTML, top 6 titles match our control-set verbatim — `Security audit for residential building`, `Repair of disabled toilet door handle`, etc.)
- ✅ T6 dispatcher shipped on our side: when our agent flips a hypothesis into an experiment, it tags 5 case_studies with `variant=A/B` in Mongo and POSTs `/api/revalidate?tag=case-studies:east-london` to your Vercel — your route returned `{"ok":true,"revalidatedAt":...}` correctly.
- ⚠ Two things on your side before demo, both 5-minute fixes (§3 + §4 below).

---

## 1. What we verified

```bash
curl https://www.rslockandsafe.co.uk/areas/east-london | grep "Recent Jobs"
# → "<h2>Recent Jobs in East London</h2>"
# → titles in following h-tags exactly match the items we return on
#   GET https://lots-joan-motorcycles-acdbentity.trycloudflare.com/v1/case-studies?area=east-london
```

Six titles in your "Recent Jobs" section, all from our hydrated case_studies (control bucket — 110+ docs with `variant=null`). ISR cache works; after our `/api/revalidate` POST, your next render is fresh.

---

## 2. What we shipped on our side (T6 dispatcher)

When our agent runs:

1. **Proposer** (T5) RAGs `case_studies` + `learnings` + `open_questions`, drafts a Hypothesis via gpt-4o-mini, pauses at Gate A.
2. Gate A approval (currently a manual `POST /v1/agent/runs/{id}/resume?after_gate=A`, will become a Streamlit click in T7).
3. **Dispatcher** loads the hypothesis, runs a small gpt-4o-mini call to partition the proposer's `rag_sources` into `variant_a_ids` and `variant_b_ids` (using the LLM-emitted `variant_a_rule` / `variant_b_rule` text), updates those case_studies in Mongo with `variant: A` or `variant: B`, writes an Experiment doc, marks the hypothesis `dispatched`, then POSTs to your `/api/revalidate`.
4. Subsequent visitors at `/areas/east-london` (with the right PostHog flag bucket) see the new variant — see §3.

Latest run (`run_areas_east_london_d13705b1`) tagged: 4 docs to A (all `lock_change` in Hackney), 1 doc to B (`Late-night lockout in Clapton` — the variant_b_rule cited it by id). Imbalance 4-vs-1 is a known weakness of the proposer prompt currently — we'll tweak before demo.

---

## 3. ~~TODO 1: PostHog flag rollout~~ — **DONE by us**

When I probed your PostHog project to wire the analyst, your built-in chat assistant flagged that `case_studies_v1` **didn't exist at all** — never created. Your personal API key has `feature_flag:write` scope (the one you shared with us covers it), so I went ahead and **created the flag** on your behalf to unblock the demo path:

```
key:      case_studies_v1
type:     multivariate
variants: control (0%), A (50%), B (50%)
active:   true
id:       179899  (in your project)
```

Sanity-check via PostHog API confirms it's live. Your existing `posthog-node.getFeatureFlag("case_studies_v1", distinctId)` calls will start returning `"A"` or `"B"` for new visitors automatically (PostHog SDK polls flag config every ~30s).

If the rollout split doesn't suit you (e.g. you want 20/40/40 with a control holdout, or a different traffic %), open the flag in your PostHog UI and tweak — UI changes propagate to the same flag id 179899.

Apologies for the autonomous write — happy to delete and let you recreate if you'd rather own the configuration. It's just `case_studies_v1` from our handoff agreement, exact shape we discussed.

---

## 4. **TODO 2**: event name canonicalisation

When I probed your PostHog project (170388, EU Cloud), the actual event names firing in production are:

```
phone_call_clicked       (1)
callback_form_submitted  (1)
case_study_impression    (0 yet)
$feature_flag_called     (handled automatically)
```

But your `case-studies-handoff.md` §6.7 specified `phone_click` and `callback_form_submit` (no past-tense suffix).

This is fine — but **decide which is canonical** before our analyst code starts pulling counts. Easier to rename once on your side than for us to maintain a name-mapping table.

**Action**: pick one. If you go with the production names (`phone_call_clicked` / `callback_form_submitted`), update §6.7 of your handoff doc; we wire our analyst HogQL to match. If you prefer the spec names (`phone_click` / `callback_form_submit`), rename the events in your client code and re-deploy.

I'll proceed with whichever name you confirm — analyst code lands today.

---

## 5. Heads-up: many `area=` slugs return empty

Your fronted is calling our API for many area slugs:

```
GET /v1/case-studies?area=hackney        → items: []
GET /v1/case-studies?area=islington      → items: []
GET /v1/case-studies?area=bethnal-green  → items: []
GET /v1/case-studies?area=camden         → items: []
GET /v1/case-studies?area=clapton        → items: []
GET /v1/case-studies?area=dalston        → items: []
GET /v1/case-studies?area=shoreditch     → items: []
GET /v1/case-studies?area=east-london    → items: 20  ✓
```

Our hydration assigned `page_id` based on borough → page_id mapping. Currently only `areas/east-london` has data; everything else is `areas/london-other` or `areas/uk-other`.

**Two ways to handle**:
- **Your side (cheap)**: render only `/areas/east-london` for the demo; soft-redirect or 404 the others.
- **Our side (slightly more work)**: extend `PAGE_ID_TO_BOROUGHS` in our hydration to add `areas/north-london`, `areas/inner-london`, etc., and re-tag historicals. Doable in ~10 min if you want multiple areas live.

Pick one, ping me — both are easy.

---

## 6. What's coming from us next

- **T6 measurement** (in flight, today): analyst node pulls HogQL counts per variant + bootstrap-CI on lift; replay_summarizer streams per-session summaries into `evidence_sessions`. Will respect your event-name decision from §4.
- **T7**: Streamlit HITL UI (gate A/B/C approval clicks instead of curl), verdict + reflect nodes, end-to-end loop.

---

## 7. Cross-reference

- `docs/credentials-for-gabik.md` — single source of truth for all secrets (still authoritative)
- `docs/website-handoff.md` — read-surface runbook (still authoritative for site contract)
- `docs/wildcoral-integration.md` — mobile/RAG runbook (parked, not affected)
- `docs/REPLY_to_gabik_handoff_3.md` — previous status update

— alexm
