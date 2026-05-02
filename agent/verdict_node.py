"""Verdict node — bootstrap-CI on analyst's live_stats, classify status, LLM reasoning, gate C pause."""
import json
from datetime import datetime, timezone
from typing import Any, Optional

from agent.analyst import PRIMARY_METRIC_FIELDS, bootstrap_ci_lift
from agent.context import NodeContext
from domain.enums import Confidence, Direction, VerdictStatus

VERDICT_LLM_MODEL = "gpt-4o-mini"

VERDICT_SCHEMA = {
    "name": "experiment_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reasoning": {"type": "string"},
            "counter_evidence": {"type": "string"},
            "generated_open_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["reasoning", "counter_evidence", "generated_open_questions"],
    },
}

SYSTEM_PROMPT = """You are an autonomous research analyst writing the final verdict on a finished A/B experiment.

You are given:
- The hypothesis being tested (statement, expected metric, expected direction).
- Live counts per arm (impressions, conversions on primary + secondary metrics).
- A computed bootstrap 95% CI on relative lift.
- The pre-computed verdict status (one of confirmed-high / confirmed-directional / refuted / inconclusive).

Write three things, ALL in plain English, no jargon for jurors:

1. `reasoning`: 3-5 sentences explaining the verdict. Reference the actual numbers (n, conversions, lift, CI bounds). Be honest about strength of evidence.

2. `counter_evidence`: 1-3 sentences naming what would invalidate this verdict — what additional data or scenarios would refute it. Critical for "agentic evolution" credibility — we know what we don't know.

3. `generated_open_questions`: 1-3 NEW questions raised by this result, things the next iteration could probe. Different from counter_evidence — these are forward-looking research directions, not threats."""


def _classify_status(stop_signal: str, ci_result: Optional[tuple[float, float, float]], expected_dir: str) -> tuple[VerdictStatus, Confidence]:
    if ci_result is None:
        return VerdictStatus.INCONCLUSIVE, Confidence.LOW
    lift, lo, hi = ci_result
    expected_increase = expected_dir == Direction.INCREASE.value
    expected_decrease = expected_dir == Direction.DECREASE.value

    # CI excludes 0 → significant
    if lo > 0:
        status = VerdictStatus.CONFIRMED_HIGH if expected_increase else VerdictStatus.REFUTED
        return status, Confidence.HIGH
    if hi < 0:
        status = VerdictStatus.CONFIRMED_HIGH if expected_decrease else VerdictStatus.REFUTED
        return status, Confidence.HIGH

    # CI straddles 0 — direction-only check
    if stop_signal == "min_sample":
        directional = (
            (expected_increase and lift > 0) or
            (expected_decrease and lift < 0) or
            (expected_dir == Direction.NO_CHANGE.value and abs(lift) < 0.05)
        )
        if directional:
            return VerdictStatus.CONFIRMED_DIRECTIONAL, Confidence.DIRECTIONAL

    return VerdictStatus.INCONCLUSIVE, Confidence.LOW


def _arm_stat(n: int, conv: int) -> dict:
    return {"n": n, "conv": conv, "rate": (conv / n) if n > 0 else 0.0}


def _metric_result(name: str, a_n: int, a_conv: int, b_n: int, b_conv: int, expected_dir: str) -> dict:
    ci = bootstrap_ci_lift(a_n, a_conv, b_n, b_conv)
    if ci is None:
        lift, lo, hi = 0.0, 0.0, 0.0
    else:
        lift, lo, hi = ci
    return {
        "name": name,
        "variant_a": _arm_stat(a_n, a_conv),
        "variant_b": _arm_stat(b_n, b_conv),
        "lift": round(lift, 4),
        "ci_method": "bootstrap",
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
        "direction": expected_dir,
    }


