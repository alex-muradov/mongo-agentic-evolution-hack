"""evidence_sessions collection — PostHog session pulled into Mongo for analyst + replay summarizer."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import ConversionKind, VariantLabel


class HogEvent(BaseModel):
    """Trimmed PostHog event payload, kept for analyst replay reasoning."""
    model_config = ConfigDict(extra="ignore")

    name: str  # case_study_impression | case_study_click | phone_click | callback_form_submit | $feature_flag_called
    at: datetime
    properties: Dict[str, Any] = Field(default_factory=dict)


class EvidenceSession(BaseModel):
    """One PostHog session that landed on the experiment's page."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")  # PostHog session_id; gives natural idempotency on re-pulls
    experiment_id: str
    page_id: str
    variant: VariantLabel
    distinct_id: str

    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = Field(default=None, ge=0)

    events: List[HogEvent] = Field(default_factory=list)
    conversion: ConversionKind = ConversionKind.NONE

    replay_summary: Optional[str] = None  # written by replay_summarizer (streamed)
    replay_summary_at: Optional[datetime] = None

    pulled_at: datetime  # last HogQL refresh
