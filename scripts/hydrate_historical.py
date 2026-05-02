"""Hydrate historical Firebase jobs into case_studies via LLM rewriter + Voyage embed."""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from dotenv import load_dotenv

load_dotenv(HERE.parent / ".env")

from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI
from pydantic import ValidationError

from domain.case_study import CaseStudyInternal
from domain.enums import CaseStudySource, PriceBand
from integrations.embeddings import embed_text, make_client as make_voyage_client


JSON_PATH = "/Users/alexm/Desktop/firebase-jobs-raw-historical.json"
LLM_MODEL = "gpt-4o-mini"
MAX_CONCURRENT = 8

LONDON_POSTCODE_TO_BOROUGH = {
    "E1": "Tower Hamlets", "E2": "Tower Hamlets", "E3": "Tower Hamlets", "E14": "Tower Hamlets",
    "E5": "Hackney", "E8": "Hackney", "E9": "Hackney", "N16": "Hackney",
    "E6": "Newham", "E7": "Newham", "E12": "Newham", "E13": "Newham",
    "E15": "Newham", "E16": "Newham", "E20": "Newham",
    "E4": "Waltham Forest", "E10": "Waltham Forest", "E11": "Waltham Forest", "E17": "Waltham Forest",
    "E18": "Redbridge",
    "N1": "Islington", "N5": "Islington", "N7": "Islington", "N19": "Islington",
    "N4": "Haringey", "N8": "Haringey", "N10": "Haringey", "N15": "Haringey", "N17": "Haringey", "N22": "Haringey",
    "N2": "Barnet", "N3": "Barnet", "N11": "Barnet", "N12": "Barnet", "N20": "Barnet",
    "N9": "Enfield", "N13": "Enfield", "N14": "Enfield", "N18": "Enfield", "N21": "Enfield",
    "N6": "Camden", "WC1": "Camden",
    "EC1": "Islington",
}

EAST_LONDON_BOROUGHS = {"Hackney", "Tower Hamlets", "Newham", "Waltham Forest", "Redbridge"}

POSTCODE_OUTWARD_RE = re.compile(r"^E[A-Z0-9]{1,3}$|^[A-Z]{1,2}[0-9]{1,2}$")


def parse_postcode_outward(raw: str) -> Optional[str]:
    if not raw:
        return None
    parts = raw.strip().upper().split()
    if not parts:
        return None
    return parts[0] if POSTCODE_OUTWARD_RE.match(parts[0]) else None


def borough_and_page(outward: Optional[str]) -> tuple[Optional[str], str]:
    if outward and outward in LONDON_POSTCODE_TO_BOROUGH:
        b = LONDON_POSTCODE_TO_BOROUGH[outward]
        return b, "areas/east-london" if b in EAST_LONDON_BOROUGHS else "areas/london-other"
    return None, "areas/uk-other"


def total_price(job: dict) -> float:
    base = float(job.get("price") or 0)
    vat = float(job.get("vat") or 0)
    services = job.get("services") or []
    services_total = sum(
        float(s.get("price") or 0) * int(s.get("quantity") or 1) for s in services
    )
    return max(base + vat, services_total + vat, base + services_total)


def price_to_band(total: float) -> Optional[PriceBand]:
    if total <= 0:
        return None
    if total <= 80:
        return PriceBand.UP_TO_80
    if total <= 150:
        return PriceBand.BAND_80_150
    if total <= 300:
        return PriceBand.BAND_150_300
    return PriceBand.OVER_300


SCHEMA = {
    "name": "case_study_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "service_type": {
                "type": "string",
                "enum": ["emergency_lockout", "lock_change", "safe_opening",
                         "key_extraction", "upvc_repair", "security_audit"],
            },
            "service_tag": {"type": "string"},
            "problem": {"type": "string"},
            "solution": {"type": "string"},
            "outcome": {"type": "string", "enum": ["success", "partial", "referred"]},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "street": {"type": "string"},
        },
        "required": ["service_type", "service_tag", "problem", "solution",
                     "outcome", "title", "summary", "street"],
    },
}


