"""Backfill borough on case_studies where it landed as 'Unknown' — uses postcodes.io.

Also cleans up smoke-test docs left from T2.A development.
"""
import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from dotenv import load_dotenv

load_dotenv(HERE.parent / ".env")

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

EAST_LONDON_BOROUGHS = {"Hackney", "Tower Hamlets", "Newham", "Waltham Forest", "Redbridge"}

# Smoke-test docs created during T2.A development (curl-based, not real RS Lock data)
SMOKE_DOC_IDS = [
    "cs_live_debrief_rsl_2025_01_17_e8_0042",
    "cs_live_debrief_rsl_2026_05_02_e8_smoke",
    "cs_live_debrief_rsl_2026_05_02_e8_smoke2",
    "cs_live_debrief_rsl_2026_05_02_via_tunnel",
]


async def lookup_outcode(client: httpx.AsyncClient, outcode: str) -> tuple[str | None, str]:
    """Return (borough, page_id) for a UK outcode via postcodes.io. Falls back to (None, 'areas/uk-other')."""
    try:
        r = await client.get(f"https://api.postcodes.io/outcodes/{outcode}", timeout=8.0)
    except Exception:
        return None, "areas/uk-other"
    if r.status_code != 200:
        return None, "areas/uk-other"
    res = r.json().get("result") or {}
    districts = res.get("admin_district") or []
    if not districts:
        return None, "areas/uk-other"
    # postcodes.io returns multiple districts when an outcode straddles boundaries.
    # Pick East-London-priority match if any, else first.
    pick = next((d for d in districts if d in EAST_LONDON_BOROUGHS), districts[0])
    page_id = "areas/east-london" if pick in EAST_LONDON_BOROUGHS else "areas/london-other"
    # Heuristic: outcodes with non-London-area-style admin_district go to uk-other.
    # postcodes.io gives e.g. "Reading", "Milton Keynes" for those — they don't show in our London set.
    london_districts = {
        "Westminster", "City of London", "Camden", "Islington", "Hackney", "Tower Hamlets",
        "Newham", "Waltham Forest", "Redbridge", "Haringey", "Enfield", "Barnet", "Brent",
        "Harrow", "Hillingdon", "Ealing", "Hounslow", "Richmond upon Thames", "Kingston upon Thames",
        "Merton", "Wandsworth", "Lambeth", "Southwark", "Lewisham", "Greenwich", "Bexley",
        "Bromley", "Croydon", "Sutton", "Kensington and Chelsea", "Hammersmith and Fulham",
    }
    if pick not in london_districts:
        page_id = "areas/uk-other"
    return pick, page_id


async def main():
    mongo = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    coll = mongo[os.environ.get("MONGODB_DB", "agentic_evolution")]["case_studies"]

    # 1. Clean smoke docs
    print("=== Cleaning smoke-test docs")
    res = await coll.delete_many({"_id": {"$in": SMOKE_DOC_IDS}})
    print(f"  deleted: {res.deleted_count}")

    # 2. Collect distinct outcodes that need fixing
    print("\n=== Fetching distinct outcodes with borough=Unknown")
    outcodes: list[str] = []
    async for d in coll.aggregate([
        {"$match": {"borough": "Unknown"}},
        {"$group": {"_id": "$postcode_outward"}},
    ]):
        outcodes.append(d["_id"])
    print(f"  {len(outcodes)} distinct outcodes")

    # 3. Resolve via postcodes.io with concurrency
    print("\n=== Resolving via postcodes.io (concurrency 10)")
    sem = asyncio.Semaphore(10)
    resolved: dict[str, tuple[str | None, str]] = {}

    async with httpx.AsyncClient() as http:
        async def resolve(oc: str):
            async with sem:
                resolved[oc] = await lookup_outcode(http, oc)

        await asyncio.gather(*[resolve(oc) for oc in outcodes])

    hits = sum(1 for (b, _) in resolved.values() if b)
    print(f"  resolved {hits}/{len(outcodes)}")

    # 4. Bulk update Mongo
    print("\n=== Updating docs in Mongo")
    n_updated = 0
    n_repaged = 0
    for oc, (borough, page_id) in resolved.items():
        if not borough:
            continue
        # Update borough; only update page_id if it migrates AND the doc currently has a non-target page_id
        upd = {"borough": borough}
        # also rectify page_id if our heuristic disagrees with what hydration assigned
        r = await coll.update_many(
            {"postcode_outward": oc, "borough": "Unknown"},
            {"$set": {"borough": borough, "page_id": page_id, "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)}},
        )
        n_updated += r.modified_count
        if r.modified_count:
            print(f"  {oc:8s} -> {borough:25s} ({r.modified_count}, page_id={page_id})")

    # 5. Report leftovers
    leftover = await coll.count_documents({"borough": "Unknown"})
    print(f"\n=== Done. {n_updated} docs updated. Still Unknown: {leftover}")

    # 6. Distribution after fix
    print("\nFinal distribution by page_id:")
    async for d in coll.aggregate([{"$group": {"_id": "$page_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]):
        print(f"  {d['_id']:25s} {d['n']}")

    print("\nFinal east-london by borough:")
    async for d in coll.aggregate([
        {"$match": {"page_id": "areas/east-london"}},
        {"$group": {"_id": "$borough", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"  {d['_id']:25s} {d['n']}")

    mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
