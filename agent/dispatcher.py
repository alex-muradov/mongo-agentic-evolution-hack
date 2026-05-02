"""Dispatcher node — partitions rag_sources into A/B variants, writes Experiment, triggers revalidate.

Cleanup of variant tags happens in T7 reflect (or on next iteration's dispatch).
"""
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from agent.context import NodeContext
from agent.proposer import PAGE_ID_TO_BOROUGHS
from app.deps import settings
from domain.enums import ExperimentStatus, HypothesisStatus, VariantLabel

DISPATCHER_LLM_MODEL = "gpt-4o-mini"
POSTHOG_FLAG_KEY = "case_studies_v1"
DEFAULT_MIN_SAMPLE_PER_ARM = 80
DEFAULT_MAX_RUNTIME_MINUTES = 90


ASSIGNMENT_SCHEMA = {
    "name": "variant_assignment",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "variant_a_ids": {"type": "array", "items": {"type": "string"}},
            "variant_b_ids": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
        },
        "required": ["variant_a_ids", "variant_b_ids", "reasoning"],
    },
}


SYSTEM_PROMPT = """You partition a small set of locksmith case studies into two A/B test variants based on selection rules from a research hypothesis.

Hard constraints:
- `variant_a_ids` and `variant_b_ids` are DISJOINT (no id appears in both).
- Each list should have 4–6 ids; if fewer candidates fit a rule cleanly, return what you can.
- Use ONLY ids from the candidate set provided. Do not invent ids.
- Each id should fit its variant's rule semantically (service_type, borough, framing). If a candidate fits neither rule cleanly, leave it out.
- Reasoning is 1-2 sentences explaining why the partition isolates the hypothesis."""


def _format_candidates(items: list[dict]) -> str:
    return "\n".join(
        f"- id={d['_id']} | {d['service_type']} | {d['borough']} {d.get('postcode_outward','')} "
        f"| {d.get('outcome','-')} | {d.get('price_band','-')}\n"
        f"  title: {d['title']}\n"
        f"  summary: {d['summary'][:200]}{'…' if len(d['summary'])>200 else ''}"
        for d in items
    )


async def _llm_assign(ctx: NodeContext, hyp: dict, candidates: list[dict]) -> dict:
    if not ctx.openai_client:
        raise RuntimeError("dispatcher requires OpenAI client")
    user_prompt = f"""Hypothesis statement: {hyp['statement']}
Primary metric: {hyp['expected_metric']}, expected direction: {hyp['expected_direction']}

variant_a_rule: {hyp['variant_a_rule']}
variant_b_rule: {hyp['variant_b_rule']}

Candidate case studies (the proposer's rag_sources):
{_format_candidates(candidates)}

Partition them. Output strict JSON."""
    r = await ctx.openai_client.chat.completions.create(
        model=DISPATCHER_LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_schema", "json_schema": ASSIGNMENT_SCHEMA},
        temperature=0.2,
        max_tokens=600,
    )
    return json.loads(r.choices[0].message.content)


def _validate_assignment(out: dict, valid_ids: set[str]) -> tuple[list[str], list[str]]:
    """Drop hallucinated ids; ensure disjointness (A wins on conflict)."""
    a = [i for i in out.get("variant_a_ids", []) if i in valid_ids]
    a_set = set(a)
    b = [i for i in out.get("variant_b_ids", []) if i in valid_ids and i not in a_set]
    return a, b


async def _clear_existing_variants(ctx: NodeContext, page_id: str) -> int:
    boroughs = PAGE_ID_TO_BOROUGHS.get(page_id)
    if not boroughs:
        return 0
    r = await ctx.db.case_studies.update_many(
        {"variant": {"$ne": None}, "borough": {"$in": boroughs}},
        {"$set": {"variant": None, "updated_at": datetime.now(timezone.utc)}},
    )
    return r.modified_count


