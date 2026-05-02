"""POST /v1/agent/runs (manual trigger), GET /v1/agent/runs[/{id}], POST /v1/agent/runs/{id}/resume."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent.runner import resume_run, start_run
from app.deps import require_api_key
from domain.enums import GateLetter, RunTrigger

router = APIRouter()


class StartRunIn(BaseModel):
    page_id: str = Field(default="areas/east-london")


class StartRunOut(BaseModel):
    run_id: str
    status: str = "started"


class ResumeOut(BaseModel):
    run_id: str
    resumed_at: str  # next node name
    status: str = "running"


@router.post(
    "/agent/runs",
    response_model=StartRunOut,
    dependencies=[Depends(require_api_key)],
    status_code=202,
)
async def trigger_run(payload: StartRunIn, request: Request) -> StartRunOut:
    run_id = await start_run(request.app.state.node_ctx, payload.page_id, RunTrigger.MANUAL)
    return StartRunOut(run_id=run_id)


@router.post(
    "/agent/runs/{run_id}/resume",
    response_model=ResumeOut,
    dependencies=[Depends(require_api_key)],
)
async def resume(
    run_id: str,
    request: Request,
    after_gate: GateLetter = Query(..., description="A | B | C"),
) -> ResumeOut:
    try:
        next_node = await resume_run(request.app.state.node_ctx, run_id, after_gate.value)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ResumeOut(run_id=run_id, resumed_at=next_node)


def _isoize(d: dict, fields: tuple[str, ...]) -> None:
    from datetime import datetime
    for k in fields:
        v = d.get(k)
        if isinstance(v, datetime):
            d[k] = v.isoformat()


@router.get(
    "/agent/runs/{run_id}",
    dependencies=[Depends(require_api_key)],
)
async def get_run(run_id: str, request: Request) -> dict:
    db = request.app.state.db
    doc = await db.agent_runs.find_one({"_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="run not found")
    _isoize(doc, ("started_at", "updated_at", "completed_at"))
    for entry in doc.get("log_tail") or []:
        _isoize(entry, ("at",))

    # Embed linked artefacts for the HITL UI (single round-trip).
    if doc.get("current_hypothesis_id"):
        h = await db.hypotheses.find_one({"_id": doc["current_hypothesis_id"]})
        if h:
            _isoize(h, ("created_at", "approved_at"))
            doc["hypothesis"] = h
    if doc.get("current_experiment_id"):
        e = await db.experiments.find_one({"_id": doc["current_experiment_id"]})
        if e:
            _isoize(e, ("started_at", "ended_at"))
            ls = e.get("live_stats") or {}
            _isoize(ls, ("last_pulled_at",))
            doc["experiment"] = e
    if doc.get("current_verdict_id"):
        v = await db.verdicts.find_one({"_id": doc["current_verdict_id"]})
        if v:
            _isoize(v, ("created_at", "approved_at"))
            doc["verdict"] = v
    if doc.get("current_learning_id"):
        l = await db.learnings.find_one({"_id": doc["current_learning_id"]})
        if l:
            _isoize(l, ("created_at",))
            l.pop("embedding", None)  # too big for UI, drop
            doc["learning"] = l
    return doc


@router.get(
    "/agent/runs",
    dependencies=[Depends(require_api_key)],
)
async def list_runs(request: Request, limit: int = 20) -> dict:
    db = request.app.state.db
    cursor = db.agent_runs.find({}).sort("started_at", -1).limit(limit)
    items = []
    hyp_ids: set[str] = set()
    async for doc in cursor:
        for k in ("started_at", "updated_at", "completed_at"):
            if doc.get(k):
                doc[k] = doc[k].isoformat()
        doc.pop("log_tail", None)
        if doc.get("current_hypothesis_id"):
            hyp_ids.add(doc["current_hypothesis_id"])
        items.append(doc)
    # one batched fetch of hypothesis statements for the sidebar
    statements: dict[str, str] = {}
    if hyp_ids:
        async for h in db.hypotheses.find({"_id": {"$in": list(hyp_ids)}}, {"statement": 1}):
            statements[h["_id"]] = h.get("statement") or ""
    for doc in items:
        hid = doc.get("current_hypothesis_id")
        if hid and hid in statements:
            doc["hypothesis_summary"] = statements[hid]
    return {"items": items}
