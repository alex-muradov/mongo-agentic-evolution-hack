"""Proposer node — RAG over case_studies + learnings, LLM-drafted Hypothesis, gate A pause."""
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from agent.context import NodeContext
from domain.enums import HypothesisStatus, OpenQuestionStatus
from integrations.embeddings import embed_text


RAG_ANCHOR_QUERY = (
    "London locksmith case studies for an East London Area Hub: representative mix of "
    "common services (emergency lockout, lock change, uPVC repair, safe opening, key extraction) "
    "across multiple boroughs and customer types, with strong trust signals."
)

PROPOSER_LLM_MODEL = "gpt-4o-mini"
RAG_K_CASE_STUDIES = 8
RAG_K_LEARNINGS = 4
OPEN_Q_LIMIT = 10

# page_id → boroughs filter (the vector index has `borough` as a filter-path, not `page_id`).
# When PageSpec collection is seeded (T6+) we'll read this from Mongo instead.
PAGE_ID_TO_BOROUGHS: dict[str, list[str]] = {
    "areas/east-london": ["Hackney", "Tower Hamlets", "Newham", "Waltham Forest", "Redbridge"],
}


PROPOSER_SCHEMA = {
    "name": "research_hypothesis",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "statement": {"type": "string"},
            "rationale": {"type": "string"},
            "expected_metric": {
                "type": "string",
                "enum": ["phone_click", "callback_form_submit"],
            },
            "secondary_metrics": {"type": "array", "items": {"type": "string"}},
            "expected_direction": {
                "type": "string",
                "enum": ["increase", "decrease", "no_change"],
            },
            "expected_effect_size": {"type": "string"},
            "variant_a_rule": {"type": "string"},
            "variant_b_rule": {"type": "string"},
            "rag_sources": {"type": "array", "items": {"type": "string"}},
            "open_questions_delta": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "statement", "rationale", "expected_metric", "secondary_metrics",
            "expected_direction", "expected_effect_size",
            "variant_a_rule", "variant_b_rule",
            "rag_sources", "open_questions_delta",
        ],
    },
}


SYSTEM_PROMPT = """You are an autonomous research analyst designing A/B experiments for a programmatic-SEO locksmith landing page.

The page is `/areas/east-london` on a London locksmith website. It renders a "Recent Jobs in East London" section. Visitors arriving here are likely worried about a lock-out, lock-change, or security concern. The two conversion events are:
- `phone_click` (immediate-call) — primary, faster signal, fires when a visitor taps the phone link.
- `callback_form_submit` (lead capture) — secondary, slower but higher-intent.

You generate ONE hypothesis at a time about how to vary the case_studies shown in this section to improve conversion. Hard constraints:

1. The hypothesis MUST be expressible as ONE selection rule difference between variant A (control) and variant B (test). Anything else is unfalsifiable.
2. Selection rules operate over the available `case_studies` and CAN reference: `service_type` (enum), `borough`, `postcode_outward`, `outcome`, `price_band` (internal-only filter, never surfaces in copy), `completed_at` (recency window). Each rule should specify roughly 5 case_studies to feature.
3. `expected_metric` must be one of `phone_click` or `callback_form_submit`. Default to `phone_click` for high-urgency framings, `callback_form_submit` for educational/research-mode framings.
4. `expected_direction` is `increase`, `decrease`, or `no_change`. Effect size is human-readable, e.g. "≥10% lift" or "no measurable change".
5. `rag_sources` MUST cite case_study `_id` strings ONLY from the retrieved set provided to you. Do not invent ids.
6. `open_questions_delta` lists 1–3 NEW research questions raised by this hypothesis — things the verdict will NOT directly answer but that future iterations should explore.

Be specific. Avoid generic claims like "improving the section will help conversions" — name the lever (which case studies, which framing, which order, which filter)."""


def _format_case_studies(items: list[dict]) -> str:
    lines = []
    for d in items:
        lines.append(
            f"- id={d['_id']} | {d['service_type']} | {d['borough']} {d.get('postcode_outward','')} "
            f"| {d.get('outcome','-')} | {d.get('price_band','-')}\n"
            f"  title: {d['title']}\n"
            f"  summary: {d['summary'][:240]}{'…' if len(d['summary'])>240 else ''}"
        )
    return "\n".join(lines) if lines else "(none retrieved)"


def _format_learnings(items: list[dict]) -> str:
    if not items:
        return "(no prior learnings — this is the first iteration)"
    return "\n".join(
        f"- id={d['_id']} | {d.get('borough','-')} | {d.get('service_type','-')}\n"
        f"  what_worked: {d['what_worked']}\n"
        f"  reasoning: {d['reasoning'][:200]}"
        for d in items
    )


def _format_open_questions(items: list[dict]) -> str:
    if not items:
        return "(no open questions yet)"
    return "\n".join(f"- {d['question']}  (raised_by={d.get('raised_by','-')})" for d in items)


def build_user_prompt(
    page_id: str,
    iteration: int,
    case_studies: list[dict],
    learnings: list[dict],
    open_questions: list[dict],
) -> str:
    return f"""Page: {page_id}
Iteration: {iteration} of 3 (loop cap)

Retrieved case_studies (top {len(case_studies)} by semantic similarity):
{_format_case_studies(case_studies)}

Prior learnings:
{_format_learnings(learnings)}

Open research questions:
{_format_open_questions(open_questions)}

Propose ONE hypothesis. Output strict JSON matching the schema. Cite specific case_study ids in rag_sources from the retrieved set above."""


