"""Sequential async runner with HITL-gate pause/resume support."""
import asyncio
import secrets
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from agent import nodes
from agent.context import NodeContext
from domain.enums import RunStatus, RunTrigger

LOG_TAIL_MAX = 20

PIPELINE: list[tuple[str, Any]] = [
    ("proposer", nodes.proposer),
    ("dispatcher", nodes.dispatcher),
    ("analyst", nodes.analyst),
    ("replay_summarizer", nodes.replay_summarizer),
    ("verdict_node", nodes.verdict_node),
    ("reflect", nodes.reflect),
]

# Gate letter -> name of the node to RESUME at (the node AFTER the gate pauses)
GATE_RESUMES_AT = {"A": "dispatcher", "B": "replay_summarizer", "C": "reflect"}


def _new_run_id(page_id: str) -> str:
    slug = page_id.replace("/", "_").replace("-", "_")
    return f"run_{slug}_{secrets.token_hex(4)}"


async def _push_log(db: AsyncIOMotorDatabase, run_id: str, node: str, msg: str) -> None:
    entry = {"at": datetime.now(timezone.utc), "node": node, "msg": msg}
    await db.agent_runs.update_one(
        {"_id": run_id},
        {
            "$push": {"log_tail": {"$each": [entry], "$slice": -LOG_TAIL_MAX}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )


async def start_run(
    ctx: NodeContext,
    page_id: str,
    trigger: RunTrigger = RunTrigger.MANUAL,
) -> str:
    run_id = _new_run_id(page_id)
    now = datetime.now(timezone.utc)
    doc = {
        "_id": run_id,
        "page_id": page_id,
        "status": RunStatus.RUNNING.value,
        "current_node": "queued",
        "pending_gate": None,
        "iteration": 1,
        "trigger": trigger.value,
        "current_hypothesis_id": None,
        "current_experiment_id": None,
        "current_verdict_id": None,
        "log_tail": [{"at": now, "node": "runner", "msg": f"run started ({trigger.value})"}],
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    await ctx.db.agent_runs.insert_one(doc)
    asyncio.create_task(_drive(ctx, run_id, page_id, start_at="proposer", iteration=1))
    return run_id


async def resume_run(ctx: NodeContext, run_id: str, after_gate: str) -> str:
    if after_gate not in GATE_RESUMES_AT:
        raise ValueError(f"unknown gate {after_gate!r}")

    run = await ctx.db.agent_runs.find_one({"_id": run_id})
    if not run:
        raise LookupError(f"run not found: {run_id}")

    next_node = GATE_RESUMES_AT[after_gate]
    await ctx.db.agent_runs.update_one(
        {"_id": run_id},
        {"$set": {
            "status": RunStatus.RUNNING.value,
            "pending_gate": None,
            "current_node": next_node,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    await _push_log(ctx.db, run_id, "runner", f"gate {after_gate} approved → resuming at {next_node}")
    asyncio.create_task(_drive(ctx, run_id, run["page_id"], start_at=next_node, iteration=run.get("iteration", 1)))
    return next_node


async def _drive(ctx: NodeContext, run_id: str, page_id: str, start_at: str, iteration: int) -> None:
    state: dict[str, Any] = {"run_id": run_id, "page_id": page_id, "iteration": iteration}

    # On resume, hydrate state from the agent_run doc so downstream nodes see
    # ids set by upstream nodes that ran in a previous _drive task.
    if start_at != "proposer":
        prior = await ctx.db.agent_runs.find_one({"_id": run_id})
        if prior:
            for k in ("current_hypothesis_id", "current_experiment_id", "current_verdict_id"):
                if prior.get(k):
                    state[k] = prior[k]

    start_idx = next((i for i, (n, _) in enumerate(PIPELINE) if n == start_at), 0)

    try:
        for label, fn in PIPELINE[start_idx:]:
            await ctx.db.agent_runs.update_one(
                {"_id": run_id},
                {"$set": {"current_node": label, "updated_at": datetime.now(timezone.utc)}},
            )
            await _push_log(ctx.db, run_id, label, f"entering {label}")

            update = await fn(state, ctx)
            state.update(update)

            persist: dict[str, Any] = {}
            for k in ("current_hypothesis_id", "current_experiment_id", "current_verdict_id"):
                if k in update:
                    persist[k] = update[k]
            if persist:
                persist["updated_at"] = datetime.now(timezone.utc)
                await ctx.db.agent_runs.update_one({"_id": run_id}, {"$set": persist})

            meta = update.get("_proposer_meta") or update.get("_node_meta")
            log_msg = f"{label} ok"
            if meta:
                log_msg += f": {meta}"
            elif persist:
                log_msg += f": {persist}"
            await _push_log(ctx.db, run_id, label, log_msg)

            if update.get("pending_gate"):
                gate = update["pending_gate"]
                status_val = {"A": RunStatus.AWAITING_GATE_A, "B": RunStatus.AWAITING_GATE_B, "C": RunStatus.AWAITING_GATE_C}[gate].value
                await ctx.db.agent_runs.update_one(
                    {"_id": run_id},
                    {"$set": {
                        "status": status_val,
                        "pending_gate": gate,
                        "updated_at": datetime.now(timezone.utc),
                    }},
                )
                await _push_log(ctx.db, run_id, "runner", f"paused at gate {gate}")
                return

        now = datetime.now(timezone.utc)
        await ctx.db.agent_runs.update_one(
            {"_id": run_id},
            {"$set": {
                "status": RunStatus.COMPLETED.value,
                "current_node": "completed",
                "completed_at": now,
                "updated_at": now,
            }},
        )
        await _push_log(ctx.db, run_id, "runner", "run completed")
    except Exception as e:
        await ctx.db.agent_runs.update_one(
            {"_id": run_id},
            {"$set": {
                "status": RunStatus.ABORTED.value,
                "current_node": "error",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        await _push_log(ctx.db, run_id, "runner", f"aborted: {type(e).__name__}: {str(e)[:160]}")
