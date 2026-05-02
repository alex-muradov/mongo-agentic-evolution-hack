"""Embedding helper — voyage-4-large via MongoDB AI gateway (OpenAI-compatible)."""
from openai import AsyncOpenAI

EMBEDDING_MODEL = "voyage-4-large"
EMBEDDING_DIMS = 1024
GATEWAY_BASE_URL = "https://ai.mongodb.com/v1"


def make_client(api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=GATEWAY_BASE_URL, api_key=api_key)


async def embed_text(client: AsyncOpenAI, text: str) -> list[float]:
    """Embed and unit-normalize. Raises on API failure — caller decides whether to skip."""
    r = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    v = r.data[0].embedding
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v] if n > 0 else v
