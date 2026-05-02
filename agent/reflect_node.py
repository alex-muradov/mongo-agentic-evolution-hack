"""Reflect node — distill Learning, embed, close open_questions, untag case_studies, revalidate."""
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from agent.context import NodeContext
from agent.dispatcher import _post_revalidate
from domain.enums import ExperimentStatus, OpenQuestionStatus
from integrations.embeddings import embed_text

REFLECT_LLM_MODEL = "gpt-4o-mini"

REFLECT_SCHEMA = {
    "name": "experiment_learning",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "what_worked": {"type": "string"},
            "reasoning": {"type": "string"},
            "counter_factors": {"type": "string"},
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question_id": {"type": "string"},
                        "answer_summary": {"type": "string"},
                    },
                    "required": ["question_id", "answer_summary"],
                },
            },
        },
        "required": ["what_worked", "reasoning", "counter_factors", "answers"],
    },
}

SYSTEM_PROMPT = """You distill an experiment outcome into a durable Learning that future hypothesis-proposers will retrieve via RAG.

Inputs: hypothesis, stats, verdict, list of currently-open research questions on this page.

Output:
- `what_worked`: one short sentence stating the load-bearing finding (or null finding if inconclusive).
- `reasoning`: 2-4 sentences that future-you can semantically retrieve and ground the next hypothesis on. Be concrete. Avoid generic wisdom.
- `counter_factors`: what would invalidate this learning — same shape as verdict's counter_evidence, but written for future RAG context.
- `answers`: for each open_question this experiment actually addresses, return {question_id, answer_summary}. Skip questions not directly addressed."""


def _format_open_questions(items: list[dict]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"- id={q['_id']}: {q['question']}" for q in items)


async def reflect_node(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    if ctx.openai_client is None or ctx.voyage_client is None:
        raise RuntimeError("reflect requires both OpenAI and Voyage clients")

    verdict_id = state.get("current_verdict_id")
    if not verdict_id:
        raise RuntimeError("reflect: no current_verdict_id")

    verdict = await ctx.db.verdicts.find_one({"_id": verdict_id})
    if not verdict:
        raise RuntimeError(f"reflect: verdict {verdict_id} not found")
    exp = await ctx.db.experiments.find_one({"_id": verdict["experiment_id"]})
    hyp = await ctx.db.hypotheses.find_one({"_id": verdict["hypothesis_id"]})

    open_qs = [d async for d in ctx.db.open_questions.find(
        {"page_id": state["page_id"], "status": OpenQuestionStatus.OPEN.value}
    ).limit(20)]

    user_prompt = f"""Hypothesis: {hyp['statement']}
Verdict status: {verdict['status']} (confidence: {verdict['confidence']})
Verdict reasoning: {verdict['reasoning']}

Live stats:
  A: n={exp['live_stats']['variant_a']['n']}, phone_click={exp['live_stats']['variant_a']['phone_click']}, callback_form_submit={exp['live_stats']['variant_a']['callback_form_submit']}
  B: n={exp['live_stats']['variant_b']['n']}, phone_click={exp['live_stats']['variant_b']['phone_click']}, callback_form_submit={exp['live_stats']['variant_b']['callback_form_submit']}
Stop rule: {verdict['stop_rule']}

Currently-open research questions on this page:
{_format_open_questions(open_qs)}

Distill the Learning."""

    r = await ctx.openai_client.chat.completions.create(
        model=REFLECT_LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_schema", "json_schema": REFLECT_SCHEMA},
        temperature=0.4,
        max_tokens=1800,
    )
    out = json.loads(r.choices[0].message.content)

    embedding_text = (out["what_worked"] + "\n" + out["reasoning"]).strip()
    try:
        embedding = await embed_text(ctx.voyage_client, embedding_text)
    except Exception:
        embedding = None

    # derive scoping borough/service_type if all tagged docs share one
    cs_ids = (exp["variant_a"]["case_study_ids"] or []) + (exp["variant_b"]["case_study_ids"] or [])
    boroughs: set = set()
    service_types: set = set()
    async for d in ctx.db.case_studies.find({"_id": {"$in": cs_ids}}, {"borough": 1, "service_type": 1}):
        if d.get("borough"):
            boroughs.add(d["borough"])
        if d.get("service_type"):
            service_types.add(d["service_type"])

    learning_id = f"lrn_{state['run_id']}_i{state['iteration']}"
    now = datetime.now(timezone.utc)
    await ctx.db.learnings.insert_one({
        "_id": learning_id,
        "run_id": state["run_id"],
        "experiment_id": exp["_id"],
        "hypothesis_id": hyp["_id"],
        "verdict_id": verdict_id,
        "page_id": state["page_id"],
        "borough": next(iter(boroughs)) if len(boroughs) == 1 else None,
        "service_type": next(iter(service_types)) if len(service_types) == 1 else None,
        "what_worked": out["what_worked"],
        "reasoning": out["reasoning"],
        "counter_factors": out.get("counter_factors"),
        "related_hypothesis_ids": [hyp["_id"]],
        "embedding": embedding,
        "created_at": now,
    })

    # Close answered open_questions
    valid_oq_ids = {q["_id"] for q in open_qs}
    answered_count = 0
    for ans in out.get("answers", []):
        oq_id = ans.get("question_id")
        if oq_id in valid_oq_ids:
            await ctx.db.open_questions.update_one(
                {"_id": oq_id, "status": OpenQuestionStatus.OPEN.value},
                {"$set": {
                    "status": OpenQuestionStatus.ANSWERED.value,
                    "answered_in_verdict_id": verdict_id,
                    "answered_at": now,
                    "answer_summary": ans.get("answer_summary", ""),
                }},
            )
            answered_count += 1

    # Insert generated open_questions from verdict (if any)
    new_oqs = verdict.get("generated_open_questions") or []
    for q in new_oqs:
        await ctx.db.open_questions.insert_one({
            "_id": f"oq_{learning_id}_{secrets.token_hex(3)}",
            "question": q,
            "raised_by": "verdict",
            "raised_at": now,
            "raised_in_run_id": state["run_id"],
            "related_hypothesis_id": hyp["_id"],
            "related_verdict_id": verdict_id,
            "page_id": state["page_id"],
            "borough": None,
            "service_type": None,
            "status": OpenQuestionStatus.OPEN.value,
        })

    # Cleanup: untag case_studies (back to control rendering)
    untag = await ctx.db.case_studies.update_many(
        {"variant": {"$in": ["A", "B"]}},
        {"$set": {"variant": None, "updated_at": now}},
    )

    # Mark experiment ended
    await ctx.db.experiments.update_one(
        {"_id": exp["_id"]},
        {"$set": {"status": ExperimentStatus.STOPPED.value, "ended_at": now}},
    )

    revalidate_result = await _post_revalidate(state["page_id"])

    return {
        "current_learning_id": learning_id,
        "_node_meta": {
            "learning_id": learning_id,
            "answered_open_questions": answered_count,
            "added_open_questions": len(new_oqs),
            "untagged_case_studies": untag.modified_count,
            "embedded": embedding is not None,
            "revalidate": revalidate_result,
        },
    }
