"""learnings collection — reflect-node output, RAG-retrievable for future proposers."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import ServiceType


class Learning(BaseModel):
    """One distilled lesson from a completed experiment. Embedded for vector retrieval."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")
    run_id: str
    experiment_id: str
    hypothesis_id: str
    verdict_id: str
    page_id: str

    borough: Optional[str] = None
    service_type: Optional[ServiceType] = None

    what_worked: str
    reasoning: str  # narrative; embedded source = what_worked + "\n" + reasoning
    counter_factors: Optional[str] = None  # what would invalidate this lesson
    related_hypothesis_ids: List[str] = Field(default_factory=list)

    embedding: Optional[List[float]] = None  # 1024 dims (voyage-4-large), cosine, unit-norm — same contract as case_studies

    created_at: datetime
