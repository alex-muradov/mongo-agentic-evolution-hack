"""agent_runs collection — top-level lifecycle of one closed-loop research iteration."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import GateLetter, RunStatus, RunTrigger


class LogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    node: str
    msg: str


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")  # e.g. "run_2026_05_02_e8_001"
    page_id: str

    status: RunStatus = RunStatus.RUNNING
    current_node: str
    pending_gate: Optional[GateLetter] = None

    iteration: int = Field(default=1, ge=1)  # demo cap = 3, enforced in graph
    trigger: RunTrigger

    current_hypothesis_id: Optional[str] = None
    current_experiment_id: Optional[str] = None
    current_verdict_id: Optional[str] = None

    log_tail: List[LogEntry] = Field(default_factory=list)  # last ~20 events

    started_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
