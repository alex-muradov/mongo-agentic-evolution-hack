"""Analyst node — pulls per-variant counts from PostHog HogQL, updates live_stats, emits stop signal."""
import random
from datetime import datetime, timezone
from typing import Any, Optional

from agent.context import NodeContext
from app.deps import settings
from domain.enums import Direction, StopRule
from integrations.posthog import PostHogClient


def _simulate_traffic(expected_direction: str) -> dict[str, dict[str, int]]:
    """Demo-mode synthetic stats. Realistic ranges; honours hypothesis direction."""
    base_phone = 0.06
    base_form = 0.02
    a_n = random.randint(100, 140)
    b_n = random.randint(100, 140)

    if expected_direction == Direction.INCREASE.value:
        lift_phone = random.uniform(0.30, 0.80)
        lift_form = random.uniform(0.10, 0.50)
    elif expected_direction == Direction.DECREASE.value:
        lift_phone = random.uniform(-0.50, -0.20)
        lift_form = random.uniform(-0.40, -0.10)
    else:
        lift_phone = random.uniform(-0.10, 0.10)
        lift_form = random.uniform(-0.10, 0.10)

    def jitter(rate, n):
        return max(0, round(n * rate * random.uniform(0.85, 1.15)))

    return {
        "A": {
            "n": a_n,
            "phone_click": jitter(base_phone, a_n),
            "callback_form_submit": jitter(base_form, a_n),
        },
        "B": {
            "n": b_n,
            "phone_click": jitter(base_phone * (1 + lift_phone), b_n),
            "callback_form_submit": jitter(base_form * (1 + lift_form), b_n),
        },
    }

# Maps the canonical metric labels we use in Hypothesis (`expected_metric`) to the
# field name our HogQL aggregation produces and into ArmStats.
PRIMARY_METRIC_FIELDS = {
    "phone_click": "phone_click",
    "callback_form_submit": "callback_form_submit",
}

BOOTSTRAP_ITERATIONS = 5000


def bootstrap_ci_lift(
    a_n: int, a_conv: int, b_n: int, b_conv: int,
    *, n_iter: int = BOOTSTRAP_ITERATIONS, alpha: float = 0.05,
) -> Optional[tuple[float, float, float]]:
    """Returns (lift, ci_low, ci_high) for relative lift (rate_b - rate_a)/rate_a, or None."""
    if a_n == 0 or b_n == 0:
        return None
    a_rate = a_conv / a_n
    b_rate = b_conv / b_n
    if a_rate == 0:
        return None
    lift = (b_rate - a_rate) / a_rate
    samples = []
    for _ in range(n_iter):
        a_sim = sum(1 for _ in range(a_n) if random.random() < a_rate) / a_n
        b_sim = sum(1 for _ in range(b_n) if random.random() < b_rate) / b_n
        if a_sim == 0:
            continue
        samples.append((b_sim - a_sim) / a_sim)
    if not samples:
        return None
    samples.sort()
    lo = samples[int(len(samples) * alpha / 2)]
    hi = samples[int(len(samples) * (1 - alpha / 2))]
    return (lift, lo, hi)


def _make_posthog_client() -> Optional[PostHogClient]:
    if not (settings.POSTHOG_HOST and settings.POSTHOG_PROJECT_ID and settings.POSTHOG_PERSONAL_API_KEY):
        return None
    return PostHogClient(
        host=settings.POSTHOG_HOST,
        project_id=settings.POSTHOG_PROJECT_ID,
        personal_api_key=settings.POSTHOG_PERSONAL_API_KEY,
    )


async def _pull_counts(client: PostHogClient, started_at: datetime) -> dict[str, dict[str, int]]:
    """Returns {'A': {n, phone_click, callback_form_submit}, 'B': {...}}.

    Tolerates both spec event names (`phone_click` / `callback_form_submit`) and
    production names (`phone_call_clicked` / `callback_form_submitted`) until canonicalised.
    """
    started_iso = started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"""
SELECT
  toString(properties.variant) AS variant,
  countIf(event = 'case_study_impression')                                          AS impressions,
  countIf(event IN ('phone_click','phone_call_clicked'))                            AS phone_click,
  countIf(event IN ('callback_form_submit','callback_form_submitted'))              AS callback_form_submit
FROM events
WHERE timestamp >= toDateTime('{started_iso}')
  AND properties.variant IN ('A', 'B')
GROUP BY variant
"""
    rows = await client.hogql(query)
    out = {
        "A": {"n": 0, "phone_click": 0, "callback_form_submit": 0},
        "B": {"n": 0, "phone_click": 0, "callback_form_submit": 0},
    }
    for r in rows:
        v = r.get("variant")
        if v in out:
            out[v] = {
                "n": int(r.get("impressions") or 0),
                "phone_click": int(r.get("phone_click") or 0),
                "callback_form_submit": int(r.get("callback_form_submit") or 0),
            }
    return out


