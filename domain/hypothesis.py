"""hypotheses collection — proposer output, awaiting gate A approval."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import Direction, HypothesisStatus


class Hypothesis(BaseModel):
    """One A/B test idea drafted by the proposer node."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")
    run_id: str
    page_id: str

    statement: str
    rationale: str
    expected_metric: str  # primary; expected one of "phone_click" | "callback_form_submit"
    secondary_metrics: List[str] = Field(default_factory=list)
    expected_direction: Direction
    expected_effect_size: Optional[str] = None  # human-readable, e.g. "≥15% lift"

    rag_sources: List[str] = Field(default_factory=list)  # case_study + learning ids used
    open_questions_delta: List[str] = Field(default_factory=list)

    status: HypothesisStatus = HypothesisStatus.PROPOSED
    created_at: datetime

    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rejection_reason: Optional[str] = None
