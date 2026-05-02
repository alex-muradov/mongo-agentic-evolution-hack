"""AutoResearch FastAPI entrypoint."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI

from agent.change_stream import case_studies_watcher
from agent.context import NodeContext
from app.deps import settings
from app.routes import agent, case_studies
from integrations.embeddings import make_client
from mongo.indexes import ensure_indexes, ensure_search_indexes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB]
    await ensure_indexes(db)
    await ensure_search_indexes(db)

    voyage_client = make_client(settings.MONGODB_AI_KEY) if settings.MONGODB_AI_KEY else None
    openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

    app.state.db = db
    app.state.ai_client = voyage_client  # back-compat for ingest path
    app.state.node_ctx = NodeContext(db=db, voyage_client=voyage_client, openai_client=openai_client)

    watcher_task = asyncio.create_task(case_studies_watcher(db))

    try:
        yield
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        client.close()


app = FastAPI(title="AutoResearch", version="0.0.1", lifespan=lifespan)
app.include_router(case_studies.router, prefix="/v1")
app.include_router(agent.router, prefix="/v1")


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
