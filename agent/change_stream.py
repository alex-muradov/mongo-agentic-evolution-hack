"""Watch case_studies inserts; in T4 we log only. Auto-trigger lands in T5+."""
import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

log = logging.getLogger("agent.change_stream")


async def case_studies_watcher(db: AsyncIOMotorDatabase) -> None:
    pipeline = [{"$match": {"operationType": "insert"}}]
    while True:
        try:
            async with db.case_studies.watch(pipeline, full_document="updateLookup") as stream:
                log.info("change_stream: watching agentic_evolution.case_studies inserts")
                async for change in stream:
                    doc = change.get("fullDocument") or {}
                    log.info(
                        "case_study insert: id=%s page_id=%s borough=%s service_type=%s",
                        doc.get("_id"), doc.get("page_id"), doc.get("borough"), doc.get("service_type"),
                    )
        except asyncio.CancelledError:
            log.info("change_stream: cancelled, exiting")
            raise
        except Exception as e:
            log.warning("change_stream: %s — reconnecting in 5s", type(e).__name__)
            await asyncio.sleep(5)