SYSTEM_PROMPT = """You convert raw locksmith job records into anonymised case studies for a public locksmith website.

CRITICAL HARD RULES for the `summary` field (frontend rejects on violation):
1. MUST be 50-75 words. Count carefully. Frontend hard-rejects below 40 or above 80, so leave safety margin. If your draft is under 50, expand the technical detail; if over 75, trim.
2. First-person plural ("we"), past tense.
3. NO £/$/EUR symbols. NO "+ vat". NO numbers near currency words ("150 quid", "200 pounds", "150 GBP" all forbidden). The number "5-lever" is fine; "150 quid" is forbidden.
4. NO @ symbol. NO UK mobile patterns (07XXXXXXXXX).
5. NO house numbers, NO full postcodes — only the area name (e.g. "in Hackney") is fine.
6. NO customer names; describe role only ("a tenant", "a property manager", "a landlord").
7. NO marketing superlatives ("best", "amazing", "top-rated").

Other fields:
- `title`: ≤70 chars, sentence case, no brand mentions, no prices.
- `service_tag`: ≤24 chars, free-form badge label like "Move-in security" or "Emergency lockout".
- `service_type`: classify carefully. emergency_lockout = customer locked out / no entry; lock_change = replace cylinder/deadbolt/lock; safe_opening = safe-related; key_extraction = broken/snapped key; upvc_repair = uPVC door / multipoint / patio / handle issues; security_audit = consultation/survey, no installation. If genuinely ambiguous, default to lock_change.
- `outcome`: success | partial | referred.
- `street`: street name only — no house number, no leading digit, no flat reference. If unclear, use a plausible inner-London street name from the same area.
- `problem`: 1-2 sentences describing customer situation (anonymised).
- `solution`: 1-2 sentences on what we did technically.

If the source notes are thin, embellish using common locksmith narratives — but never invent prices, addresses, names, or phone numbers."""


def build_user_prompt(job: dict, borough: Optional[str], outward: Optional[str]) -> str:
    descr = (job.get("description") or "").strip()
    services = job.get("services") or []
    services_text = "\n".join(
        f"- [{s.get('type','?')}] {s.get('description','').strip()} (qty {s.get('quantity',1)})"
        for s in services
    )
    raw_address = (job.get("address") or "").replace("\n", ", ")
    return f"""Borough: {borough or 'unknown'}, postcode area: {outward or 'unknown'}

Job notes (may contain admin chatter or PII — extract semantic content only, do NOT echo into summary):
{descr or '(none)'}

Services performed:
{services_text or '(none)'}

Raw address (for street extraction only — do NOT echo into summary):
{raw_address}

Produce the JSON case study now. Respect ALL hard rules."""


async def call_llm(client: AsyncOpenAI, user: str) -> Optional[dict]:
    try:
        r = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_schema", "json_schema": SCHEMA},
            temperature=0.3,
            max_tokens=1500,
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print(f"  llm error: {type(e).__name__}: {str(e)[:120]}")
        return None


