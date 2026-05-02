"""Inbound DTOs for ingest endpoints."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.case_study import CustomerFeedback
from domain.enums import CaseStudyOutcome, CaseStudySource, PriceBand, ServiceType
from domain.validators import (
    SERVICE_TAG_MAX,
    TITLE_MAX,
    validate_postcode_outward,
    validate_street,
    validate_summary,
)


class CaseStudyCandidateIn(BaseModel):
    """POST /v1/ingest/case-study request body — Wild Coral after trader_verdict."""
    model_config = ConfigDict(extra="forbid")

    source: CaseStudySource
    source_job_id: str
    partner: str
    page_id: str

    borough: str
    postcode_outward: str
    service_type: ServiceType
    service_tag: str = Field(..., max_length=SERVICE_TAG_MAX)

    completed_at: datetime
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    price_band: Optional[PriceBand] = None

    problem: str
    solution: str
    outcome: CaseStudyOutcome
    title: str = Field(..., max_length=TITLE_MAX)
    summary: str
    street: str

    customer_feedback: Optional[CustomerFeedback] = None

    @field_validator("postcode_outward")
    @classmethod
    def _v_postcode(cls, v: str) -> str:
        return validate_postcode_outward(v)

    @field_validator("street")
    @classmethod
    def _v_street(cls, v: str) -> str:
        return validate_street(v)

    @field_validator("summary")
    @classmethod
    def _v_summary(cls, v: str) -> str:
        return validate_summary(v)


class IngestAck(BaseModel):
    id: str
    status: str  # "queued_for_embedding" | "duplicate"
