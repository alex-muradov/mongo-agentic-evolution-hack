"""Mongo index management — regular + Atlas Search/Vector indexes."""
from motor.motor_asyncio import AsyncIOMotorDatabase

CASE_STUDIES_VEC = "case_studies_vec"
LEARNINGS_VEC = "learnings_vec"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.case_studies.create_index([("page_id", 1), ("variant", 1)])
    await db.agent_runs.create_index([("status", 1), ("started_at", -1)])
    await db.agent_runs.create_index([("page_id", 1), ("started_at", -1)])
    await db.learnings.create_index([("page_id", 1), ("borough", 1)])


async def ensure_search_indexes(db: AsyncIOMotorDatabase) -> bool:
    """Create the case_studies + learnings vector indexes if absent. Atlas-only."""
    try:
        cs_existing = [idx async for idx in db.case_studies.list_search_indexes()]
    except Exception:
        return False

    if not any(idx.get("name") == CASE_STUDIES_VEC for idx in cs_existing):
        await db.case_studies.create_search_index({
            "name": CASE_STUDIES_VEC, "type": "vectorSearch",
            "definition": {"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"},
                {"type": "filter", "path": "borough"},
                {"type": "filter", "path": "postcode_outward"},
                {"type": "filter", "path": "service_type"},
                {"type": "filter", "path": "completed_at"},
                {"type": "filter", "path": "outcome"},
                {"type": "filter", "path": "schema_version"},
            ]},
        })

    lr_existing = [idx async for idx in db.learnings.list_search_indexes()]
    if not any(idx.get("name") == LEARNINGS_VEC for idx in lr_existing):
        await db.learnings.create_search_index({
            "name": LEARNINGS_VEC, "type": "vectorSearch",
            "definition": {"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"},
                {"type": "filter", "path": "page_id"},
                {"type": "filter", "path": "borough"},
                {"type": "filter", "path": "service_type"},
            ]},
        })
    return True
