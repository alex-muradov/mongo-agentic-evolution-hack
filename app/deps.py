"""Settings + shared FastAPI dependencies."""
from typing import Optional

from fastapi import Header, HTTPException, status
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    MONGODB_URI: str
    MONGODB_DB: str = "agentic_evolution"
    INGEST_API_KEY: str
    REVALIDATE_SECRET: Optional[str] = None  # used by T6 dispatcher when calling Gabik's /api/revalidate
    NEXT_REVALIDATE_URL: Optional[str] = None  # Gabik's Vercel /api/revalidate URL; set in T6
    MONGODB_AI_KEY: Optional[str] = None  # if absent, ingest skips embedding (smoke without Atlas)
    OPENAI_API_KEY: Optional[str] = None  # required for proposer LLM (T5+); absent → proposer raises

    # PostHog (read-only): analyst HogQL pulls + replay_summarizer in T6+
    POSTHOG_HOST: str = "https://eu.posthog.com"
    POSTHOG_PROJECT_ID: Optional[int] = None
    POSTHOG_PERSONAL_API_KEY: Optional[str] = None

    DEMO_SIMULATE_TRAFFIC: bool = False  # when true, analyst fabricates realistic stats if PostHog returns 0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()  # type: ignore[call-arg]


async def require_api_key(x_api_key: str = Header(...)) -> None:
    if x_api_key != settings.INGEST_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid x-api-key")
