"""pages collection — typed registry of pSEO pages we run experiments on."""
from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class PageSpec(BaseModel):
    """One Area Hub on Gabik's Next.js site that we target with case_studies experiments."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., alias="_id")  # canonical reference, e.g. "areas/east-london"
    slug: str  # "east-london" — used in revalidate tag `case-studies:{slug}`
    area_label: str  # "East London"
    boroughs: List[str]  # ["Hackney", "Tower Hamlets", "Newham", ...] — case_study filter set
    next_revalidate_url: str  # full URL to Gabik's `/api/revalidate`
    posthog_flag_key: str  # "case_studies_v1"

    active: bool = True
    created_at: datetime
    updated_at: datetime