async def analyst(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    exp_id = state.get("current_experiment_id")
    if not exp_id:
        raise RuntimeError("analyst: no current_experiment_id in state")

    exp = await ctx.db.experiments.find_one({"_id": exp_id})
    if not exp:
        raise RuntimeError(f"analyst: experiment {exp_id} not found")
    started_at: datetime = exp["started_at"]
    min_sample = int(exp.get("min_sample_per_arm") or 80)
    max_runtime_min = int(exp.get("max_runtime_minutes") or 90)

    hyp = await ctx.db.hypotheses.find_one({"_id": exp["hypothesis_id"]})
    primary_metric = (hyp or {}).get("expected_metric", "phone_click")
    metric_field = PRIMARY_METRIC_FIELDS.get(primary_metric, "phone_click")

    posthog = _make_posthog_client()
    now = datetime.now(timezone.utc)

    if posthog is None:
        # PostHog credentials missing — skip pull, mark insufficient_sample so verdict can finalize as inconclusive.
        await ctx.db.experiments.update_one(
            {"_id": exp_id},
            {"$set": {"live_stats.last_pulled_at": now, "stop_signal": StopRule.INSUFFICIENT_SAMPLE.value}},
        )
        return {
            "stop_signal": StopRule.INSUFFICIENT_SAMPLE.value,
            "_node_meta": {"reason": "PostHog credentials not configured"},
        }

    try:
        counts = await _pull_counts(posthog, started_at)
    except Exception as e:
        await ctx.db.experiments.update_one(
            {"_id": exp_id},
            {"$set": {"live_stats.last_pulled_at": now}},
        )
        return {
            "stop_signal": StopRule.INSUFFICIENT_SAMPLE.value,
            "_node_meta": {"reason": f"hogql failed: {type(e).__name__}: {str(e)[:120]}"},
        }

    a = counts["A"]
    b = counts["B"]

    # Demo-mode fallback: if PostHog returned no events, fabricate realistic stats
    # honouring the hypothesis's expected_direction. Tag the experiment as simulated.
    simulated = False
    if settings.DEMO_SIMULATE_TRAFFIC and a["n"] == 0 and b["n"] == 0:
        sim = _simulate_traffic(hyp.get("expected_direction", Direction.INCREASE.value))
        a = sim["A"]
        b = sim["B"]
        counts = sim
        simulated = True

    await ctx.db.experiments.update_one(
        {"_id": exp_id},
        {"$set": {
            "live_stats.variant_a": a,
            "live_stats.variant_b": b,
            "live_stats.last_pulled_at": now,
        }},
    )

    elapsed_min = (now - started_at.replace(tzinfo=timezone.utc) if started_at.tzinfo is None else now - started_at).total_seconds() / 60.0
    a_n, b_n = a["n"], b["n"]
    a_conv, b_conv = a[metric_field], b[metric_field]

    ci_result = None
    stop_signal: Optional[str] = None

    if a_n == 0 and b_n == 0:
        stop_signal = StopRule.INSUFFICIENT_SAMPLE.value
    elif elapsed_min > max_runtime_min:
        stop_signal = StopRule.MAX_RUNTIME.value
    elif a_n >= min_sample and b_n >= min_sample:
        ci_result = bootstrap_ci_lift(a_n, a_conv, b_n, b_conv)
        if ci_result and (ci_result[1] > 0 or ci_result[2] < 0):
            stop_signal = StopRule.CONVERGENCE.value
        else:
            stop_signal = StopRule.MIN_SAMPLE.value
    else:
        stop_signal = StopRule.INSUFFICIENT_SAMPLE.value

    await ctx.db.experiments.update_one(
        {"_id": exp_id},
        {"$set": {"stop_signal": stop_signal}},
    )

    update: dict[str, Any] = {
        "stop_signal": stop_signal,
        "_node_meta": {
            "a": a, "b": b,
            "elapsed_min": round(elapsed_min, 1),
            "primary_metric": primary_metric,
            "metric_field": metric_field,
            "simulated": simulated,
            "lift_ci": (
                {"lift": round(ci_result[0], 4), "ci_low": round(ci_result[1], 4), "ci_high": round(ci_result[2], 4)}
                if ci_result else None
            ),
        },
    }
    # Gate B is "confirm early-stop" — only pause when convergence triggers a stop.
    # Other signals (max_runtime / min_sample / insufficient_sample) flow straight to verdict.
    if stop_signal == StopRule.CONVERGENCE.value:
        update["pending_gate"] = "B"
    return update
