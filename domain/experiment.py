"""experiments collection — dispatched A/B test, in-flight or stopped."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import ExperimentStatus, StopRule, VariantLabel


class VariantSpec(BaseModel):
    """Which case_studies render under which PostHog variant label."""
    model_config = ConfigDict(extra="forbid")

    label: VariantLabel
    case_study_ids: List[str]


class ArmStats(BaseModel):
    """Per-arm aggregate counts. Both metrics tracked: phone_click is primary, callback_form_submit secondary."""
    model_config = ConfigDict(extra="forbid")

    n: int = Field(default=0, ge=0)
    phone_click: int = Field(default=0, ge=0)
    callback_form_submit: int = Field(default=0, ge=0)


class LiveStats(BaseModel):
    """Refreshed on each HogQL pull from PostHog."""
    model_config = ConfigDict(extra="forbid")

    variant_a: ArmStats = Field(default_factory=ArmStats)
    variant_b: ArmStats = Field(default_factory=ArmStats)
    last_pulled_at: Optional[datetime] = None


class Experiment(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")
    run_id: str
    hypothesis_id: str
    page_id: str

    posthog_flag_key: str  # case_studies_v1
    variant_a: VariantSpec
    variant_b: VariantSpec

    started_at: datetime
    ended_at: Optional[datetime] = None
    min_sample_per_arm: int = Field(default=80, ge=1)
    max_runtime_minutes: int = Field(default=90, ge=1)

    live_stats: LiveStats = Field(default_factory=LiveStats)
    stop_signal: Optional[StopRule] = None
    status: ExperimentStatus = ExperimentStatus.RUNNING
