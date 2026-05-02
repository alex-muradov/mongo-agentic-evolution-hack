"""Content/PII validators per docs/case-studies-handoff.md §1 (hard rules) + §5 (checklist)."""
import re

POSTCODE_OUTWARD_RE = re.compile(r"^E[A-Z0-9]{1,3}$|^[A-Z]{1,2}[0-9]{1,2}$")
STREET_LEADING_OK_RE = re.compile(r"^[^\d]")

PRICE_SYMBOL_RE = re.compile(r"[£$]|EUR\b", re.IGNORECASE)
VAT_RE = re.compile(r"\+\s*vat", re.IGNORECASE)
# Tighter than handoff §5 spec: requires currency qualifier to avoid false-positives
# on legitimate copy like "5-lever lock" or "3 cylinders". See open-question to Gabik.
PRICE_QUALIFIED_RE = re.compile(r"\b\d+(\.\d+)?\s*(quid|pounds|gbp)\b", re.IGNORECASE)
EMAIL_AT_RE = re.compile(r"@")
UK_MOBILE_RE = re.compile(r"\b07\d{9}\b")

SERVICE_TAG_MAX = 24
TITLE_MAX = 70
SUMMARY_MIN_WORDS = 40
SUMMARY_MAX_WORDS = 80


def validate_postcode_outward(value: str) -> str:
    if not POSTCODE_OUTWARD_RE.match(value):
        raise ValueError(
            f"postcode must be outward code only (e.g. 'E5', 'N1', 'SW1'), got {value!r}"
        )
    return value


def validate_street(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("street is empty")
    if not STREET_LEADING_OK_RE.match(v):
        raise ValueError(f"street must not start with a digit; got {value!r}")
    return v


def validate_summary(value: str) -> str:
    """Enforces handoff §1 voice/length and §5 PII bans. First violation wins."""
    if PRICE_SYMBOL_RE.search(value):
        raise ValueError("summary contains £/$/EUR (prices banned in public copy)")
    if VAT_RE.search(value):
        raise ValueError("summary contains '+ vat' (banned)")
    if PRICE_QUALIFIED_RE.search(value):
        raise ValueError("summary contains a price token (banned)")
    if EMAIL_AT_RE.search(value):
        raise ValueError("summary contains '@' (email PII)")
    if UK_MOBILE_RE.search(value):
        raise ValueError("summary contains a UK mobile pattern (PII)")
    n = len(value.split())
    if n < SUMMARY_MIN_WORDS:
        raise ValueError(f"summary has {n} words; min {SUMMARY_MIN_WORDS}")
    if n > SUMMARY_MAX_WORDS:
        raise ValueError(f"summary has {n} words; max {SUMMARY_MAX_WORDS}")
    return value
