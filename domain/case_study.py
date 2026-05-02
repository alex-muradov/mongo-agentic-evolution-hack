"""case_studies collection — internal Mongo doc + public API projection."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    CaseStudyOutcome,
    CaseStudySource,
    PriceBand,
    ServiceType,
    VariantLabel,
)
from .validators import (
    SERVICE_TAG_MAX,
    TITLE_MAX,
    validate_postcode_outward,
    validate_street,
    validate_summary,
)


class CaseStudyPublic(BaseModel):
    """Exact contract for Gabik's Next.js frontend (docs/case-studies-handoff.md §1)."""
    model_config = ConfigDict(extra="forbid")

    id: str
    variant: Optional[str] = None
    postcode: str
    street: str
    serviceTag: str = Field(..., max_length=SERVICE_TAG_MAX)
    title: str = Field(..., max_length=TITLE_MAX)
    summary: str

    @field_validator("postcode")
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


class CustomerFeedback(BaseModel):
    """Consent-gated; only present when source=live_debrief and customer opted in."""
    model_config = ConfigDict(extra="forbid")

    quote: Optional[str] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    consent_given_at: Optional[datetime] = None


class PiiRedaction(BaseModel):
    """Audit trail row of what the PII pass stripped from inbound text."""
    model_config = ConfigDict(extra="forbid")

    category: str  # name | address | phone | email | lock_serial | other
    count: int = Field(..., ge=0)
    samples_hashed: List[str] = Field(default_factory=list)


class CaseStudyInternal(BaseModel):
    """Source-of-truth document in MongoDB `case_studies`. Public API serves to_public()."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")
    schema_version: str = "v1"  # bumped on breaking change; cross-team boundary marker
    source: CaseStudySource
    source_job_id: str
    page_id: str

    borough: str
    postcode_outward: str
    service_type: ServiceType
    service_tag: str = Field(..., max_length=SERVICE_TAG_MAX)

    completed_at: datetime
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    price_band: Optional[PriceBand] = None  # internal-only

    problem: str
    solution: str
    outcome: CaseStudyOutcome
    title: str = Field(..., max_length=TITLE_MAX)
    summary: str
    street: str

    customer_feedback: Optional[CustomerFeedback] = None

    embedding: Optional[List[float]] = None  # 1024 dims (voyage-4-large via ai.mongodb.com)
    pii_redactions: List[PiiRedaction] = Field(default_factory=list)
    pii_strip_version: str

    variant: Optional[VariantLabel] = None

    created_at: datetime
    updated_at: datetime

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

    def to_public(self) -> CaseStudyPublic:
        return CaseStudyPublic(
            id=self.id,
            variant=self.variant.value if self.variant else None,
            postcode=self.postcode_outward,
            street=self.street,
            serviceTag=self.service_tag,
            title=self.title,
            summary=self.summary,
        )