async def _rag_case_studies(ctx: NodeContext, qvec: list[float], page_id: str, k: int) -> list[dict]:
    filter_q: dict = {"schema_version": "v1"}
    boroughs = PAGE_ID_TO_BOROUGHS.get(page_id)
    if boroughs:
        filter_q["borough"] = {"$in": boroughs}
    pipeline = [
        {"$vectorSearch": {
            "index": "case_studies_vec",
            "queryVector": qvec,
            "path": "embedding",
            "numCandidates": max(150, k * 20),
            "limit": k,
            "filter": filter_q,
        }},
        {"$project": {
            "_id": 1, "service_type": 1, "borough": 1, "postcode_outward": 1,
            "outcome": 1, "price_band": 1, "title": 1, "summary": 1,
            "score": {"$meta": "vectorSearchScore"},
        }},
    ]
    return [d async for d in ctx.db.case_studies.aggregate(pipeline)]


async def _rag_learnings(ctx: NodeContext, qvec: list[float], page_id: str, k: int) -> list[dict]:
    if await ctx.db.learnings.count_documents({"page_id": page_id}) == 0:
        return []
    boroughs = PAGE_ID_TO_BOROUGHS.get(page_id)
    filter_q: dict = {}
    if boroughs:
        filter_q["borough"] = {"$in": boroughs}
    pipeline = [
        {"$vectorSearch": {
            "index": "learnings_vec",
            "queryVector": qvec,
            "path": "embedding",
            "numCandidates": max(50, k * 10),
            "limit": k,
            "filter": filter_q,
        }},
        {"$project": {
            "_id": 1, "borough": 1, "service_type": 1,
            "what_worked": 1, "reasoning": 1,
        }},
    ]
    try:
        return [d async for d in ctx.db.learnings.aggregate(pipeline)]
    except Exception:
        return []  # learnings_vec index not yet created — fall back to empty


async def _open_questions(ctx: NodeContext, page_id: str, limit: int) -> list[dict]:
    cursor = ctx.db.open_questions.find(
        {"$or": [{"page_id": page_id}, {"page_id": None}], "status": OpenQuestionStatus.OPEN.value}
    ).limit(limit)
    return [d async for d in cursor]


async def proposer(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    if ctx.voyage_client is None or ctx.openai_client is None:
        raise RuntimeError("proposer requires both Voyage and OpenAI clients")

    page_id = state["page_id"]
    iteration = state["iteration"]
    run_id = state["run_id"]

    qvec = await embed_text(ctx.voyage_client, RAG_ANCHOR_QUERY)
    case_studies = await _rag_case_studies(ctx, qvec, page_id, RAG_K_CASE_STUDIES)
    learnings = await _rag_learnings(ctx, qvec, page_id, RAG_K_LEARNINGS)
    open_qs = await _open_questions(ctx, page_id, OPEN_Q_LIMIT)

    user_prompt = build_user_prompt(page_id, iteration, case_studies, learnings, open_qs)
    r = await ctx.openai_client.chat.completions.create(
        model=PROPOSER_LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_schema", "json_schema": PROPOSER_SCHEMA},
        temperature=0.6,
        max_tokens=1500,
    )
    out = json.loads(r.choices[0].message.content)

    # validate cited rag_sources actually came from our retrieved set; drop hallucinated ids
    valid_ids = {d["_id"] for d in case_studies} | {d["_id"] for d in learnings}
    out["rag_sources"] = [s for s in out.get("rag_sources", []) if s in valid_ids]

    hyp_id = f"hyp_{run_id}_i{iteration}"
    now = datetime.now(timezone.utc)
    await ctx.db.hypotheses.insert_one({
        "_id": hyp_id,
        "run_id": run_id,
        "page_id": page_id,
        "statement": out["statement"],
        "rationale": out["rationale"],
        "expected_metric": out["expected_metric"],
        "secondary_metrics": out["secondary_metrics"],
        "expected_direction": out["expected_direction"],
        "expected_effect_size": out["expected_effect_size"],
        "variant_a_rule": out["variant_a_rule"],
        "variant_b_rule": out["variant_b_rule"],
        "rag_sources": out["rag_sources"],
        "open_questions_delta": out["open_questions_delta"],
        "status": HypothesisStatus.PROPOSED.value,
        "created_at": now,
    })

    for q in out.get("open_questions_delta", []):
        await ctx.db.open_questions.insert_one({
            "_id": f"oq_{hyp_id}_{secrets.token_hex(3)}",
            "question": q,
            "raised_by": "proposer",
            "raised_at": now,
            "raised_in_run_id": run_id,
            "related_hypothesis_id": hyp_id,
            "page_id": page_id,
            "borough": None,
            "service_type": None,
            "status": OpenQuestionStatus.OPEN.value,
        })

    return {
        "current_hypothesis_id": hyp_id,
        "pending_gate": "A",
        "_proposer_meta": {
            "rag_case_studies": len(case_studies),
            "rag_learnings": len(learnings),
            "open_questions_in": len(open_qs),
            "open_questions_added": len(out.get("open_questions_delta", [])),
            "rag_sources_cited": len(out["rag_sources"]),
        },
    }