async def _apply_variants(ctx: NodeContext, var_a: list[str], var_b: list[str]) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    n_a = n_b = 0
    if var_a:
        r = await ctx.db.case_studies.update_many(
            {"_id": {"$in": var_a}},
            {"$set": {"variant": VariantLabel.A.value, "updated_at": now}},
        )
        n_a = r.modified_count
    if var_b:
        r = await ctx.db.case_studies.update_many(
            {"_id": {"$in": var_b}},
            {"$set": {"variant": VariantLabel.B.value, "updated_at": now}},
        )
        n_b = r.modified_count
    return n_a, n_b


async def _post_revalidate(page_id: str) -> dict[str, Any]:
    next_url = (settings.NEXT_REVALIDATE_URL or "").strip()
    secret = (settings.REVALIDATE_SECRET or "").strip()
    if not next_url or not secret:
        return {"skipped": True, "reason": "no NEXT_REVALIDATE_URL or REVALIDATE_SECRET"}
    slug = page_id.replace("areas/", "", 1)
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.post(
                f"{next_url.rstrip('/')}/api/revalidate",
                params={"tag": f"case-studies:{slug}"},
                headers={"x-revalidate-secret": secret},
            )
        return {"status_code": r.status_code, "body_excerpt": r.text[:120]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


async def dispatcher(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    hyp_id = state.get("current_hypothesis_id")
    if not hyp_id:
        raise RuntimeError("dispatcher: no current_hypothesis_id in state")

    hyp = await ctx.db.hypotheses.find_one({"_id": hyp_id})
    if not hyp:
        raise RuntimeError(f"dispatcher: hypothesis {hyp_id} not found")

    rag_ids = hyp.get("rag_sources") or []
    if not rag_ids:
        raise RuntimeError(f"dispatcher: hypothesis {hyp_id} has empty rag_sources")

    candidates = [d async for d in ctx.db.case_studies.find({"_id": {"$in": rag_ids}})]
    valid_ids = {d["_id"] for d in candidates}

    assignment = await _llm_assign(ctx, hyp, candidates)
    var_a, var_b = _validate_assignment(assignment, valid_ids)

    if not var_a and not var_b:
        raise RuntimeError("dispatcher: LLM produced no valid variant assignments")

    cleared = await _clear_existing_variants(ctx, state["page_id"])
    n_a, n_b = await _apply_variants(ctx, var_a, var_b)

    exp_id = f"exp_{state['run_id']}_i{state['iteration']}"
    now = datetime.now(timezone.utc)
    await ctx.db.experiments.insert_one({
        "_id": exp_id,
        "run_id": state["run_id"],
        "hypothesis_id": hyp_id,
        "page_id": state["page_id"],
        "posthog_flag_key": POSTHOG_FLAG_KEY,
        "variant_a": {"label": VariantLabel.A.value, "case_study_ids": var_a},
        "variant_b": {"label": VariantLabel.B.value, "case_study_ids": var_b},
        "started_at": now,
        "ended_at": None,
        "min_sample_per_arm": DEFAULT_MIN_SAMPLE_PER_ARM,
        "max_runtime_minutes": DEFAULT_MAX_RUNTIME_MINUTES,
        "live_stats": {
            "variant_a": {"n": 0, "phone_click": 0, "callback_form_submit": 0},
            "variant_b": {"n": 0, "phone_click": 0, "callback_form_submit": 0},
            "last_pulled_at": None,
        },
        "stop_signal": None,
        "status": ExperimentStatus.RUNNING.value,
    })

    await ctx.db.hypotheses.update_one(
        {"_id": hyp_id},
        {"$set": {"status": HypothesisStatus.DISPATCHED.value}},
    )

    revalidate_result = await _post_revalidate(state["page_id"])

    return {
        "current_experiment_id": exp_id,
        "_node_meta": {
            "cleared_prev_variants": cleared,
            "tagged_variant_a": n_a,
            "tagged_variant_b": n_b,
            "assignment_reasoning": assignment.get("reasoning", "")[:140],
            "revalidate": revalidate_result,
        },
    }