async def process_job(
    job: dict,
    coll,
    voyage_client: AsyncOpenAI,
    openai_client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    stats: dict,
    dry_run: bool,
):
    async with sem:
        if not job.get("completed") or job.get("removed"):
            stats["skipped_filter"] += 1
            return
        outward = parse_postcode_outward(job.get("postcode") or "")
        if not outward:
            stats["skipped_no_postcode"] += 1
            return
        borough, page_id = borough_and_page(outward)

        completed_at_ms = job.get("timefinish") or job.get("timestart")
        if not completed_at_ms:
            stats["skipped_no_time"] += 1
            return

        band = price_to_band(total_price(job))

        completed_at = datetime.fromtimestamp(completed_at_ms / 1000, tz=timezone.utc)
        duration_min = None
        if job.get("timestart") and job.get("timefinish"):
            duration_min = max(0, int((job["timefinish"] - job["timestart"]) / 60000))

        doc_id = f"cs_historical_firebase_{job['id']}"
        now = datetime.now(timezone.utc)
        base_prompt = build_user_prompt(job, borough, outward)

        def assemble(out: dict) -> CaseStudyInternal:
            return CaseStudyInternal.model_validate({
                "_id": doc_id,
                "schema_version": "v1",
                "source": CaseStudySource.HISTORICAL_FIREBASE.value,
                "source_job_id": job["id"],
                "page_id": page_id,
                "borough": borough or "Unknown",
                "postcode_outward": outward,
                "service_type": out["service_type"],
                "service_tag": out["service_tag"][:24],
                "completed_at": completed_at,
                "duration_minutes": duration_min,
                "price_band": band.value if band else None,
                "problem": out["problem"],
                "solution": out["solution"],
                "outcome": out["outcome"],
                "title": out["title"][:70],
                "summary": out["summary"],
                "street": out["street"],
                "pii_strip_version": "v1",
                "created_at": now,
                "updated_at": now,
            })

        internal: Optional[CaseStudyInternal] = None
        prompt = base_prompt
        for attempt in range(2):
            llm_out = await call_llm(openai_client, prompt)
            if not llm_out:
                stats["llm_failures"] += 1
                return
            try:
                internal = assemble(llm_out)
                break
            except ValidationError as e:
                if attempt == 1:
                    stats["validation_failures"] += 1
                    err = e.errors()[0] if e.errors() else {}
                    print(f"  validation fail {job['id']} ({err.get('loc')}): {err.get('msg','')[:120]}")
                    return
                feedback = "; ".join(
                    f"{err['loc']}: {err['msg']}" for err in e.errors()[:3]
                )
                prompt = (
                    base_prompt
                    + f"\n\nYour PREVIOUS attempt FAILED validation with: {feedback}\n"
                    + "Fix precisely and retry. The summary word count is the most common failure — count words carefully."
                )
        if internal is None:
            return

        doc = internal.model_dump(by_alias=True)

        try:
            doc["embedding"] = await embed_text(voyage_client, internal.summary)
        except Exception as e:
            doc["embedding"] = None
            stats["embed_failures"] += 1

        if dry_run:
            stats["upserted"] += 1
            print(f"  [dry] would upsert {doc_id}: {internal.service_type.value} | {internal.title[:50]}")
            return

        try:
            await coll.replace_one({"_id": doc_id}, doc, upsert=True)
            stats["upserted"] += 1
            if stats["upserted"] % 25 == 0:
                print(f"  ... {stats['upserted']} upserted")
        except Exception as e:
            stats["mongo_failures"] += 1
            print(f"  mongo error {doc_id}: {str(e)[:120]}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="process only N jobs")
    parser.add_argument("--dry-run", action="store_true", help="don't write to Mongo")
    args = parser.parse_args()

    raw = json.loads(Path(JSON_PATH).read_text())
    jobs = raw.get("jobs", [])
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"Processing {len(jobs)} jobs (model={LLM_MODEL}, concurrency={MAX_CONCURRENT}, dry={args.dry_run})")

    mongo = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    coll = mongo[os.environ.get("MONGODB_DB", "agentic_evolution")]["case_studies"]
    voyage = make_voyage_client(os.environ["MONGODB_AI_KEY"])
    openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    stats = {
        "skipped_filter": 0,
        "skipped_no_postcode": 0,
        "skipped_no_time": 0,
        "llm_failures": 0,
        "validation_failures": 0,
        "embed_failures": 0,
        "mongo_failures": 0,
        "upserted": 0,
    }

    await asyncio.gather(*[
        process_job(j, coll, voyage, openai_client, sem, stats, args.dry_run) for j in jobs
    ])

    print("\nDone. Stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
