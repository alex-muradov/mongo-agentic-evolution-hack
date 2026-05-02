"""POST /v1/ingest/case-study + GET /v1/case-studies."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pymongo.errors import DuplicateKeyError

from app.deps import require_api_key
from app.schemas.ingest import CaseStudyCandidateIn, IngestAck
from domain.case_study import CaseStudyInternal
from integrations.embeddings import embed_text

router = APIRouter()


def _make_id(source: str, source_job_id: str) -> str:
    return f"cs_{source}_{source_job_id}"


@router.post(
    "/ingest/case-study",
    status_code=202,
    response_model=IngestAck,
    dependencies=[Depends(require_api_key)],
)
async def ingest_case_study(payload: CaseStudyCandidateIn, request: Request) -> IngestAck:
    db = request.app.state.db
    ai_client = request.app.state.ai_client
    now = datetime.now(timezone.utc)
    doc_id = _make_id(payload.source.value, payload.source_job_id)

    candidate_dict = payload.model_dump(exclude={"partner"})
    internal = CaseStudyInternal.model_validate(
        {
            "_id": doc_id,
            **candidate_dict,
            "pii_strip_version": "v1",
            "created_at": now,
            "updated_at": now,
        }
    )
    doc = internal.model_dump(by_alias=True)

    if ai_client is not None:
        try:
            doc["embedding"] = await embed_text(ai_client, internal.summary)
        except Exception:
            doc["embedding"] = None

    try:
        await db.case_studies.insert_one(doc)
        return IngestAck(id=doc_id, status="queued_for_embedding")
    except DuplicateKeyError:
        return IngestAck(id=doc_id, status="duplicate")


@router.get(
    "/case-studies",
    dependencies=[Depends(require_api_key)],
)
async def list_case_studies(
    request: Request,
    area: str = Query(..., min_length=1),
    variant: Optional[str] = None,
) -> dict:
    db = request.app.state.db
    page_id = f"areas/{area}"

    if variant:
        filter_q = {"page_id": page_id, "$or": [{"variant": variant}, {"variant": None}]}
    else:
        filter_q = {"page_id": page_id, "variant": None}

    # Sort variant-tagged docs first, null fillers after — so the experiment's chosen 5 lead the section.
    pipeline = [
        {"$match": filter_q},
        {"$addFields": {"_var_priority": {"$cond": [{"$eq": ["$variant", None]}, 1, 0]}}},
        {"$sort": {"_var_priority": 1, "completed_at": -1}},
        {"$limit": 20},
    ]
    items: list[dict] = []
    async for doc in db.case_studies.aggregate(pipeline):
        doc.pop("_var_priority", None)
        try:
            internal = CaseStudyInternal.model_validate(doc)
            items.append(internal.to_public().model_dump())
        except Exception:
            continue
    return {"items": items}
