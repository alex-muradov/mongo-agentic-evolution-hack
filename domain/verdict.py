"""verdicts collection — analyst output, post-gate-C final call."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import Confidence, Direction, StopRule, VerdictStatus


class MetricArmStat(BaseModel):
    """Per-arm conversion stat for one metric, frozen at verdict time."""
    model_config = ConfigDict(extra="forbid")

    n: int = Field(..., ge=0)
    conv: int = Field(..., ge=0)
    rate: float = Field(..., ge=0.0, le=1.0)


class MetricResult(BaseModel):
    """Stats for one metric across both arms, with bootstrap CI on the lift."""
    model_config = ConfigDict(extra="forbid")

    name: str  # phone_click | callback_form_submit
    variant_a: MetricArmStat
    variant_b: MetricArmStat
    lift: float  # (rate_b - rate_a) / rate_a; can be negative
    ci_method: str = "bootstrap"
    ci_low: float
    ci_high: float
    direction: Direction


class HitlEdit(BaseModel):
    """Audit-trail row for a human edit at gate C."""
    model_config = ConfigDict(extra="forbid")

    by: str
    at: datetime
    field: str
    before: Optional[str] = None
    after: Optional[str] = None


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")
    run_id: str
    experiment_id: str
    hypothesis_id: str

    status: VerdictStatus
    primary_metric: MetricResult
    secondary_metrics: List[MetricResult] = Field(default_factory=list)
    stop_rule: StopRule
    reasoning: str
    replay_evidence_refs: List[str] = Field(default_factory=list)
    confidence: Confidence
    counter_evidence: Optional[str] = None
    generated_open_questions: List[str] = Field(default_factory=list)

    hitl_edits: List[HitlEdit] = Field(default_factory=list)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    created_at: datetime
