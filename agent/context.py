"""Shared context passed into every node."""
from dataclasses import dataclass
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI


@dataclass
class NodeContext:
    db: AsyncIOMotorDatabase
    voyage_client: Optional[AsyncOpenAI]   # ai.mongodb.com — embeddings
    openai_client: Optional[AsyncOpenAI]   # api.openai.com — chat-completions
