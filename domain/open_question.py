"""open_questions collection — explicit research-agenda artifact (locked design decision #3)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import OpenQuestionStatus, ServiceType


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")
    question: str
    raised_by: str  # node name: proposer | analyst | reflect
    raised_at: datetime
    raised_in_run_id: str

    related_hypothesis_id: Optional[str] = None
    related_verdict_id: Optional[str] = None

    page_id: Optional[str] = None
    borough: Optional[str] = None
    service_type: Optional[ServiceType] = None

    status: OpenQuestionStatus = OpenQuestionStatus.OPEN
    answered_in_verdict_id: Optional[str] = None
    answered_at: Optional[datetime] = None
    answer_summary: Optional[str] = None