async def verdict_node(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    if ctx.openai_client is None:
        raise RuntimeError("verdict_node requires OpenAI client")

    exp_id = state.get("current_experiment_id")
    if not exp_id:
        raise RuntimeError("verdict_node: no current_experiment_id")
    exp = await ctx.db.experiments.find_one({"_id": exp_id})
    if not exp:
        raise RuntimeError(f"verdict_node: experiment {exp_id} not found")
    hyp = await ctx.db.hypotheses.find_one({"_id": exp["hypothesis_id"]})
    if not hyp:
        raise RuntimeError(f"verdict_node: hypothesis {exp['hypothesis_id']} not found")

    stop_signal = exp.get("stop_signal") or "insufficient_sample"
    expected_dir = hyp.get("expected_direction", Direction.INCREASE.value)
    primary_metric = hyp.get("expected_metric", "phone_click")
    primary_field = PRIMARY_METRIC_FIELDS.get(primary_metric, "phone_click")

    a = exp["live_stats"]["variant_a"]
    b = exp["live_stats"]["variant_b"]

    primary_result = _metric_result(
        primary_metric, a["n"], a[primary_field], b["n"], b[primary_field], expected_dir
    )
    primary_ci = (primary_result["lift"], primary_result["ci_low"], primary_result["ci_high"])
    primary_ci_obj = primary_ci if primary_result["ci_low"] != primary_result["ci_high"] else None

    status, confidence = _classify_status(stop_signal, primary_ci_obj, expected_dir)

    secondary_metric = "callback_form_submit" if primary_metric == "phone_click" else "phone_click"
    secondary_field = PRIMARY_METRIC_FIELDS[secondary_metric]
    secondary_result = _metric_result(
        secondary_metric, a["n"], a[secondary_field], b["n"], b[secondary_field], expected_dir
    )

    # LLM reasoning
    user_prompt = f"""Hypothesis: {hyp['statement']}
Rationale: {hyp.get('rationale', '')}
Expected metric: {primary_metric} (direction: {expected_dir}, effect size: {hyp.get('expected_effect_size','-')})

Stop signal: {stop_signal}
Computed verdict status: {status.value}, confidence: {confidence.value}

Primary metric ({primary_metric}):
  variant A: n={a['n']}, conversions={a[primary_field]}, rate={primary_result['variant_a']['rate']:.4f}
  variant B: n={b['n']}, conversions={b[primary_field]}, rate={primary_result['variant_b']['rate']:.4f}
  lift: {primary_result['lift']:.4f}    bootstrap 95% CI: [{primary_result['ci_low']:.4f}, {primary_result['ci_high']:.4f}]

Secondary metric ({secondary_metric}):
  variant A: n={a['n']}, conversions={a[secondary_field]}, rate={secondary_result['variant_a']['rate']:.4f}
  variant B: n={b['n']}, conversions={b[secondary_field]}, rate={secondary_result['variant_b']['rate']:.4f}
  lift: {secondary_result['lift']:.4f}    bootstrap 95% CI: [{secondary_result['ci_low']:.4f}, {secondary_result['ci_high']:.4f}]

Write the verdict's reasoning, counter_evidence, and generated_open_questions per the schema. Be specific to these numbers."""

    r = await ctx.openai_client.chat.completions.create(
        model=VERDICT_LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_schema", "json_schema": VERDICT_SCHEMA},
        temperature=0.4,
        max_tokens=900,
    )
    out = json.loads(r.choices[0].message.content)

    verdict_id = f"vrd_{state['run_id']}_i{state['iteration']}"
    now = datetime.now(timezone.utc)
    replay_refs = exp.get("replay_session_ids") or []
    await ctx.db.verdicts.insert_one({
        "_id": verdict_id,
        "run_id": state["run_id"],
        "experiment_id": exp_id,
        "hypothesis_id": exp["hypothesis_id"],
        "status": status.value,
        "primary_metric": primary_result,
        "secondary_metrics": [secondary_result],
        "stop_rule": stop_signal,
        "reasoning": out["reasoning"],
        "replay_evidence_refs": replay_refs,
        "confidence": confidence.value,
        "counter_evidence": out.get("counter_evidence"),
        "generated_open_questions": out.get("generated_open_questions", []),
        "hitl_edits": [],
        "approved_by": None,
        "approved_at": None,
        "created_at": now,
    })

    return {
        "current_verdict_id": verdict_id,
        "pending_gate": "C",
        "_node_meta": {
            "status": status.value,
            "confidence": confidence.value,
            "primary_lift": primary_result["lift"],
            "primary_ci": [primary_result["ci_low"], primary_result["ci_high"]],
        },
    }
