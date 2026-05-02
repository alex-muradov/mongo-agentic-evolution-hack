"""Spot-check the hydrated case_studies — counts, samples, vector search, validator re-pass."""
import asyncio
import os
import sys
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from dotenv import load_dotenv

load_dotenv(HERE.parent / ".env")

from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI

from domain.case_study import CaseStudyInternal
from integrations.embeddings import embed_text, make_client as make_voyage_client


async def main():
    mongo = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    coll = mongo[os.environ.get("MONGODB_DB", "agentic_evolution")]["case_studies"]

    print("=" * 60)
    print("AGGREGATES")
    print("=" * 60)
    total = await coll.count_documents({})
    no_emb = await coll.count_documents({"embedding": None})
    print(f"total: {total}    without embedding: {no_emb}")

    print("\nby page_id:")
    async for d in coll.aggregate([{"$group": {"_id": "$page_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]):
        print(f"  {d['_id']:30s}  {d['n']}")

    print("\nby service_type:")
    async for d in coll.aggregate([{"$group": {"_id": "$service_type", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]):
        print(f"  {d['_id']:25s}  {d['n']}")

    print("\nby borough (east-london only):")
    async for d in coll.aggregate([
        {"$match": {"page_id": "areas/east-london"}},
        {"$group": {"_id": "$borough", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"  {d['_id']:25s}  {d['n']}")

    print("\nby price_band:")
    async for d in coll.aggregate([{"$group": {"_id": "$price_band", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]):
        print(f"  {str(d['_id']):15s}  {d['n']}")

    # word count distribution
    word_counts = []
    async for d in coll.find({}, {"summary": 1}):
        word_counts.append(len(d["summary"].split()))
    print(f"\nsummary word count: min={min(word_counts)}, median={median(word_counts):.0f}, "
          f"max={max(word_counts)}, n={len(word_counts)}")
    out_of_range = sum(1 for w in word_counts if w < 40 or w > 80)
    print(f"  out of [40,80]: {out_of_range}  (should be 0; all passed validation on insert)")

    # validator re-check on a sample
    print("\nvalidator re-pass on 50 docs (sanity):")
    bad = 0
    async for d in coll.find({}).limit(50):
        try:
            CaseStudyInternal.model_validate(d)
        except Exception as e:
            bad += 1
            print(f"  re-validate fail {d['_id']}: {str(e)[:120]}")
    print(f"  failed: {bad}/50")

    print("\n" + "=" * 60)
    print("EAST LONDON SAMPLES (3 random)")
    print("=" * 60)
    async for d in coll.aggregate([
        {"$match": {"page_id": "areas/east-london"}},
        {"$sample": {"size": 3}},
    ]):
        print(f"\n--- {d['_id']}")
        print(f"  service_type: {d['service_type']}    tag: {d['service_tag']}")
        print(f"  title:        {d['title']}")
        print(f"  street:       {d['street']}    borough: {d['borough']} {d['postcode_outward']}")
        print(f"  outcome:      {d['outcome']}    price_band: {d.get('price_band')}    duration: {d.get('duration_minutes')} min")
        print(f"  problem:      {d['problem']}")
        print(f"  solution:     {d['solution']}")
        print(f"  summary ({len(d['summary'].split())} words):")
        print(f"    {d['summary']}")

    print("\n" + "=" * 60)
    print("VECTOR SEARCH SMOKE")
    print("=" * 60)
    voyage = make_voyage_client(os.environ["MONGODB_AI_KEY"])

    for query in [
        "emergency lockout euro cylinder forced entry",
        "new tenant moved in wanted everything changed",
        "uPVC patio door multipoint not engaging",
        "broken key snapped in cylinder",
    ]:
        print(f"\nquery: {query!r}")
        qv = await embed_text(voyage, query)
        cur = coll.aggregate([
            {"$vectorSearch": {
                "index": "case_studies_vec",
                "queryVector": qv,
                "path": "embedding",
                "numCandidates": 100,
                "limit": 4,
                "filter": {"schema_version": "v1"},
            }},
            {"$project": {"_id": 1, "service_type": 1, "borough": 1, "title": 1, "score": {"$meta": "vectorSearchScore"}}},
        ])
        async for d in cur:
            print(f"  {d['score']:.3f}  {d['service_type']:18s}  {d['borough']:18s}  {d['title']}")

    mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
