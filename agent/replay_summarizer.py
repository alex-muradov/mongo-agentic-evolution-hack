"""replay_summarizer — pulls PostHog session recordings + events, LLM-summarizes per session, writes to evidence_sessions."""
from datetime import datetime, timezone
from typing import Any, Optional

from agent.context import NodeContext
from app.deps import settings
from domain.enums import ConversionKind, VariantLabel
from integrations.posthog import PostHogClient

SUMMARIZER_LLM_MODEL = "gpt-4o-mini"
MAX_RECORDINGS_PER_RUN = 10
MAX_EVENTS_PER_SESSION = 50

CONVERSION_PHONE = {"phone_click", "phone_call_clicked"}
CONVERSION_FORM = {"callback_form_submit", "callback_form_submitted"}

SUMMARY_SYSTEM_PROMPT = """You write a single concise summary of a user session on a locksmith landing page (`/areas/east-london`).

Inputs: chronological event stream, the variant the user was bucketed into, and whether they converted.

Constraints:
- 25-50 words, plain English, third person ("the user").
- Behavioural and concrete: name what they did (scrolled, clicked card, hit phone link, left).
- No marketing language, no interpretation of intent — just observed behaviour.
- If they converted, name the conversion event explicitly. If they didn't, name the last visible action.

Return ONLY the summary text (no JSON, no preamble)."""


def _make_posthog() -> Optional[PostHogClient]:
    if not (settings.POSTHOG_HOST and settings.POSTHOG_PROJECT_ID and settings.POSTHOG_PERSONAL_API_KEY):
        return None
    return PostHogClient(
        host=settings.POSTHOG_HOST,
        project_id=settings.POSTHOG_PROJECT_ID,
        personal_api_key=settings.POSTHOG_PERSONAL_API_KEY,
    )


def _classify_conversion(event_names: set[str]) -> str:
    has_phone = bool(event_names & CONVERSION_PHONE)
    has_form = bool(event_names & CONVERSION_FORM)
    if has_phone and has_form:
        return ConversionKind.BOTH.value
    if has_phone:
        return ConversionKind.PHONE_CLICK.value
    if has_form:
        return ConversionKind.CALLBACK_FORM_SUBMIT.value
    return ConversionKind.NONE.value


def _format_events_for_llm(events: list[dict]) -> str:
    lines = []
    for e in events[:MAX_EVENTS_PER_SESSION]:
        ts = (e.get("timestamp") or "")[11:19]
        evt = e.get("event") or "?"
        props = e.get("properties") or {}
        variant = props.get("variant") if isinstance(props, dict) else None
        suffix = f" [variant={variant}]" if variant else ""
        lines.append(f"  {ts}  {evt}{suffix}")
    return "\n".join(lines) or "  (no events)"


async def _summarize_session(client, events: list[dict], variant: str, conversion: str) -> str:
    user_prompt = f"""Variant: {variant}
Conversion: {conversion}
Event stream:
{_format_events_for_llm(events)}

Write the 25-50 word summary."""
    r = await client.chat.completions.create(
        model=SUMMARIZER_LLM_MODEL,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=200,
    )
    return r.choices[0].message.content.strip()


async def _pull_session_events(posthog: PostHogClient, session_id: str) -> list[dict]:
    """Pull chronological events for a single session via HogQL."""
    safe_sid = session_id.replace("'", "")
    query = f"""
SELECT event, timestamp, properties
FROM events
WHERE properties."$session_id" = '{safe_sid}'
ORDER BY timestamp
LIMIT {MAX_EVENTS_PER_SESSION}
"""
    try:
        return await posthog.hogql(query)
    except Exception:
        return []


def _detect_variant(events: list[dict]) -> Optional[str]:
    for e in events:
        props = e.get("properties") or {}
        if isinstance(props, dict):
            v = props.get("variant") or props.get("$feature_flag_response")
            if v in ("A", "B"):
                return v
    return None


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


async def replay_summarizer(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    exp_id = state.get("current_experiment_id")
    if not exp_id:
        raise RuntimeError("replay_summarizer: no current_experiment_id")
    exp = await ctx.db.experiments.find_one({"_id": exp_id})
    if not exp:
        raise RuntimeError(f"replay_summarizer: experiment {exp_id} not found")

    posthog = _make_posthog()
    if posthog is None or ctx.openai_client is None:
        return {"_node_meta": {"skipped": "PostHog or OpenAI client not configured"}}

    started_at: datetime = exp["started_at"]
    started_iso = started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        recordings = await posthog.list_recordings(date_from=started_iso, limit=MAX_RECORDINGS_PER_RUN)
    except Exception as e:
        return {"_node_meta": {"recordings_error": f"{type(e).__name__}: {str(e)[:120]}"}}

    if not recordings:
        return {"_node_meta": {"recordings_seen": 0, "summarized": 0}}

    summarized = 0
    skipped = 0
    refs: list[str] = []
    now = datetime.now(timezone.utc)

    for rec in recordings:
        sid = rec.get("id") or rec.get("session_id")
        if not sid:
            continue

        existing = await ctx.db.evidence_sessions.find_one({"_id": sid})
        if existing and existing.get("replay_summary"):
            refs.append(sid)
            continue

        events = await _pull_session_events(posthog, sid)
        variant = _detect_variant(events)
        if variant is None:
            skipped += 1
            continue  # session wasn't part of an A/B bucket

        event_names = {e.get("event") for e in events if e.get("event")}
        conversion = _classify_conversion(event_names)

        try:
            summary = await _summarize_session(ctx.openai_client, events, variant, conversion)
        except Exception as e:
            summary = f"(summarizer failed: {type(e).__name__})"

        events_compact = [
            {
                "name": e.get("event") or "?",
                "at": _parse_iso(e.get("timestamp")) or now,
                "properties": e.get("properties") or {},
            }
            for e in events[:MAX_EVENTS_PER_SESSION]
        ]

        await ctx.db.evidence_sessions.replace_one(
            {"_id": sid},
            {
                "_id": sid,
                "experiment_id": exp_id,
                "page_id": state["page_id"],
                "variant": variant,
                "distinct_id": rec.get("distinct_id") or "",
                "started_at": _parse_iso(rec.get("start_time")) or started_at,
                "ended_at": _parse_iso(rec.get("end_time")),
                "duration_seconds": rec.get("recording_duration"),
                "events": events_compact,
                "conversion": conversion,
                "replay_summary": summary,
                "replay_summary_at": now,
                "pulled_at": now,
            },
            upsert=True,
        )
        refs.append(sid)
        summarized += 1

    if refs:
        await ctx.db.experiments.update_one(
            {"_id": exp_id},
            {"$set": {"replay_session_ids": refs}},
        )

    return {
        "_node_meta": {
            "recordings_seen": len(recordings),
            "summarized": summarized,
            "skipped_no_variant": skipped,
            "refs": refs[:5],
        }
    }
