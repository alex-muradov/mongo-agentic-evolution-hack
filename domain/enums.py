"""Closed enums shared across collections. str-based for clean Mongo + JSON storage."""
from enum import Enum


class CaseStudySource(str, Enum):
    LIVE_DEBRIEF = "live_debrief"
    HISTORICAL_FIREBASE = "historical_firebase"


class ServiceType(str, Enum):
    EMERGENCY_LOCKOUT = "emergency_lockout"
    LOCK_CHANGE = "lock_change"
    SAFE_OPENING = "safe_opening"
    KEY_EXTRACTION = "key_extraction"
    UPVC_REPAIR = "upvc_repair"
    SECURITY_AUDIT = "security_audit"


class PriceBand(str, Enum):
    """Internal-only — never serialised to public CaseStudyPublic."""
    UP_TO_80 = "0-80"
    BAND_80_150 = "80-150"
    BAND_150_300 = "150-300"
    OVER_300 = "300+"


class CaseStudyOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    REFERRED = "referred"


class VariantLabel(str, Enum):
    """Mirrors PostHog multivariate flag `case_studies_v1`. Extend if flag grows."""
    CONTROL = "control"
    A = "A"
    B = "B"


class Direction(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    NO_CHANGE = "no_change"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    MEASURED = "measured"


class ExperimentStatus(str, Enum):
    RUNNING = "running"
    AWAITING_STOP_CONFIRM = "awaiting_stop_confirm"
    STOPPED = "stopped"


class StopRule(str, Enum):
    CONVERGENCE = "convergence"
    MIN_SAMPLE = "min_sample"
    MAX_RUNTIME = "max_runtime"
    EARLY_STOP = "early_stop"
    MANUAL = "manual"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


class VerdictStatus(str, Enum):
    CONFIRMED_HIGH = "confirmed-high"
    CONFIRMED_DIRECTIONAL = "confirmed-directional"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class Confidence(str, Enum):
    HIGH = "high"
    DIRECTIONAL = "directional"
    LOW = "low"


class RunStatus(str, Enum):
    RUNNING = "running"
    AWAITING_GATE_A = "awaiting_gate_a"
    AWAITING_GATE_B = "awaiting_gate_b"
    AWAITING_GATE_C = "awaiting_gate_c"
    COMPLETED = "completed"
    ABORTED = "aborted"


class RunTrigger(str, Enum):
    CHANGE_STREAM = "change_stream"
    MANUAL = "manual"


class GateLetter(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class ConversionKind(str, Enum):
    NONE = "none"
    PHONE_CLICK = "phone_click"
    CALLBACK_FORM_SUBMIT = "callback_form_submit"
    BOTH = "both"


class OpenQuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    DEPRIORITIZED = "deprioritized"
